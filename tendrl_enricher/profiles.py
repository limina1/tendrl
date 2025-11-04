"""
Profile cache for nostr-feeds enricher

Two-tier caching: in-memory (LRU) + SQLite (persistent).
Profiles are kind 0 events containing user metadata.
"""

import sqlite3
import time
from collections import OrderedDict
from typing import Dict, Any, Optional

from .db import get_profile, store_profile


class ProfileCache:
    """
    Two-tier profile cache with LRU eviction.

    Tier 1: In-memory OrderedDict (fast, limited size)
    Tier 2: SQLite profiles.db (persistent, unlimited)

    Lookup order: memory → SQLite → None
    Store order: memory + SQLite (both updated)
    """

    def __init__(
        self,
        profiles_db_conn: sqlite3.Connection,
        max_memory_size: int = 10_000,
        cache_ttl: int = 3600  # 1 hour
    ):
        """
        Initialize profile cache.

        Args:
            profiles_db_conn: SQLite connection to profiles.db
            max_memory_size: Maximum profiles in memory (LRU eviction)
            cache_ttl: Profile cache TTL in seconds (0 = never expire)
        """
        self.profiles_db_conn = profiles_db_conn
        self.max_memory_size = max_memory_size
        self.cache_ttl = cache_ttl

        # In-memory cache: OrderedDict for LRU
        # Key: pubkey (hex string)
        # Value: (profile dict, fetch_timestamp)
        self._memory_cache: OrderedDict[str, tuple[Dict[str, Any], float]] = OrderedDict()

        # Statistics
        self.stats = {
            'memory_hits': 0,
            'db_hits': 0,
            'misses': 0,
            'stores': 0
        }

    def get(self, pubkey: str) -> Optional[Dict[str, Any]]:
        """
        Get profile from cache.

        Lookup order:
        1. Memory cache (fast)
        2. SQLite profiles.db (slower)
        3. None (cache miss)

        Args:
            pubkey: Public key (hex string)

        Returns:
            Profile dictionary or None if not found

        Examples:
            >>> cache = ProfileCache(conn)
            >>> profile = cache.get('deadbeef...')
            >>> profile['name']
            'alice'
        """
        # Check memory cache first
        if pubkey in self._memory_cache:
            profile, fetch_time = self._memory_cache[pubkey]

            # Check if expired
            if self.cache_ttl > 0 and time.time() - fetch_time > self.cache_ttl:
                # Expired, remove from memory
                del self._memory_cache[pubkey]
            else:
                # Hit! Move to end (most recently used)
                self._memory_cache.move_to_end(pubkey)
                self.stats['memory_hits'] += 1
                return profile

        # Check SQLite database
        profile = get_profile(self.profiles_db_conn, pubkey)

        if profile:
            # Hit! Store in memory for faster access
            fetch_time = profile.get('fetched_at', time.time())

            # Check if expired
            if self.cache_ttl > 0 and time.time() - fetch_time > self.cache_ttl:
                # Expired, return None
                self.stats['misses'] += 1
                return None

            self._store_in_memory(pubkey, profile, fetch_time)
            self.stats['db_hits'] += 1
            return profile

        # Miss
        self.stats['misses'] += 1
        return None

    def set(self, pubkey: str, profile: Dict[str, Any]) -> None:
        """
        Store profile in cache (both memory and SQLite).

        Args:
            pubkey: Public key (hex string)
            profile: Profile dictionary

        Examples:
            >>> cache.set('deadbeef...', {
            ...     'pubkey': 'deadbeef...',
            ...     'name': 'alice',
            ...     'display_name': 'Alice Johnson'
            ... })
        """
        fetch_time = time.time()

        # Store in memory
        self._store_in_memory(pubkey, profile, fetch_time)

        # Store in SQLite (persistent)
        store_profile(self.profiles_db_conn, profile)

        self.stats['stores'] += 1

    def _store_in_memory(
        self,
        pubkey: str,
        profile: Dict[str, Any],
        fetch_time: float
    ) -> None:
        """
        Store profile in memory cache with LRU eviction.

        Args:
            pubkey: Public key
            profile: Profile dictionary
            fetch_time: Unix timestamp of fetch
        """
        # Add/update in memory
        self._memory_cache[pubkey] = (profile, fetch_time)
        self._memory_cache.move_to_end(pubkey)  # Most recently used

        # Evict oldest if over limit
        while len(self._memory_cache) > self.max_memory_size:
            self._memory_cache.popitem(last=False)  # Remove oldest (FIFO)

    def is_stale(self, pubkey: str) -> bool:
        """
        Check if profile is stale and needs refreshing.

        Args:
            pubkey: Public key

        Returns:
            True if profile is missing or expired, False otherwise

        Examples:
            >>> cache.is_stale('alice_pubkey')
            True  # No profile or expired
        """
        # Check memory cache first
        if pubkey in self._memory_cache:
            profile, fetch_time = self._memory_cache[pubkey]
            if self.cache_ttl > 0 and time.time() - fetch_time > self.cache_ttl:
                return True  # Expired
            return False  # Fresh

        # Check SQLite database
        profile = get_profile(self.profiles_db_conn, pubkey)
        if not profile:
            return True  # Missing

        fetch_time = profile.get('fetched_at', 0)
        if self.cache_ttl > 0 and time.time() - fetch_time > self.cache_ttl:
            return True  # Expired

        return False  # Fresh

    def get_or_default(self, pubkey: str) -> Dict[str, Any]:
        """
        Get profile or return default placeholder.

        Useful for rendering when profile is missing.

        Args:
            pubkey: Public key

        Returns:
            Profile dictionary (real or placeholder)

        Examples:
            >>> profile = cache.get_or_default('unknown_pubkey')
            >>> profile['name']
            'unknown'
        """
        profile = self.get(pubkey)

        if profile:
            return profile

        # Return placeholder
        return {
            'pubkey': pubkey,
            'name': pubkey[:8],  # Use first 8 chars of pubkey
            'display_name': 'Unknown User',
            'about': '',
            'picture': '',
            'nip05': '',
            'nip05_verified': False
        }

    def prefetch(self, pubkeys: list[str]) -> None:
        """
        Prefetch profiles for multiple pubkeys.

        Useful for batch loading before enrichment.

        Args:
            pubkeys: List of public keys to prefetch

        Examples:
            >>> cache.prefetch(['alice_pubkey', 'bob_pubkey', ...])
        """
        for pubkey in pubkeys:
            if pubkey not in self._memory_cache:
                # Fetch from DB and cache in memory
                self.get(pubkey)

    def clear_memory(self) -> None:
        """Clear in-memory cache (keep SQLite)."""
        self._memory_cache.clear()

    def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache hit/miss statistics

        Examples:
            >>> stats = cache.get_stats()
            >>> stats['memory_hits']
            1500
            >>> stats['hit_rate']
            0.85
        """
        total_lookups = (
            self.stats['memory_hits'] +
            self.stats['db_hits'] +
            self.stats['misses']
        )

        hit_rate = (
            (self.stats['memory_hits'] + self.stats['db_hits']) / total_lookups
            if total_lookups > 0
            else 0.0
        )

        return {
            **self.stats,
            'memory_size': len(self._memory_cache),
            'total_lookups': total_lookups,
            'hit_rate': hit_rate
        }

    def __len__(self) -> int:
        """Return number of profiles in memory cache."""
        return len(self._memory_cache)

    def __contains__(self, pubkey: str) -> bool:
        """Check if profile is in memory cache."""
        return pubkey in self._memory_cache
