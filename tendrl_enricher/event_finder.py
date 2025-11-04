"""
Event finder for nostr-feeds enricher

Search for events across multiple databases and optionally fetch from relays.
"""

import os
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Any

from .db import get_event_by_id, DatabaseManager


class EventNotFoundError(Exception):
    """Raised when event cannot be found in any database."""
    pass


def find_event_in_databases(
    event_id: str,
    db_manager: DatabaseManager,
    db_names: Optional[List[str]] = None
) -> Optional[Dict[str, Any]]:
    """
    Search for event across multiple databases.

    Args:
        event_id: Hex event ID to search for
        db_manager: DatabaseManager instance
        db_names: Optional list of database names to search
                 (defaults to all *.db files in db_dir)

    Returns:
        Event dictionary if found, None otherwise

    Examples:
        >>> db_manager = DatabaseManager('~/.local/share/nostr-feeds')
        >>> event = find_event_in_databases('abc123...', db_manager)
        >>> event['id']
        'abc123...'
    """
    if db_names is None:
        # Search all databases in directory
        db_dir = Path(db_manager.db_dir)
        if not db_dir.exists():
            return None

        db_names = [f.name for f in db_dir.glob('*.db')]

    # Search each database
    for db_name in db_names:
        try:
            conn = db_manager.get_connection(db_name)
            event = get_event_by_id(conn, event_id)

            if event:
                # Add metadata about which database it was found in
                event['_source_db'] = db_name
                return event

        except FileNotFoundError:
            # Database doesn't exist, skip
            continue
        except sqlite3.Error:
            # Database error, skip
            continue

    return None


def find_event(
    event_id: str,
    db_manager: DatabaseManager,
    db_names: Optional[List[str]] = None,
    relay_hints: Optional[List[str]] = None,
    fetch_from_relays: bool = False
) -> Dict[str, Any]:
    """
    Find event in databases or optionally fetch from relays.

    Args:
        event_id: Hex event ID
        db_manager: DatabaseManager instance
        db_names: Optional list of databases to search
        relay_hints: Optional relay URLs to try (from nevent)
        fetch_from_relays: If True, fetch from relays if not in DB

    Returns:
        Event dictionary

    Raises:
        EventNotFoundError: If event not found anywhere

    Examples:
        >>> event = find_event('abc123...', db_manager)
        >>> event = find_event('abc123...', db_manager,
        ...                    relay_hints=['wss://relay.damus.io'],
        ...                    fetch_from_relays=True)
    """
    # First, search local databases
    event = find_event_in_databases(event_id, db_manager, db_names)

    if event:
        return event

    # If not found and fetch enabled, try relays
    if fetch_from_relays:
        if relay_hints:
            event = fetch_event_from_relays(event_id, relay_hints)
            if event:
                return event

    # Not found anywhere
    raise EventNotFoundError(
        f"Event not found: {event_id}\n"
        f"Searched databases: {db_names or 'all'}\n"
        f"Relay hints: {relay_hints or 'none'}\n"
        f"Fetch from relays: {fetch_from_relays}"
    )


def fetch_event_from_relays(
    event_id: str,
    relays: List[str],
    timeout: int = 10
) -> Optional[Dict[str, Any]]:
    """
    Fetch event from relays using nak CLI.

    Args:
        event_id: Hex event ID
        relays: List of relay URLs
        timeout: Timeout in seconds

    Returns:
        Event dictionary if found, None otherwise

    Examples:
        >>> event = fetch_event_from_relays(
        ...     'abc123...',
        ...     ['wss://relay.damus.io', 'wss://nos.lol']
        ... )
    """
    import subprocess
    import json

    # Use nak req to fetch event by ID
    try:
        cmd = ['nak', 'req', '-i', event_id, '-l', '1'] + relays

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        # Parse JSONL output (nak outputs one event per line)
        lines = result.stdout.strip().split('\n')
        for line in lines:
            if line:
                try:
                    event = json.loads(line)
                    # Verify it's the right event
                    if event.get('id') == event_id:
                        return event
                except json.JSONDecodeError:
                    continue

        return None

    except subprocess.TimeoutExpired:
        return None
    except FileNotFoundError:
        # nak not installed
        return None


def list_databases(db_dir: str) -> List[str]:
    """
    List all database files in directory.

    Args:
        db_dir: Database directory path

    Returns:
        List of database filenames

    Examples:
        >>> dbs = list_databases('~/.local/share/nostr-feeds')
        >>> dbs
        ['timeline.db', 'global.db', 'profiles.db']
    """
    db_path = Path(os.path.expanduser(db_dir))

    if not db_path.exists():
        return []

    return [f.name for f in db_path.glob('*.db')]


def search_databases_for_event(
    event_id: str,
    db_dir: str
) -> Optional[str]:
    """
    Search all databases and return which one contains the event.

    Args:
        event_id: Hex event ID
        db_dir: Database directory

    Returns:
        Database filename if found, None otherwise

    Examples:
        >>> db_name = search_databases_for_event('abc123...', '~/.local/share/nostr-feeds')
        >>> db_name
        'timeline.db'
    """
    db_manager = DatabaseManager(db_dir)
    db_names = list_databases(db_dir)

    for db_name in db_names:
        try:
            conn = db_manager.get_connection(db_name)
            event = get_event_by_id(conn, event_id)

            if event:
                db_manager.close_all()
                return db_name

        except (FileNotFoundError, sqlite3.Error):
            continue

    db_manager.close_all()
    return None
