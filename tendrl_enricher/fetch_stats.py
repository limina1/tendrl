#!/usr/bin/env python3
"""
Fetch historical reactions, zaps, and reposts for timeline events.

This populates your database so the enricher can compute stats.
"""

import subprocess
import json
import sys
from pathlib import Path

from .config import Config
from .db import DatabaseManager, store_event


def fetch_missing_stats_for_events(
    event_ids: list[str],
    stat_kinds: list[int],
    relays: list[str],
    db_connection,
    timeout: int = 15
) -> int:
    """
    Fetch missing stat events (reactions, zaps, reposts) for given events.

    Args:
        event_ids: List of event IDs to fetch stats for
        stat_kinds: List of kinds to fetch (e.g., [6, 7, 9735])
        relays: Relay URLs to fetch from
        db_connection: Database connection to store events
        timeout: Timeout for nak command in seconds

    Returns:
        Number of stat events fetched and stored

    Examples:
        >>> fetched = fetch_missing_stats_for_events(
        ...     ['abc123...', 'def456...'],
        ...     [7, 9735],
        ...     ['wss://relay.damus.io'],
        ...     db_conn
        ... )
    """
    if not event_ids or not stat_kinds:
        return 0

    total_fetched = 0

    for kind in stat_kinds:
        events = fetch_events_for_notes(event_ids, kind, relays, timeout=timeout)

        for event in events:
            try:
                if store_event(db_connection, event):
                    total_fetched += 1
            except:
                pass  # Ignore duplicates

    return total_fetched


def fetch_stats_for_timeline(config_path: str = "enricher/config.toml"):
    """Fetch historical engagement events for timeline."""

    # Load config
    config = Config.load(config_path)
    db_manager = DatabaseManager(config.get_db_dir())

    # Get timeline database
    timeline_db = db_manager.get_connection('timeline.db')

    # Get all note IDs from timeline
    cursor = timeline_db.execute("SELECT id FROM events WHERE kind = 1 LIMIT 100")
    note_ids = [row[0] for row in cursor.fetchall()]

    if not note_ids:
        print("No notes in database. Run stream viewer first to collect notes.")
        return

    print(f"Found {len(note_ids)} notes")
    print("Fetching engagement events...\n")

    # Relays to fetch from
    relays = ['wss://relay.damus.io', 'wss://nos.lol', 'wss://relay.nostr.band']

    # Fetch reactions (kind 7)
    print("Fetching reactions (kind 7)...")
    reactions = fetch_events_for_notes(note_ids, 7, relays)
    print(f"  Found {len(reactions)} reactions")

    for event in reactions:
        try:
            store_event(timeline_db, event)
        except:
            pass  # Ignore duplicates

    # Fetch reposts (kind 6)
    print("Fetching reposts (kind 6)...")
    reposts = fetch_events_for_notes(note_ids, 6, relays)
    print(f"  Found {len(reposts)} reposts")

    for event in reposts:
        try:
            store_event(timeline_db, event)
        except:
            pass

    # Fetch zaps (kind 9735)
    # Zaps reference the event in 'e' tag
    print("Fetching zaps (kind 9735)...")
    zaps = fetch_events_for_notes(note_ids, 9735, relays)
    print(f"  Found {len(zaps)} zaps")

    for event in zaps:
        try:
            store_event(timeline_db, event)
        except:
            pass

    print("\nDone! Stats summary:")
    cursor = timeline_db.execute("SELECT kind, COUNT(*) FROM events GROUP BY kind ORDER BY kind")
    for kind, count in cursor.fetchall():
        kind_name = {1: 'notes', 3: 'follows', 6: 'reposts', 7: 'reactions', 9735: 'zaps'}.get(kind, f'kind {kind}')
        print(f"  {kind_name}: {count}")

    db_manager.close_all()

def fetch_events_for_notes(note_ids: list[str], kind: int, relays: list[str], timeout: int = 30) -> list[dict]:
    """Fetch events that reference the given notes."""

    # Build nak command
    # For reactions and reposts, use -e tag filter
    # For zaps, also use -e tag filter
    cmd = ['nak', 'req', '-k', str(kind)]

    # Add event ID filters (first 20 notes to avoid command too long)
    for note_id in note_ids[:20]:
        cmd.extend(['-e', note_id])

    # Add limit
    cmd.extend(['--limit', '500'])

    # Add relays
    cmd.extend(relays)

    print(f"  Running: nak req -k {kind} -e <{len(note_ids[:20])} notes> ...")

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
                    pass

        return events

    except subprocess.TimeoutExpired:
        print(f"  Timeout fetching kind {kind}")
        return []
    except Exception as e:
        print(f"  Error fetching kind {kind}: {e}")
        return []

if __name__ == '__main__':
    import sys
    config_path = sys.argv[1] if len(sys.argv) > 1 else "enricher/config.toml"
    fetch_stats_for_timeline(config_path)
