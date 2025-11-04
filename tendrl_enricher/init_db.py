#!/usr/bin/env python3
"""
Initialize database schema for nostr-feeds

Creates events and profiles tables with proper indexes.
"""

import sqlite3
import sys
from pathlib import Path
from typing import Optional


def create_events_table(conn: sqlite3.Connection):
    """Create events table with indexes."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            pubkey TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            kind INTEGER NOT NULL,
            tags_json TEXT,
            content TEXT,
            sig TEXT NOT NULL,
            event_json TEXT,
            fetched_at INTEGER
        )
    """)

    # Indexes for common queries
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_pubkey ON events(pubkey)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_kind_created ON events(kind, created_at DESC)")

    conn.commit()
    print("✓ Created events table")


def create_profiles_table(conn: sqlite3.Connection):
    """Create profiles table with indexes."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            pubkey TEXT PRIMARY KEY,
            name TEXT,
            display_name TEXT,
            about TEXT,
            picture TEXT,
            nip05 TEXT,
            nip05_verified INTEGER DEFAULT 0,
            lightning_address TEXT,
            fetched_at INTEGER,
            content_json TEXT
        )
    """)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_profiles_name ON profiles(name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_profiles_nip05 ON profiles(nip05)")

    conn.commit()
    print("✓ Created profiles table")


def create_relay_lists_table(conn: sqlite3.Connection):
    """Create relay_lists table for NIP-65."""
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
    print("✓ Created relay_lists table")


def init_feed_db(db_path: Path):
    """Initialize a feed database with events table."""
    print(f"\nInitializing feed database: {db_path}")

    # Create parent directory if needed
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))

    # Enable WAL mode
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")

    create_events_table(conn)

    conn.close()
    print(f"✓ Initialized: {db_path}\n")


def init_profiles_db(db_path: Path):
    """Initialize profiles.db with profiles and relay_lists tables."""
    print(f"\nInitializing profiles database: {db_path}")

    # Create parent directory if needed
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))

    # Enable WAL mode
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")

    create_profiles_table(conn)
    create_relay_lists_table(conn)

    conn.close()
    print(f"✓ Initialized: {db_path}\n")


def init_all_databases(db_dir: str, feed_dbs: Optional[list] = None):
    """
    Initialize all databases for nostr-feeds.

    Args:
        db_dir: Database directory path
        feed_dbs: List of feed database names (e.g., ['timeline.db', 'zaps.db'])
    """
    db_path = Path(db_dir).expanduser()
    db_path.mkdir(parents=True, exist_ok=True)

    print(f"Database directory: {db_path}")

    # Always initialize profiles.db
    init_profiles_db(db_path / "profiles.db")

    # Initialize feed databases
    if feed_dbs:
        for feed_db in feed_dbs:
            init_feed_db(db_path / feed_db)
    else:
        # Default databases
        for db_name in ["timeline.db", "global.db", "zaps.db", "articles.db"]:
            init_feed_db(db_path / db_name)

    print("=" * 60)
    print("✓ Database initialization complete!")
    print("=" * 60)


def main():
    """CLI entry point."""
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 -m enricher.init_db <db_dir> [feed1.db feed2.db ...]")
        print()
        print("Examples:")
        print("  # Initialize all default databases")
        print("  python3 -m enricher.init_db ~/.local/share/nostr-feeds")
        print()
        print("  # Initialize specific databases")
        print("  python3 -m enricher.init_db ~/.local/share/nostr-feeds zaps.db timeline.db")
        sys.exit(1)

    db_dir = sys.argv[1]
    feed_dbs = sys.argv[2:] if len(sys.argv) > 2 else None

    init_all_databases(db_dir, feed_dbs)


if __name__ == '__main__':
    main()
