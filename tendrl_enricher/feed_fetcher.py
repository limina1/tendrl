"""
Feed fetcher for nostr-feeds enricher

Generates nak commands to populate feeds from relays and manages periodic updates.
"""

import subprocess
import json
import time
import sqlite3
from typing import List, Dict, Any, Optional
from pathlib import Path

from .config import Config, npub_to_hex
from .db import DatabaseManager, store_event, store_profile
from .connectivity import is_online


class FeedFetcher:
    """
    Manages fetching events from relays to populate feeds.

    Handles:
    - Generating nak command patterns per feed type
    - Periodic updates with --since for incremental fetching
    - Tracking last fetch timestamps
    - Storing events to per-feed databases
    """

    def __init__(self, config: Config, db_manager: DatabaseManager):
        """
        Initialize feed fetcher.

        Args:
            config: Configuration object
            db_manager: Database manager for storing events
        """
        self.config = config
        self.db_manager = db_manager

        # Track last fetch timestamp per feed
        self._last_fetch: Dict[str, int] = {}

    def fetch_feed(
        self,
        feed_name: str,
        limit: int = 100,
        since: Optional[int] = None,
        until: Optional[int] = None,
        relays: Optional[List[str]] = None
    ) -> int:
        """
        Fetch events for a feed and store to its database.

        Supports bidirectional fetching:
        - Upward (new events): Use `since` to fetch events AFTER timestamp
        - Downward (backfill): Use `until` to fetch events BEFORE timestamp

        Args:
            feed_name: Feed name from config (e.g., 'timeline')
            limit: Maximum events to fetch
            since: Only fetch events after this timestamp (upward growth)
            until: Only fetch events before this timestamp (historical backfill)
            relays: Relay URLs (defaults to user's relays from config)

        Returns:
            Number of new events fetched

        Examples:
            >>> fetcher = FeedFetcher(config, db_manager)
            >>> # Upward: fetch new events
            >>> count = fetcher.fetch_feed('timeline', limit=50, since=1729123456)
            >>> # Downward: fetch older events
            >>> count = fetcher.fetch_feed('timeline', limit=50, until=1729000000)
        """
        # Check internet connectivity first
        online, status_msg = is_online()
        if not online:
            print(f"⚠️  Skipping fetch - {status_msg}", flush=True)
            return 0

        # Get feed config
        feed_config = self.config.feeds.get(feed_name)
        if not feed_config:
            raise ValueError(f"Unknown feed: {feed_name}")

        # Use since parameter or last fetch time (for upward growth)
        if since is None and until is None:
            since = self._last_fetch.get(feed_name, 0)

        # Generate nak command based on feed type
        cmd = self._generate_fetch_command(feed_name, feed_config, limit, since, until, relays)

        # Execute command and parse events
        events = self._execute_fetch_command(cmd)

        # Store events to feed database (thread-safe with batched commits)
        db_file = feed_config.get('db_file', f"{feed_name}.db")

        stored_count = 0
        with self.db_manager.write_transaction(db_file) as feed_db:
            for event in events:
                if store_event(feed_db, event, auto_commit=False):
                    stored_count += 1
            # Commit happens automatically when exiting context

        # Fetch missing profiles for all authors in fetched events
        if events:
            self._fetch_missing_profiles(events, relays)

        # Update last fetch timestamp
        if events:
            latest_timestamp = max(e.get('created_at', 0) for e in events)
            self._last_fetch[feed_name] = latest_timestamp

        return stored_count

    def _fetch_missing_profiles(
        self,
        events: List[Dict[str, Any]],
        relays: Optional[List[str]] = None,
        batch_size: int = 25,
        use_fallback_relays: bool = True
    ) -> int:
        """
        Fetch missing profiles for all authors in events.

        After fetching feed events (notes, reactions, zaps, etc.), we need to
        fetch kind 0 profiles for all authors mentioned. This ensures the
        enricher can display author names, pictures, etc.

        Uses two-tier relay strategy:
        1. First try user's configured relays
        2. If profiles still missing, try well-known public relays

        Args:
            events: List of events to extract pubkeys from
            relays: Relay URLs (defaults to user relays)
            batch_size: Number of profiles to fetch per batch
            use_fallback_relays: If True, search well-known relays for missing profiles

        Returns:
            Number of profiles fetched

        Examples:
            >>> events = [{'pubkey': 'abc123', ...}, {'pubkey': 'def456', ...}]
            >>> count = fetcher._fetch_missing_profiles(events)
            >>> print(f"Fetched {count} profiles")
        """
        # Extract all unique pubkeys from events
        pubkeys = set()
        for event in events:
            # Author pubkey
            pubkey = event.get('pubkey')
            if pubkey:
                pubkeys.add(pubkey)

            # Mentioned pubkeys in p tags
            for tag in event.get('tags', []):
                if isinstance(tag, list) and len(tag) >= 2 and tag[0] == 'p':
                    pubkeys.add(tag[1])

        if not pubkeys:
            return 0

        # Check which profiles are missing from profiles.db
        profiles_db = self.db_manager.get_connection('profiles.db')
        cursor = profiles_db.cursor()

        missing_pubkeys = []
        for pubkey in pubkeys:
            cursor.execute('SELECT 1 FROM profiles WHERE pubkey = ?', (pubkey,))
            if not cursor.fetchone():
                missing_pubkeys.append(pubkey)

        if not missing_pubkeys:
            return 0

        # Fetch missing profiles in batches from user's relays
        if relays is None:
            relays = self._get_user_relays()

        fetched_count = 0
        still_missing = set(missing_pubkeys)

        for i in range(0, len(missing_pubkeys), batch_size):
            batch = missing_pubkeys[i:i + batch_size]

            # Build nak command to fetch profiles
            cmd = ['nak', 'req', '-k', '0']
            for pubkey in batch:
                cmd.extend(['-a', pubkey])
            cmd.extend(['-l', '1'])  # Only get latest profile per author
            cmd.extend(relays)

            # Execute and parse
            try:
                profile_events = self._execute_fetch_command(cmd, timeout=30)

                # Store profiles to profiles.db
                for event in profile_events:
                    try:
                        store_profile(profiles_db, event)
                        fetched_count += 1
                        still_missing.discard(event.get('pubkey'))
                    except Exception:
                        # Profile might already exist, continue
                        still_missing.discard(event.get('pubkey'))
                        pass

            except Exception as e:
                # Continue even if batch fails
                import sys
                print(f"Warning: Failed to fetch profile batch: {e}", file=sys.stderr)
                continue

        # Fallback: Search well-known relays for remaining missing profiles
        if use_fallback_relays and still_missing:
            import sys
            print(f"\n🔍 Searching fallback relays for {len(still_missing)} remaining profiles...", file=sys.stderr)

            fallback_relays = self._get_fallback_relays()
            fallback_count = 0

            for i in range(0, len(list(still_missing)), batch_size):
                batch = list(still_missing)[i:i + batch_size]

                # Build nak command with fallback relays
                cmd = ['nak', 'req', '-k', '0']
                for pubkey in batch:
                    cmd.extend(['-a', pubkey])
                cmd.extend(['-l', '1'])
                cmd.extend(fallback_relays)

                # Execute and parse
                try:
                    profile_events = self._execute_fetch_command(cmd, timeout=45)

                    # Store profiles to profiles.db
                    for event in profile_events:
                        try:
                            store_profile(profiles_db, event)
                            fallback_count += 1
                            fetched_count += 1
                        except Exception:
                            # Profile might already exist, continue
                            pass

                except Exception as e:
                    print(f"Warning: Fallback batch failed: {e}", file=sys.stderr)
                    continue

            if fallback_count > 0:
                print(f"✓ Found {fallback_count} profiles on fallback relays", file=sys.stderr)

        return fetched_count

    def _get_all_kinds_for_feed(self, feed_config: Dict[str, Any]) -> List[int]:
        """
        Derive all event kinds needed for feed from dependency tree.

        This includes:
        - Root kind (the primary event type for the feed)
        - Dependency kinds (reactions, reposts, zaps, etc.)

        Kind 0 (profiles) is excluded from the main query because:
        - Profiles are fetched separately AFTER the main fetch
        - We extract pubkeys from fetched events, then fetch missing profiles
        - This allows batching and deduplication across multiple events

        Args:
            feed_config: Feed configuration from config.toml

        Returns:
            List of kinds to fetch (e.g., [1, 7, 6, 9735] for timeline)

        Examples:
            >>> # For timeline feed with:
            >>> # root.kind = 1 (notes)
            >>> # deps.reactions.kind = 7
            >>> # deps.reposts.kind = 6
            >>> # deps.zaps.kind = 9735
            >>> kinds = fetcher._get_all_kinds_for_feed(timeline_config)
            >>> print(kinds)
            [1, 7, 6, 9735]
        """
        kinds = []

        # Add root kind (primary event type for the feed)
        root_kind = feed_config.get('root', {}).get('kind', 1)
        kinds.append(root_kind)

        # Add dependency kinds from deps configuration
        # Skip kind 0 (profiles) as they're fetched separately
        deps = feed_config.get('root', {}).get('deps', {})
        for dep_name, dep_config in deps.items():
            dep_kind = dep_config.get('kind')
            if dep_kind and dep_kind != 0 and dep_kind not in kinds:
                kinds.append(dep_kind)

        return kinds

    def _generate_fetch_command(
        self,
        feed_name: str,
        feed_config: Dict[str, Any],
        limit: int,
        since: Optional[int],
        until: Optional[int],
        relays: Optional[List[str]]
    ) -> List[str]:
        """
        Generate nak req command for feed.

        Supports bidirectional fetching:
        - Upward: --since <timestamp> (fetch newer events)
        - Downward: --until <timestamp> (fetch older events)

        Args:
            feed_name: Feed name
            feed_config: Feed configuration from config.toml
            limit: Maximum events to fetch
            since: Fetch events after this timestamp (upward)
            until: Fetch events before this timestamp (downward)
            relays: Relay URLs

        Returns:
            Command list for subprocess
        """
        # Start with base command
        cmd = ['nak', 'req']

        # Add kind filters for root + all dependencies
        # This fetches root events (e.g., notes) AND related events
        # (reactions, reposts, zaps) in a single query
        kinds = self._get_all_kinds_for_feed(feed_config)
        for kind in kinds:
            cmd.extend(['-k', str(kind)])

        # Add author filter if feed uses follows
        if feed_config.get('filter_by_follows', False):
            authors = self._get_follow_list()
            if authors:
                for author in authors:
                    cmd.extend(['-a', author])
            else:
                # No follows yet, use default user
                default_npub = self.config.global_config.get('default_npub')
                if default_npub:
                    default_hex = npub_to_hex(default_npub)
                    cmd.extend(['-a', default_hex])

        # Add filter constraints
        root_filter = feed_config.get('root', {}).get('filter', {})
        if root_filter.get('no_e_tags'):
            # Only top-level posts (no replies)
            cmd.append('--no-e-tags')

        # Add time filters (bidirectional)
        # Upward growth: --since (fetch events AFTER this timestamp)
        if since is not None and since > 0:
            cmd.extend(['--since', str(since)])

        # Downward backfill: --until (fetch events BEFORE this timestamp)
        if until is not None and until > 0:
            cmd.extend(['--until', str(until)])

        # Add limit
        cmd.extend(['-l', str(limit)])

        # Add relays (priority: explicit param > feed config > user config)
        if relays:
            cmd.extend(relays)
        else:
            # Check for feed-specific relays in config
            feed_relays = feed_config.get('relays')
            if feed_relays:
                cmd.extend(feed_relays)
            else:
                # Fall back to user's relays from config
                user_relays = self._get_user_relays()
                cmd.extend(user_relays)

        return cmd

    def _execute_fetch_command(self, cmd: List[str], timeout: int = 30) -> List[Dict[str, Any]]:
        """
        Execute nak command and parse JSONL output.

        Args:
            cmd: Command list
            timeout: Timeout in seconds

        Returns:
            List of event dictionaries
        """
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout
            )

            events = []
            for line in result.stdout.strip().split('\n'):
                if line:
                    try:
                        event = json.loads(line)
                        events.append(event)
                    except json.JSONDecodeError:
                        continue

            return events

        except subprocess.TimeoutExpired:
            return []
        except FileNotFoundError:
            raise RuntimeError("nak CLI not found - please install nak")

    def _get_follow_list(self) -> List[str]:
        """
        Get list of followed pubkeys from kind 3 event.

        Returns:
            List of hex pubkeys
        """
        # Get default user
        default_npub = self.config.global_config.get('default_npub')
        if not default_npub:
            return []

        default_hex = npub_to_hex(default_npub)

        # Try to find kind 3 event in any database
        from .event_finder import find_event_in_databases

        try:
            # Search for user's kind 3 in databases
            kind3_event = None
            for db_name in ['timeline.db', 'profiles.db']:
                try:
                    conn = self.db_manager.get_connection(db_name)
                    from .db import get_events_by_author
                    events = get_events_by_author(conn, default_hex, kinds=[3], limit=1)
                    if events:
                        kind3_event = events[0]
                        break
                except FileNotFoundError:
                    continue

            if not kind3_event:
                return []

            # Extract p tags (followed pubkeys)
            follows = []
            for tag in kind3_event.get('tags', []):
                if isinstance(tag, list) and len(tag) >= 2 and tag[0] == 'p':
                    follows.append(tag[1])

            return follows

        except Exception:
            return []

    def _get_user_relays(self) -> List[str]:
        """
        Get user's relay list from config.

        Returns:
            List of relay URLs
        """
        default_npub = self.config.global_config.get('default_npub')
        if not default_npub:
            return ['wss://relay.damus.io', 'wss://relay.nostr.band']

        user_config = self.config.users.get(default_npub, {})
        relays = user_config.get('relays', [])

        if not relays:
            return ['wss://relay.damus.io', 'wss://relay.nostr.band']

        return relays

    def _get_fallback_relays(self) -> List[str]:
        """
        Get comprehensive list of well-known public relays for profile discovery.

        These relays are used as a fallback when profiles aren't found on
        user's configured relays. Uses major public relays with good uptime
        and broad coverage.

        Returns:
            List of fallback relay URLs

        Examples:
            >>> relays = fetcher._get_fallback_relays()
            >>> print(f"Searching {len(relays)} fallback relays")
        """
        return [
            # Major public relays
            'wss://relay.damus.io',
            'wss://relay.nostr.band',
            'wss://nos.lol',
            'wss://relay.snort.social',
            'wss://relay.current.fyi',
            'wss://nostr.wine',
            'wss://relay.nostr.bg',

            # Popular community relays
            'wss://nostr-pub.wellorder.net',
            'wss://relay.nostr.info',
            'wss://nostr.fmt.wiz.biz',
            'wss://relay.orangepill.dev',

            # Specialized indexers (good for profile discovery)
            'wss://purplepag.es',
            'wss://relay.noswhere.com',
            'wss://relay.nostrati.com',

            # Additional coverage
            'wss://offchain.pub',
            'wss://bitcoiner.social',
            'wss://nostr.rocks',
        ]

    def fetch_profile_events(
        self,
        npub_or_hex: str,
        kinds: Optional[List[int]] = None,
        limit: int = 100,
        relays: Optional[List[str]] = None
    ) -> int:
        """
        Fetch all events for a specific user (manual profile view).

        Args:
            npub_or_hex: User's npub or hex pubkey
            kinds: Event kinds to fetch (defaults to [0, 1])
            limit: Maximum events to fetch
            relays: Relay URLs (defaults to user's relays)

        Returns:
            Number of events fetched

        Examples:
            >>> count = fetcher.fetch_profile_events('npub1abc...', kinds=[0, 1, 7])
            >>> print(f"Fetched {count} events for profile")
        """
        pubkey = npub_to_hex(npub_or_hex)

        if kinds is None:
            kinds = [0, 1]  # Profile metadata + notes

        # Build nak command
        cmd = ['nak', 'req']

        for kind in kinds:
            cmd.extend(['-k', str(kind)])

        cmd.extend(['-a', pubkey])
        cmd.extend(['-l', str(limit)])

        # Add relays
        if relays:
            cmd.extend(relays)
        else:
            cmd.extend(self._get_user_relays())

        # Execute and store
        events = self._execute_fetch_command(cmd)

        # Store to profiles.db for metadata, timeline.db for notes (thread-safe)
        stored_count = 0

        # Separate events by database to minimize lock contention
        profile_events = [e for e in events if e.get('kind', 0) == 0]
        other_events = [e for e in events if e.get('kind', 0) != 0]

        # Store profiles
        if profile_events:
            with self.db_manager.write_transaction('profiles.db') as profiles_db:
                for event in profile_events:
                    try:
                        store_profile(profiles_db, event, auto_commit=False)
                        stored_count += 1
                    except Exception:
                        # Profile might already exist, continue
                        pass
                # Commit happens automatically when exiting context

        # Store other events
        if other_events:
            with self.db_manager.write_transaction('timeline.db') as timeline_db:
                for event in other_events:
                    if store_event(timeline_db, event, auto_commit=False):
                        stored_count += 1
                # Commit happens automatically when exiting context

        return stored_count

    def fetch_follows_list(
        self,
        npub_or_hex: Optional[str] = None,
        relays: Optional[List[str]] = None
    ) -> int:
        """
        Fetch user's kind 3 follow list and store to database.

        Args:
            npub_or_hex: User's npub/hex (defaults to default_npub from config)
            relays: Relay URLs (defaults to user's relays)

        Returns:
            Number of follows found (0 if not found)

        Examples:
            >>> count = fetcher.fetch_follows_list()
            >>> print(f"Following {count} users")
        """
        if npub_or_hex is None:
            npub_or_hex = self.config.global_config.get('default_npub')

        if not npub_or_hex:
            raise ValueError("No user specified and no default_npub in config")

        pubkey = npub_to_hex(npub_or_hex)

        # Fetch kind 3 event
        cmd = ['nak', 'req', '-k', '3', '-a', pubkey, '-l', '1']

        if relays:
            cmd.extend(relays)
        else:
            cmd.extend(self._get_user_relays())

        events = self._execute_fetch_command(cmd)

        if not events:
            return 0

        # Store to timeline.db (thread-safe with batched commits)
        with self.db_manager.write_transaction('timeline.db') as timeline_db:
            for event in events:
                store_event(timeline_db, event, auto_commit=False)
            # Commit happens automatically when exiting context

        # Count follows
        kind3_event = events[0]
        follow_count = sum(
            1 for tag in kind3_event.get('tags', [])
            if isinstance(tag, list) and len(tag) >= 2 and tag[0] == 'p'
        )

        return follow_count

    def get_last_fetch_time(self, feed_name: str) -> int:
        """
        Get timestamp of last successful fetch for feed.

        Args:
            feed_name: Feed name

        Returns:
            Unix timestamp (0 if never fetched)
        """
        return self._last_fetch.get(feed_name, 0)

    def clear_fetch_history(self, feed_name: Optional[str] = None) -> None:
        """
        Clear fetch history (forces full refetch on next update).

        Args:
            feed_name: Feed to clear (clears all if None)
        """
        if feed_name:
            self._last_fetch.pop(feed_name, None)
        else:
            self._last_fetch.clear()
