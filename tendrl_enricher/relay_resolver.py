"""
Relay resolver for nostr-feeds enricher

Resolves relay URLs based on relay_mode (general/inbox/outbox) and NIP-65.
"""

import subprocess
import json
import sys
from typing import Dict, List, Any, Optional
from dataclasses import dataclass

from .config import Config
from .db import DatabaseManager, get_inbox_relays, get_outbox_relays, store_relay_list


@dataclass
class RelayResolution:
    """
    Result of relay resolution.

    Attributes:
        strategy: 'broadcast' (single relay list) or 'grouped' (per-author groups)
        relays: For broadcast: List[str], for grouped: Dict[relay, List[authors]]
    """
    strategy: str  # 'broadcast' | 'grouped'
    relays: Any  # List[str] or Dict[str, List[str]]


class RelayResolver:
    """
    Resolve relay URLs based on relay_mode and NIP-65.

    Handles three relay modes:
    - general: User's configured relays
    - inbox: User's read relays (NIP-65 kind 10002)
    - outbox: Authors' write relays (NIP-65 kind 10002)
    """

    def __init__(self, config: Config, db_manager: DatabaseManager):
        """
        Initialize relay resolver.

        Args:
            config: Configuration object
            db_manager: Database manager for profiles.db access
        """
        self.config = config
        self.db_manager = db_manager

    def resolve(
        self,
        relay_mode: str,
        feed_config: Optional[Dict] = None,
        user_pubkey: Optional[str] = None,
        authors: Optional[List[str]] = None
    ) -> RelayResolution:
        """
        Resolve relay URLs for a feed.

        Args:
            relay_mode: 'general' | 'inbox' | 'outbox'
            feed_config: Feed configuration dict
            user_pubkey: User's pubkey (for inbox mode)
            authors: List of author pubkeys (for outbox mode)

        Returns:
            RelayResolution with strategy and relays

        Examples:
            >>> resolver = RelayResolver(config, db_manager)
            >>> res = resolver.resolve('general')
            >>> res.strategy
            'broadcast'
            >>> res.relays
            ['wss://relay.damus.io', 'wss://nos.lol']
        """
        if relay_mode == 'general':
            return self._resolve_general()
        elif relay_mode == 'inbox':
            return self._resolve_inbox(user_pubkey)
        elif relay_mode == 'outbox':
            return self._resolve_outbox(authors or [])
        else:
            raise ValueError(f"Unknown relay_mode: {relay_mode}")

    def _resolve_general(self) -> RelayResolution:
        """
        User's configured relays from config.toml.

        Returns:
            Broadcast strategy with user's relays
        """
        relays = self._get_user_relays()
        return RelayResolution(strategy='broadcast', relays=relays)

    def _resolve_inbox(self, user_pubkey: Optional[str]) -> RelayResolution:
        """
        User's read relays from NIP-65 kind 10002.

        Args:
            user_pubkey: User's hex pubkey

        Returns:
            Broadcast strategy with inbox relays
        """
        if not user_pubkey:
            # Try to get default user from config
            default_npub = self.config.global_config.get('default_npub')
            if default_npub:
                from .config import npub_to_hex
                user_pubkey = npub_to_hex(default_npub)
            else:
                # Fallback to general
                return self._resolve_general()

        # Get profiles.db connection
        profiles_db = self.db_manager.get_connection(
            self.config.global_config.get('profile_cache_db', 'profiles.db')
        )

        # Get inbox relays
        inbox_relays = get_inbox_relays(profiles_db, user_pubkey)

        if not inbox_relays:
            # Fallback: fetch kind 10002 if not cached
            print(f"Fetching kind 10002 for {user_pubkey[:8]}...", file=sys.stderr)
            relay_list = self._fetch_relay_list(user_pubkey)
            if relay_list:
                # Store and retry
                store_relay_list(profiles_db, relay_list)
                inbox_relays = get_inbox_relays(profiles_db, user_pubkey)

        if not inbox_relays:
            # Final fallback to general
            print(f"No inbox relays found for {user_pubkey[:8]}, using general", file=sys.stderr)
            return self._resolve_general()

        return RelayResolution(strategy='broadcast', relays=inbox_relays)

    def _resolve_outbox(self, authors: List[str]) -> RelayResolution:
        """
        Authors' write relays from NIP-65 kind 10002.

        Groups authors by shared outbox relays to minimize connections.

        Args:
            authors: List of author hex pubkeys

        Returns:
            Grouped strategy with relay->authors mapping
        """
        if not authors:
            # No authors, fallback to general
            return self._resolve_general()

        # Get profiles.db connection
        profiles_db = self.db_manager.get_connection(
            self.config.global_config.get('profile_cache_db', 'profiles.db')
        )

        # Group authors by relay
        relay_to_authors = self._group_authors_by_relay(authors, profiles_db)

        if not relay_to_authors:
            # No outbox relays found, fallback to general
            print(f"No outbox relays found for {len(authors)} authors, using general", file=sys.stderr)
            return self._resolve_general()

        return RelayResolution(strategy='grouped', relays=relay_to_authors)

    def _group_authors_by_relay(
        self,
        authors: List[str],
        profiles_db
    ) -> Dict[str, List[str]]:
        """
        Group authors by their outbox relays.

        Args:
            authors: List of author pubkeys
            profiles_db: SQLite connection to profiles.db

        Returns:
            Dictionary mapping relay URLs to lists of authors
            Example: {'wss://relay1.com': ['alice', 'bob'], 'wss://relay2.com': ['carol']}
        """
        relay_map = {}

        for author in authors:
            # Get outbox relays for this author
            outbox_relays = get_outbox_relays(profiles_db, author)

            if not outbox_relays:
                # Try to fetch kind 10002
                relay_list = self._fetch_relay_list(author)
                if relay_list:
                    store_relay_list(profiles_db, relay_list)
                    outbox_relays = get_outbox_relays(profiles_db, author)

            if not outbox_relays:
                # Fallback to user's configured relays
                outbox_relays = self._get_user_relays()

            # Add author to each of their relays
            for relay in outbox_relays:
                relay_map.setdefault(relay, []).append(author)

        return relay_map

    def _fetch_relay_list(self, pubkey: str) -> Optional[Dict[str, Any]]:
        """
        Fetch kind 10002 relay list from relays using nak.

        Args:
            pubkey: Hex pubkey

        Returns:
            Kind 10002 event dict or None if not found
        """
        user_relays = self._get_user_relays()

        cmd = ['nak', 'req', '-k', '10002', '-a', pubkey, '-l', '1'] + user_relays

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10
            )

            for line in result.stdout.strip().split('\n'):
                if line:
                    try:
                        event = json.loads(line)
                        if event.get('kind') == 10002 and event.get('pubkey') == pubkey:
                            return event
                    except json.JSONDecodeError:
                        continue

            return None

        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None

    def _get_user_relays(self) -> List[str]:
        """
        Get user's relay list from config.

        Returns:
            List of relay URLs from config
        """
        default_npub = self.config.global_config.get('default_npub')
        if not default_npub:
            # Use hardcoded defaults
            return ['wss://relay.damus.io', 'wss://relay.nostr.band']

        user_config = self.config.users.get(default_npub, {})
        relays = user_config.get('relays', [])

        if not relays:
            return ['wss://relay.damus.io', 'wss://relay.nostr.band']

        return relays
