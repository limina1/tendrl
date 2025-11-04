"""
Follow list management for nostr-feeds enricher

Fetch and cache follow lists (kind 3) for users.
"""

import sqlite3
import json
import time
from typing import Dict, List, Optional, Set

from .db import get_events_by_author
from .config import npub_to_hex


class FollowsCache:
    """
    Cache follow lists for users.

    Follow lists are kind 3 events containing p tags for each followed pubkey.
    This cache stores them in memory and can refresh from the database.
    """

    def __init__(self, feed_db_conn: sqlite3.Connection):
        """
        Initialize follows cache.

        Args:
            feed_db_conn: SQLite connection to feed database
        """
        self.feed_db = feed_db_conn

        # In-memory cache: npub/hex -> set of followed pubkeys
        self._follows_cache: Dict[str, Set[str]] = {}

        # Timestamp of last fetch per user
        self._last_fetch: Dict[str, float] = {}

        # Cache TTL (default: 1 hour)
        self.cache_ttl = 3600

    def get_follows(self, npub_or_hex: str, force_refresh: bool = False) -> Set[str]:
        """
        Get list of pubkeys that a user follows.

        Args:
            npub_or_hex: User's npub or hex pubkey
            force_refresh: Force refresh from database

        Returns:
            Set of followed pubkeys (hex format)

        Examples:
            >>> cache = FollowsCache(conn)
            >>> follows = cache.get_follows('npub1abc...')
            >>> 'deadbeef...' in follows
            True
        """
        # Convert npub to hex if needed
        pubkey = self._normalize_pubkey(npub_or_hex)

        # Check cache
        if not force_refresh and pubkey in self._follows_cache:
            last_fetch = self._last_fetch.get(pubkey, 0)
            if time.time() - last_fetch < self.cache_ttl:
                return self._follows_cache[pubkey]

        # Fetch from database
        follows = self._fetch_follows_from_db(pubkey)
        self._follows_cache[pubkey] = follows
        self._last_fetch[pubkey] = time.time()

        return follows

    def _fetch_follows_from_db(self, pubkey: str) -> Set[str]:
        """
        Fetch follow list from database (kind 3 event).

        Args:
            pubkey: User's hex pubkey

        Returns:
            Set of followed pubkeys
        """
        # Get most recent kind 3 event
        events = get_events_by_author(self.feed_db, pubkey, kinds=[3], limit=1)

        if not events:
            return set()

        event = events[0]
        tags = event.get('tags', [])

        # Extract p tags (followed pubkeys)
        follows = set()
        for tag in tags:
            if isinstance(tag, list) and len(tag) >= 2 and tag[0] == 'p':
                followed_pubkey = tag[1]
                follows.add(followed_pubkey)

        return follows

    def filter_by_follows(
        self,
        events: List[Dict],
        npub_or_hex: str,
        include_user: bool = True
    ) -> List[Dict]:
        """
        Filter events to only those from followed users.

        Args:
            events: List of events to filter
            npub_or_hex: User whose follow list to use
            include_user: Also include events from the user themselves

        Returns:
            Filtered list of events

        Examples:
            >>> events = get_events(conn, limit=100)
            >>> filtered = cache.filter_by_follows(events, 'npub1abc...')
            >>> len(filtered) < len(events)
            True
        """
        pubkey = self._normalize_pubkey(npub_or_hex)
        follows = self.get_follows(npub_or_hex)

        # Create allowed set
        allowed_pubkeys = follows.copy()
        if include_user:
            allowed_pubkeys.add(pubkey)

        # Filter events
        return [
            event for event in events
            if event.get('pubkey') in allowed_pubkeys
        ]

    def is_following(self, user_npub: str, target_npub: str) -> bool:
        """
        Check if user follows target.

        Args:
            user_npub: User's npub or hex
            target_npub: Target's npub or hex

        Returns:
            True if user follows target

        Examples:
            >>> cache.is_following('npub1abc...', 'npub1def...')
            True
        """
        user_pubkey = self._normalize_pubkey(user_npub)
        target_pubkey = self._normalize_pubkey(target_npub)

        follows = self.get_follows(user_pubkey)
        return target_pubkey in follows

    def get_follow_count(self, npub_or_hex: str) -> int:
        """
        Get number of users that npub follows.

        Args:
            npub_or_hex: User's npub or hex

        Returns:
            Number of follows

        Examples:
            >>> cache.get_follow_count('npub1abc...')
            250
        """
        follows = self.get_follows(npub_or_hex)
        return len(follows)

    def prefetch_follows(self, npubs: List[str]) -> None:
        """
        Prefetch follow lists for multiple users.

        Args:
            npubs: List of npubs or hex pubkeys to prefetch

        Examples:
            >>> cache.prefetch_follows(['npub1abc...', 'npub1def...'])
        """
        for npub in npubs:
            self.get_follows(npub)

    def clear_cache(self) -> None:
        """Clear in-memory cache."""
        self._follows_cache.clear()
        self._last_fetch.clear()

    def _normalize_pubkey(self, npub_or_hex: str) -> str:
        """
        Normalize npub or hex to hex format.

        Uses nak CLI to decode npub if needed.

        Args:
            npub_or_hex: npub or hex pubkey

        Returns:
            Hex pubkey

        Raises:
            ValueError: If invalid format
            RuntimeError: If nak decode fails
        """
        return npub_to_hex(npub_or_hex)

    def __len__(self) -> int:
        """Return number of cached follow lists."""
        return len(self._follows_cache)

    def __contains__(self, npub_or_hex: str) -> bool:
        """Check if user's follows are cached."""
        try:
            pubkey = self._normalize_pubkey(npub_or_hex)
            return pubkey in self._follows_cache
        except (ValueError, RuntimeError):
            return False
