"""
Database layer for nostr-feeds enricher

SQLite connection management and query functions.
Supports per-feed databases and shared profiles database.
"""

import sqlite3
import json
import os
import sys
import threading
from contextlib import contextmanager
from typing import List, Dict, Any, Optional
from pathlib import Path


class DatabaseManager:
    """Manage SQLite database connections with WAL mode."""

    def __init__(self, db_dir: str = "~/.local/share/nostr-feeds"):
        """
        Initialize database manager.

        Args:
            db_dir: Directory containing SQLite databases
        """
        self.db_dir = Path(os.path.expanduser(db_dir))
        self.connections: Dict[str, sqlite3.Connection] = {}
        self.locks: Dict[str, threading.Lock] = {}  # Per-database locks

    def get_connection(self, db_name: str) -> sqlite3.Connection:
        """
        Get or create connection to database.

        Args:
            db_name: Database filename (e.g., 'timeline.db', 'profiles.db')

        Returns:
            SQLite connection object

        Raises:
            FileNotFoundError: If database file doesn't exist
        """
        if db_name in self.connections:
            return self.connections[db_name]

        db_path = self.db_dir / db_name

        if not db_path.exists():
            raise FileNotFoundError(f"Database not found: {db_path}")

        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row  # Return rows as dicts

        # Enable WAL mode for concurrent access
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 10000")  # 10 second timeout (increased for batch writes)

        self.connections[db_name] = conn
        self.locks[db_name] = threading.Lock()  # Create lock for this database
        return conn

    def get_lock(self, db_name: str) -> threading.Lock:
        """
        Get thread lock for a specific database.

        Args:
            db_name: Database filename

        Returns:
            Threading lock for this database
        """
        if db_name not in self.locks:
            self.locks[db_name] = threading.Lock()
        return self.locks[db_name]

    @contextmanager
    def write_transaction(self, db_name: str):
        """
        Context manager for thread-safe write operations with batched commits.

        Acquires lock, yields connection for multiple writes, then commits once.
        This dramatically reduces lock contention vs committing after each write.

        Usage:
            with db_manager.write_transaction('timeline.db') as conn:
                store_event(conn, event1, auto_commit=False)
                store_event(conn, event2, auto_commit=False)
                # Commit happens automatically when exiting context

        Args:
            db_name: Database filename

        Yields:
            SQLite connection (for convenience)
        """
        lock = self.get_lock(db_name)
        conn = self.get_connection(db_name)

        with lock:
            try:
                yield conn
                # Commit all writes in one transaction
                conn.commit()
            except Exception:
                # Rollback on error
                conn.rollback()
                raise

    def close_all(self):
        """Close all database connections."""
        for conn in self.connections.values():
            conn.close()
        self.connections.clear()

    def __del__(self):
        """Cleanup on deletion."""
        self.close_all()


def row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """
    Convert SQLite row to dictionary.

    Args:
        row: SQLite row object

    Returns:
        Dictionary with column names as keys
    """
    return dict(row)


def get_events(
    conn: sqlite3.Connection,
    limit: int = 50,
    since: Optional[int] = None,
    until: Optional[int] = None,
    kinds: Optional[List[int]] = None,
    authors: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """
    Query events from database.

    Args:
        conn: SQLite connection
        limit: Maximum number of events to return
        since: Only return events after this timestamp
        until: Only return events before this timestamp
        kinds: Only return events of these kinds
        authors: Only return events from these pubkeys (for follow filtering)

    Returns:
        List of event dictionaries (most recent first)
    """
    query = "SELECT * FROM events WHERE 1=1"
    params = []

    if since is not None:
        query += " AND created_at > ?"
        params.append(since)

    if until is not None:
        query += " AND created_at < ?"
        params.append(until)

    if kinds:
        placeholders = ','.join('?' * len(kinds))
        query += f" AND kind IN ({placeholders})"
        params.extend(kinds)

    if authors:
        placeholders = ','.join('?' * len(authors))
        query += f" AND pubkey IN ({placeholders})"
        params.extend(authors)

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    cursor = conn.execute(query, params)
    rows = cursor.fetchall()

    return [parse_event_row(row) for row in rows]


def get_event_by_id(conn: sqlite3.Connection, event_id: str) -> Optional[Dict[str, Any]]:
    """
    Get single event by ID.

    Args:
        conn: SQLite connection
        event_id: Event ID (hex string)

    Returns:
        Event dictionary or None if not found
    """
    cursor = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,))
    row = cursor.fetchone()

    return parse_event_row(row) if row else None


def get_addressable_event(
    conn: sqlite3.Connection,
    kind: int,
    pubkey: str,
    d_tag: str
) -> Optional[Dict[str, Any]]:
    """
    Get addressable event by kind, pubkey, and d-tag (NIP-33).

    Addressable events are uniquely identified by the combination of
    kind (30000-39999), author pubkey, and d-tag value.

    Args:
        conn: SQLite connection
        kind: Event kind (30000-39999)
        pubkey: Author's public key (hex)
        d_tag: d-tag identifier value

    Returns:
        Event dictionary or None if not found

    Examples:
        >>> # Look up long-form article (kind 30023)
        >>> get_addressable_event(conn, 30023, 'abc123...', '1680612926599')
        {'kind': 30023, 'content': '# Article Title...', ...}
    """
    # Build LIKE pattern for d-tag: ["d", "value"
    like_pattern = f'%["d", "{d_tag}"%'

    query = """
        SELECT * FROM events
        WHERE kind = ?
        AND pubkey = ?
        AND tags_json LIKE ?
        ORDER BY created_at DESC
        LIMIT 1
    """

    cursor = conn.execute(query, (kind, pubkey, like_pattern))
    row = cursor.fetchone()

    return parse_event_row(row) if row else None


def get_events_by_tag(
    conn: sqlite3.Connection,
    kind: int,
    tag_type: str,
    tag_value: str,
    limit: int = 100
) -> List[Dict[str, Any]]:
    """
    Query events by tag reference.

    This uses LIKE query on tags_json. For better performance,
    a separate tags table could be used, but this is simpler
    and sufficient for our use case.

    Args:
        conn: SQLite connection
        kind: Event kind to filter
        tag_type: Tag identifier (e.g., 'e', 'p')
        tag_value: Tag value to search for
        limit: Maximum number of events

    Returns:
        List of event dictionaries

    Examples:
        >>> # Find all reactions (kind 7) to event abc123
        >>> get_events_by_tag(conn, 7, 'e', 'abc123')
        [{'kind': 7, 'content': '❤️', ...}, ...]
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

    cursor = conn.execute(query, (kind, like_pattern, limit))
    rows = cursor.fetchall()

    return [parse_event_row(row) for row in rows]


def get_events_by_author(
    conn: sqlite3.Connection,
    pubkey: str,
    kinds: Optional[List[int]] = None,
    limit: int = 50
) -> List[Dict[str, Any]]:
    """
    Query events by author pubkey.

    Args:
        conn: SQLite connection
        pubkey: Author's public key (hex)
        kinds: Optional list of kinds to filter
        limit: Maximum number of events

    Returns:
        List of event dictionaries (most recent first)
    """
    query = "SELECT * FROM events WHERE pubkey = ?"
    params = [pubkey]

    if kinds:
        placeholders = ','.join('?' * len(kinds))
        query += f" AND kind IN ({placeholders})"
        params.extend(kinds)

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    cursor = conn.execute(query, params)
    rows = cursor.fetchall()

    return [parse_event_row(row) for row in rows]


def get_top_level_posts(
    conn: sqlite3.Connection,
    pubkey: str,
    limit: int = 50
) -> List[Dict[str, Any]]:
    """
    Query top-level kind 1 posts (no e-tags) by author.

    This filters for posts that are NOT replies, useful for profile views.

    Args:
        conn: SQLite connection
        pubkey: Author's public key (hex)
        limit: Maximum number of posts

    Returns:
        List of event dictionaries (most recent first, top-level only)

    Examples:
        >>> posts = get_top_level_posts(conn, 'deadbeef...', limit=20)
        >>> all(not any(tag[0] == 'e' for tag in p['tags']) for p in posts)
        True
    """
    # Filter for kind 1 posts with no e-tags
    # Note: We check that tags_json doesn't contain ["e", pattern
    query = """
        SELECT * FROM events
        WHERE pubkey = ?
        AND kind = 1
        AND (tags_json IS NULL OR tags_json NOT LIKE '%["e",%')
        ORDER BY created_at DESC
        LIMIT ?
    """

    cursor = conn.execute(query, (pubkey, limit))
    rows = cursor.fetchall()

    return [parse_event_row(row) for row in rows]


def get_reply_posts(
    conn: sqlite3.Connection,
    pubkey: str,
    limit: int = 50
) -> List[Dict[str, Any]]:
    """
    Query reply posts (kind 1 with e-tags) by author.

    This filters for posts that ARE replies, useful for reply views.

    Args:
        conn: SQLite connection
        pubkey: Author's public key (hex)
        limit: Maximum number of replies

    Returns:
        List of event dictionaries (most recent first, replies only)

    Examples:
        >>> replies = get_reply_posts(conn, 'deadbeef...', limit=20)
        >>> all(any(tag[0] == 'e' for tag in r['tags']) for r in replies)
        True
    """
    # Filter for kind 1 posts WITH e-tags
    query = """
        SELECT * FROM events
        WHERE pubkey = ?
        AND kind = 1
        AND tags_json LIKE '%["e",%'
        ORDER BY created_at DESC
        LIMIT ?
    """

    cursor = conn.execute(query, (pubkey, limit))
    rows = cursor.fetchall()

    return [parse_event_row(row) for row in rows]


def parse_event_row(row: sqlite3.Row) -> Dict[str, Any]:
    """
    Parse event row from database into standard format.

    Converts tags_json string to Python list if present.

    Args:
        row: SQLite row from events table

    Returns:
        Event dictionary with parsed tags
    """
    event = row_to_dict(row)

    # Parse tags_json if present
    if 'tags_json' in event and event['tags_json']:
        try:
            event['tags'] = json.loads(event['tags_json'])
        except json.JSONDecodeError:
            event['tags'] = []
    else:
        event['tags'] = []

    return event


def get_tag(event: Dict[str, Any], tag_name: str) -> Optional[str]:
    """
    Extract tag value from event.

    Args:
        event: Event dictionary with 'tags' field
        tag_name: Tag identifier (e.g., 'd', 'e', 'p', 'a')

    Returns:
        First matching tag value or None if not found

    Examples:
        >>> event = {'tags': [['d', 'my-article'], ['t', 'bitcoin']]}
        >>> get_tag(event, 'd')
        'my-article'
        >>> get_tag(event, 'e')
        None
    """
    for tag in event.get('tags', []):
        if tag and len(tag) >= 2 and tag[0] == tag_name:
            return tag[1]
    return None


def get_profile(conn: sqlite3.Connection, pubkey: str) -> Optional[Dict[str, Any]]:
    """
    Get profile from profiles.db.

    Args:
        conn: SQLite connection to profiles.db
        pubkey: Public key (hex)

    Returns:
        Profile dictionary or None if not found
    """
    cursor = conn.execute(
        "SELECT * FROM profiles WHERE pubkey = ?",
        (pubkey,)
    )
    row = cursor.fetchone()

    if not row:
        return None

    # Convert row to dict (handle both Row and tuple)
    try:
        profile = row_to_dict(row)
    except (ValueError, TypeError):
        # Fallback: manually map columns
        columns = [desc[0] for desc in cursor.description]
        profile = dict(zip(columns, row))

    # Parse content_json if present (contains full kind 0 event)
    if profile.get('content_json'):
        try:
            event = json.loads(profile['content_json'])

            # Extract from tags (NIP-01 format)
            for tag in event.get('tags', []):
                if len(tag) >= 2:
                    key, value = tag[0], tag[1]
                    if key in ['name', 'display_name', 'about', 'picture', 'banner', 'nip05', 'lud16', 'website']:
                        profile[key] = value
                    if key == 'lud16':
                        profile['lightning_address'] = value

            # Also parse the content field (contains JSON profile data)
            if event.get('content'):
                try:
                    content_data = json.loads(event['content'])
                    # Extract standard profile fields
                    for key in ['name', 'display_name', 'about', 'picture', 'banner', 'nip05', 'lud16', 'website']:
                        if key in content_data and not profile.get(key):
                            profile[key] = content_data[key]
                    if 'lud16' in content_data and not profile.get('lightning_address'):
                        profile['lightning_address'] = content_data['lud16']
                except json.JSONDecodeError:
                    pass
        except json.JSONDecodeError:
            pass

    return profile


def store_profile(conn: sqlite3.Connection, profile: Dict[str, Any], auto_commit: bool = True) -> None:
    """
    Store or update profile in profiles.db.

    Args:
        conn: SQLite connection to profiles.db
        profile: Profile dictionary with at least 'pubkey' field
        auto_commit: If True, commit after insert. If False, caller must commit.
    """
    import time

    # Extract standard fields
    fields = {
        'pubkey': profile.get('pubkey'),
        'name': profile.get('name'),
        'display_name': profile.get('display_name'),
        'about': profile.get('about'),
        'picture': profile.get('picture'),
        'nip05': profile.get('nip05'),
        'nip05_verified': profile.get('nip05_verified', 0),
        'lightning_address': profile.get('lud16') or profile.get('lud06'),
        'fetched_at': int(time.time()),
        'content_json': json.dumps(profile)
    }

    # Upsert (INSERT OR REPLACE)
    conn.execute("""
        INSERT OR REPLACE INTO profiles
        (pubkey, name, display_name, about, picture, nip05, nip05_verified,
         lightning_address, fetched_at, content_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        fields['pubkey'],
        fields['name'],
        fields['display_name'],
        fields['about'],
        fields['picture'],
        fields['nip05'],
        fields['nip05_verified'],
        fields['lightning_address'],
        fields['fetched_at'],
        fields['content_json']
    ))

    if auto_commit:
        conn.commit()


def count_events(conn: sqlite3.Connection, kind: Optional[int] = None) -> int:
    """
    Count events in database.

    Args:
        conn: SQLite connection
        kind: Optional kind to filter

    Returns:
        Number of events
    """
    if kind is not None:
        cursor = conn.execute(
            "SELECT COUNT(*) FROM events WHERE kind = ?",
            (kind,)
        )
    else:
        cursor = conn.execute("SELECT COUNT(*) FROM events")

    return cursor.fetchone()[0]


def count_events_by_author(
    conn: sqlite3.Connection,
    pubkey: str,
    kinds: Optional[List[int]] = None
) -> int:
    """
    Count events by author pubkey.

    Args:
        conn: SQLite connection
        pubkey: Author's public key (hex)
        kinds: Optional list of kinds to filter

    Returns:
        Number of events by this author
    """
    query = "SELECT COUNT(*) FROM events WHERE pubkey = ?"
    params = [pubkey]

    if kinds:
        placeholders = ','.join('?' * len(kinds))
        query += f" AND kind IN ({placeholders})"
        params.extend(kinds)

    cursor = conn.execute(query, params)
    return cursor.fetchone()[0]


def store_event(conn: sqlite3.Connection, event: Dict[str, Any], auto_commit: bool = True) -> bool:
    """
    Store event to database.

    Args:
        conn: SQLite connection
        event: Event dictionary
        auto_commit: If True, commit after insert. If False, caller must commit.
                    Set to False when doing batch inserts for better performance.

    Returns:
        True if stored successfully, False if duplicate
    """
    import time

    # Validate required fields
    event_id = event.get('id')
    if not event_id:
        print(f"Warning: Event missing 'id' field, skipping: {event}", file=sys.stderr)
        return False

    try:
        tags_json = json.dumps(event.get('tags', []))
        event_json = json.dumps(event)
        fetched_at = int(time.time())

        conn.execute("""
            INSERT OR IGNORE INTO events
            (id, pubkey, created_at, kind, tags_json, content, sig, event_json, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event_id,
            event.get('pubkey', ''),
            event.get('created_at', 0),
            event.get('kind', 0),
            tags_json,
            event.get('content', ''),
            event.get('sig', ''),
            event_json,
            fetched_at
        ))
        if auto_commit:
            conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    except sqlite3.InterfaceError as e:
        print(f"SQLite InterfaceError storing event {event_id}: {e}", file=sys.stderr)
        print(f"Event data: id={event_id}, pubkey={event.get('pubkey')}, kind={event.get('kind')}", file=sys.stderr)
        return False


# NIP-65 Relay List Management

def init_relay_lists_table(conn: sqlite3.Connection) -> None:
    """
    Create relay_lists table for NIP-65 relay metadata (kind 10002).

    Args:
        conn: SQLite connection to profiles.db
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS relay_lists (
            pubkey TEXT PRIMARY KEY,
            kind INTEGER DEFAULT 10002,
            created_at INTEGER,
            content TEXT,
            read_relays TEXT,
            write_relays TEXT,
            all_relays TEXT,
            fetched_at INTEGER,
            FOREIGN KEY (pubkey) REFERENCES profiles(pubkey)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_relay_lists_pubkey ON relay_lists(pubkey)")
    conn.commit()


def store_relay_list(conn: sqlite3.Connection, event: Dict[str, Any]) -> None:
    """
    Store kind 10002 relay list to profiles.db.

    Args:
        conn: SQLite connection to profiles.db
        event: Kind 10002 event with r tags
    """
    import time

    pubkey = event.get('pubkey')

    # Parse relay tags
    read_relays = []
    write_relays = []
    all_relays = []

    for tag in event.get('tags', []):
        if isinstance(tag, list) and len(tag) >= 2 and tag[0] == 'r':
            relay_url = tag[1]
            permission = tag[2] if len(tag) > 2 else None

            if permission == 'read':
                read_relays.append(relay_url)
            elif permission == 'write':
                write_relays.append(relay_url)
            else:
                # No permission marker means both read and write
                all_relays.append(relay_url)

    # Combine: read_relays includes both "read" and "all"
    final_read = list(set(read_relays + all_relays))
    final_write = list(set(write_relays + all_relays))
    final_all = list(set(read_relays + write_relays + all_relays))

    # Upsert
    conn.execute("""
        INSERT OR REPLACE INTO relay_lists
        (pubkey, kind, created_at, content, read_relays, write_relays, all_relays, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        pubkey,
        event.get('kind', 10002),
        event.get('created_at'),
        event.get('content', ''),
        json.dumps(final_read),
        json.dumps(final_write),
        json.dumps(final_all),
        int(time.time())
    ))

    conn.commit()


def get_relay_list(conn: sqlite3.Connection, pubkey: str) -> Optional[Dict[str, Any]]:
    """
    Get relay list for pubkey from profiles.db.

    Args:
        conn: SQLite connection to profiles.db
        pubkey: Hex pubkey

    Returns:
        Dictionary with read, write, all relay lists or None if not found
    """
    cursor = conn.execute(
        "SELECT * FROM relay_lists WHERE pubkey = ?",
        (pubkey,)
    )
    row = cursor.fetchone()

    if not row:
        return None

    # Convert row to dict (handle both Row and tuple)
    try:
        relay_data = dict(row)
    except (ValueError, TypeError):
        # Fallback: manually map columns
        columns = [desc[0] for desc in cursor.description]
        relay_data = dict(zip(columns, row))

    # Parse JSON arrays
    try:
        relay_data['read_relays'] = json.loads(relay_data['read_relays']) if relay_data.get('read_relays') else []
        relay_data['write_relays'] = json.loads(relay_data['write_relays']) if relay_data.get('write_relays') else []
        relay_data['all_relays'] = json.loads(relay_data['all_relays']) if relay_data.get('all_relays') else []
    except json.JSONDecodeError:
        relay_data['read_relays'] = []
        relay_data['write_relays'] = []
        relay_data['all_relays'] = []

    return relay_data


def get_inbox_relays(conn: sqlite3.Connection, pubkey: str) -> List[str]:
    """
    Get read relays (inbox) for pubkey.

    Args:
        conn: SQLite connection to profiles.db
        pubkey: Hex pubkey

    Returns:
        List of relay URLs for reading (inbox)
    """
    relay_list = get_relay_list(conn, pubkey)
    return relay_list['read_relays'] if relay_list else []


def get_outbox_relays(conn: sqlite3.Connection, pubkey: str) -> List[str]:
    """
    Get write relays (outbox) for pubkey.

    Args:
        conn: SQLite connection to profiles.db
        pubkey: Hex pubkey

    Returns:
        List of relay URLs for writing (outbox)
    """
    relay_list = get_relay_list(conn, pubkey)
    return relay_list['write_relays'] if relay_list else []
