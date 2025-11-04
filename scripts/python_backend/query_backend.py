"""
Query Backend Abstraction Layer

Provides unified interface for querying events from either:
- nostrdb (fast, native LMDB)
- SQLite (proven, reliable)

This allows Tendrl to use nostr-feeds' proven patterns while
gradually migrating to nostrdb's efficiency.
"""

import json
import sqlite3
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Dict, Optional, Any


class QueryBackend(ABC):
    """Abstract interface for event queries"""

    @abstractmethod
    def query_events(
        self,
        kinds: List[int],
        authors: Optional[List[str]] = None,
        limit: int = 100,
        since: Optional[int] = None,
        until: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Query events with filters.

        Args:
            kinds: Event kinds to query
            authors: Filter by author pubkeys (hex)
            limit: Maximum number of events
            since: Only events after this timestamp
            until: Only events before this timestamp

        Returns:
            List of event dictionaries with 'tags' as Python lists
        """
        pass

    @abstractmethod
    def get_event_by_id(self, event_id: str) -> Optional[Dict[str, Any]]:
        """
        Get single event by ID.

        Args:
            event_id: Event ID (hex)

        Returns:
            Event dictionary or None if not found
        """
        pass

    @abstractmethod
    def get_events_by_tag(
        self,
        kind: int,
        tag_type: str,
        tag_value: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Query events by tag reference.

        Args:
            kind: Event kind to filter
            tag_type: Tag identifier (e.g., 'e', 'p', 'a')
            tag_value: Tag value to search for
            limit: Maximum number of events

        Returns:
            List of event dictionaries
        """
        pass


class NostrdbBackend(QueryBackend):
    """
    Backend using nostrdb via tendrl_query binary.

    Fast native LMDB queries, but currently has tag value bug.
    """

    def __init__(self, config_path: str):
        """
        Initialize nostrdb backend.

        Args:
            config_path: Path to tendrl config file
        """
        self.config_path = Path(config_path).expanduser()
        self.query_bin = Path(__file__).parent.parent / "target/release/tendrl_query"

        if not self.query_bin.exists():
            raise FileNotFoundError(
                f"tendrl_query binary not found at {self.query_bin}\n"
                "Run: cd tendrl && cargo build --release"
            )

    def query_events(self, kinds, authors=None, limit=100, since=None, until=None):
        """Query events from nostrdb"""
        cmd = [
            str(self.query_bin),
            '--config', str(self.config_path),
            '--kinds', ','.join(map(str, kinds)),
            '--limit', str(limit)
        ]

        if authors:
            for author in authors:
                cmd.extend(['--author', author])

        if since:
            cmd.extend(['--since', str(since)])

        if until:
            cmd.extend(['--until', str(until)])

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            raise RuntimeError(f"tendrl_query failed: {result.stderr}")

        events = []
        for line in result.stdout.strip().split('\n'):
            if line:
                events.append(json.loads(line))

        return events

    def get_event_by_id(self, event_id: str):
        """Get event by ID from nostrdb"""
        cmd = [
            str(self.query_bin),
            '--config', str(self.config_path),
            '--id', event_id,
            '--limit', '1'
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0 or not result.stdout.strip():
            return None

        return json.loads(result.stdout.strip())

    def get_events_by_tag(self, kind, tag_type, tag_value, limit=100):
        """
        Query events by tag.

        Note: Currently not supported by tendrl_query CLI.
        Would need to query all events and filter in Python.
        """
        # This is inefficient but works as fallback
        events = self.query_events([kind], limit=limit * 10)  # Over-fetch

        # Filter by tag (works even with buggy tag values if we have them)
        matching = []
        for event in events:
            tags = event.get('tags', [])
            for tag in tags:
                if isinstance(tag, list) and len(tag) >= 2:
                    if tag[0] == tag_type and tag[1] == tag_value:
                        matching.append(event)
                        break
            if len(matching) >= limit:
                break

        return matching[:limit]


class SqliteBackend(QueryBackend):
    """
    Backend using SQLite with nostr-feeds proven schema.

    Stores tags as JSON strings - proven to work reliably.
    """

    def __init__(self, db_path: str):
        """
        Initialize SQLite backend.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path).expanduser()

        if not self.db_path.exists():
            raise FileNotFoundError(
                f"SQLite database not found at {self.db_path}\n"
                "Run: python3 -m tendrl.python.init_db {self.db_path}"
            )

        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row

    def query_events(self, kinds, authors=None, limit=100, since=None, until=None):
        """Query events from SQLite"""
        query = "SELECT * FROM events WHERE kind IN ({})".format(
            ','.join('?' * len(kinds))
        )
        params = list(kinds)

        if authors:
            query += " AND pubkey IN ({})".format(','.join('?' * len(authors)))
            params.extend(authors)

        if since:
            query += " AND created_at >= ?"
            params.append(since)

        if until:
            query += " AND created_at <= ?"
            params.append(until)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cursor = self.conn.execute(query, params)
        return [self._parse_row(row) for row in cursor.fetchall()]

    def get_event_by_id(self, event_id: str):
        """Get event by ID from SQLite"""
        query = "SELECT * FROM events WHERE id = ? LIMIT 1"
        cursor = self.conn.execute(query, (event_id,))
        row = cursor.fetchone()

        return self._parse_row(row) if row else None

    def get_events_by_tag(self, kind, tag_type, tag_value, limit=100):
        """
        Query events by tag using LIKE on JSON string.

        This is the proven nostr-feeds pattern that works reliably.
        """
        # Construct LIKE pattern: ["e", "abc123"
        # Note: JSON format has space after comma
        like_pattern = f'%["{tag_type}", "{tag_value}"%'

        query = """
            SELECT * FROM events
            WHERE kind = ?
            AND tags_json LIKE ?
            ORDER BY created_at DESC
            LIMIT ?
        """

        cursor = self.conn.execute(query, (kind, like_pattern, limit))
        return [self._parse_row(row) for row in cursor.fetchall()]

    def _parse_row(self, row):
        """
        Parse SQLite row to event dictionary.

        Deserializes tags_json to Python list.
        """
        if row is None:
            return None

        event = dict(row)

        # Parse tags_json if present (this is the key to making it work!)
        if 'tags_json' in event and event['tags_json']:
            try:
                event['tags'] = json.loads(event['tags_json'])
            except json.JSONDecodeError:
                event['tags'] = []
        else:
            event['tags'] = []

        return event


def create_backend(config_path: str = None, backend_type: str = "auto") -> QueryBackend:
    """
    Factory function to create appropriate backend.

    Args:
        config_path: Path to tendrl.toml (for nostrdb) or SQLite db
        backend_type: "nostrdb", "sqlite", or "auto" (detect)

    Returns:
        QueryBackend instance

    Examples:
        >>> # Auto-detect with fallback
        >>> backend = create_backend("~/.config/tendrl/tendrl.toml", "auto")

        >>> # Explicit SQLite (reliable)
        >>> backend = create_backend("~/.local/share/tendrl/feeds.db", "sqlite")

        >>> # Explicit nostrdb (fast, when working)
        >>> backend = create_backend("~/.config/tendrl/tendrl.toml", "nostrdb")
    """
    if backend_type == "auto":
        # Try nostrdb first, fall back to SQLite
        try:
            backend = NostrdbBackend(config_path)
            # Test if tags work correctly
            # (Would query a known event and check tag values)
            return backend
        except Exception as e:
            print(f"⚠️  nostrdb backend unavailable ({e}), falling back to SQLite")
            # Assume SQLite db in standard location
            sqlite_path = Path.home() / ".local/share/tendrl/feeds.db"
            return SqliteBackend(sqlite_path)

    elif backend_type == "nostrdb":
        return NostrdbBackend(config_path)

    elif backend_type == "sqlite":
        return SqliteBackend(config_path)

    else:
        raise ValueError(f"Unknown backend type: {backend_type}")


# Convenience function for testing
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python query_backend.py <config_or_db_path> [backend_type]")
        print()
        print("Examples:")
        print("  python query_backend.py tendrl.toml nostrdb")
        print("  python query_backend.py feeds.db sqlite")
        print("  python query_backend.py tendrl.toml auto")
        sys.exit(1)

    path = sys.argv[1]
    backend_type = sys.argv[2] if len(sys.argv) > 2 else "auto"

    backend = create_backend(path, backend_type)
    print(f"✓ Created backend: {backend.__class__.__name__}")

    # Test query
    events = backend.query_events([1], limit=5)
    print(f"✓ Queried {len(events)} events")

    if events:
        print(f"✓ First event has {len(events[0].get('tags', []))} tags")
        print(f"✓ First tag: {events[0].get('tags', [[]])[0]}")
