#!/usr/bin/env python3
"""
General-purpose JSONL import/export utilities for nostr-feeds.

Supports:
- Importing JSONL files to any feed database
- Exporting feed databases to JSONL
- Fetching events from relays via nak and importing
- Batch operations
"""

import json
import sys
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any

from .db import store_event
from .init_db import init_feed_db


def import_jsonl(
    jsonl_file: str,
    db_file: str,
    skip_errors: bool = True,
    verbose: bool = True
) -> int:
    """
    Import events from JSONL file to database.

    Args:
        jsonl_file: Path to JSONL file
        db_file: Path to SQLite database
        skip_errors: Continue on error (default: True)
        verbose: Print progress (default: True)

    Returns:
        Number of events successfully imported
    """
    # Initialize database if needed
    db_path = Path(db_file)
    if not db_path.exists():
        if verbose:
            print(f"Creating new database: {db_file}", file=sys.stderr)
        init_feed_db(db_path)

    # Connect to database
    db = sqlite3.connect(db_file)
    db.row_factory = sqlite3.Row

    # Import events
    count = 0
    errors = 0

    if verbose:
        print(f"Importing from {jsonl_file}...", file=sys.stderr)

    with open(jsonl_file, 'r') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()

            # Skip empty lines and connection messages
            if not line or line.startswith('connecting to'):
                continue

            try:
                event = json.loads(line)

                # Validate event has required fields
                if not isinstance(event, dict):
                    raise ValueError("Event must be a dictionary")
                if 'id' not in event or 'kind' not in event:
                    raise ValueError("Event missing required fields (id, kind)")

                # Store event
                store_event(db, event)
                count += 1

                if verbose and count % 10 == 0:
                    print(f"  Imported {count} events...", file=sys.stderr)

            except json.JSONDecodeError as e:
                errors += 1
                print(f"Line {line_num}: JSON decode error: {e}", file=sys.stderr)
                if not skip_errors:
                    raise

            except Exception as e:
                errors += 1
                print(f"Line {line_num}: Error importing event: {e}", file=sys.stderr)
                if not skip_errors:
                    raise

    db.close()

    if verbose:
        print(f"\n✓ Imported {count} events to {db_file}", file=sys.stderr)
        if errors:
            print(f"⚠ Skipped {errors} events due to errors", file=sys.stderr)

    return count


def export_jsonl(
    db_file: str,
    output_file: str,
    kind: Optional[int] = None,
    author: Optional[str] = None,
    limit: Optional[int] = None,
    verbose: bool = True
) -> int:
    """
    Export events from database to JSONL file.

    Args:
        db_file: Path to SQLite database
        output_file: Path to output JSONL file
        kind: Filter by event kind (optional)
        author: Filter by author pubkey (optional)
        limit: Limit number of events (optional)
        verbose: Print progress (default: True)

    Returns:
        Number of events exported
    """
    if not Path(db_file).exists():
        raise FileNotFoundError(f"Database not found: {db_file}")

    # Connect to database
    db = sqlite3.connect(db_file)
    db.row_factory = sqlite3.Row

    # Build query
    query = "SELECT event_json FROM events WHERE 1=1"
    params = []

    if kind is not None:
        query += " AND kind = ?"
        params.append(kind)

    if author:
        query += " AND pubkey = ?"
        params.append(author)

    query += " ORDER BY created_at DESC"

    if limit:
        query += " LIMIT ?"
        params.append(limit)

    # Export events
    cursor = db.execute(query, params)
    count = 0

    if verbose:
        print(f"Exporting to {output_file}...", file=sys.stderr)

    with open(output_file, 'w') as f:
        for row in cursor:
            f.write(row['event_json'])
            f.write('\n')
            count += 1

            if verbose and count % 100 == 0:
                print(f"  Exported {count} events...", file=sys.stderr)

    db.close()

    if verbose:
        print(f"\n✓ Exported {count} events to {output_file}", file=sys.stderr)

    return count


def fetch_and_import(
    relays: List[str],
    db_file: str,
    kind: Optional[int] = None,
    author: Optional[str] = None,
    limit: int = 100,
    since: Optional[int] = None,
    until: Optional[int] = None,
    tags: Optional[Dict[str, List[str]]] = None,
    verbose: bool = True
) -> int:
    """
    Fetch events from relays using nak and import to database.

    Args:
        relays: List of relay URLs
        db_file: Path to SQLite database
        kind: Filter by event kind (optional)
        author: Filter by author pubkey (optional)
        limit: Maximum events to fetch (default: 100)
        since: Unix timestamp - events after this time (optional)
        until: Unix timestamp - events before this time (optional)
        tags: Tag filters dict, e.g., {'t': ['bitcoin'], 'd': ['my-post']}
        verbose: Print progress (default: True)

    Returns:
        Number of events imported
    """
    # Build nak command
    cmd = ["nak", "req"]

    if kind is not None:
        cmd.extend(["-k", str(kind)])

    if author:
        cmd.extend(["-a", author])

    if limit:
        cmd.extend(["-l", str(limit)])

    if since:
        cmd.extend(["--since", str(since)])

    if until:
        cmd.extend(["--until", str(until)])

    if tags:
        for tag_name, tag_values in tags.items():
            for value in tag_values:
                cmd.extend(["--tag", f"{tag_name}={value}"])

    # Add relays
    cmd.extend(relays)

    if verbose:
        print(f"Fetching from relays: {' '.join(relays)}", file=sys.stderr)
        print(f"Command: {' '.join(cmd)}", file=sys.stderr)

    # Fetch events
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode != 0:
            print(f"Error: nak command failed with code {result.returncode}", file=sys.stderr)
            if result.stderr:
                print(result.stderr, file=sys.stderr)
            return 0

        # Save to temporary file
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            temp_file = f.name
            f.write(result.stdout)

        # Import from temp file
        count = import_jsonl(temp_file, db_file, verbose=verbose)

        # Clean up
        os.unlink(temp_file)

        return count

    except subprocess.TimeoutExpired:
        print("Error: Fetch timed out", file=sys.stderr)
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 0


def main():
    """CLI interface for JSONL tools."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Import/export JSONL files to/from nostr-feeds databases"
    )

    subparsers = parser.add_subparsers(dest='command', help='Command')

    # Import command
    import_parser = subparsers.add_parser('import', help='Import JSONL to database')
    import_parser.add_argument('jsonl_file', help='JSONL file to import')
    import_parser.add_argument('db_file', help='Database file')
    import_parser.add_argument('--strict', action='store_true',
                              help='Stop on first error')
    import_parser.add_argument('-q', '--quiet', action='store_true',
                              help='Suppress progress output')

    # Export command
    export_parser = subparsers.add_parser('export', help='Export database to JSONL')
    export_parser.add_argument('db_file', help='Database file')
    export_parser.add_argument('output_file', help='Output JSONL file')
    export_parser.add_argument('-k', '--kind', type=int, help='Filter by event kind')
    export_parser.add_argument('-a', '--author', help='Filter by author pubkey')
    export_parser.add_argument('-l', '--limit', type=int, help='Limit number of events')
    export_parser.add_argument('-q', '--quiet', action='store_true',
                              help='Suppress progress output')

    # Fetch command
    fetch_parser = subparsers.add_parser('fetch', help='Fetch from relays and import')
    fetch_parser.add_argument('db_file', help='Database file')
    fetch_parser.add_argument('relays', nargs='+', help='Relay URLs')
    fetch_parser.add_argument('-k', '--kind', type=int, help='Filter by event kind')
    fetch_parser.add_argument('-a', '--author', help='Filter by author pubkey')
    fetch_parser.add_argument('-l', '--limit', type=int, default=100,
                             help='Maximum events to fetch (default: 100)')
    fetch_parser.add_argument('--since', type=int, help='Fetch events after timestamp')
    fetch_parser.add_argument('--until', type=int, help='Fetch events before timestamp')
    fetch_parser.add_argument('-q', '--quiet', action='store_true',
                             help='Suppress progress output')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        if args.command == 'import':
            count = import_jsonl(
                args.jsonl_file,
                args.db_file,
                skip_errors=not args.strict,
                verbose=not args.quiet
            )
            sys.exit(0 if count > 0 else 1)

        elif args.command == 'export':
            count = export_jsonl(
                args.db_file,
                args.output_file,
                kind=args.kind,
                author=args.author,
                limit=args.limit,
                verbose=not args.quiet
            )
            sys.exit(0 if count > 0 else 1)

        elif args.command == 'fetch':
            count = fetch_and_import(
                args.relays,
                args.db_file,
                kind=args.kind,
                author=args.author,
                limit=args.limit,
                since=args.since,
                until=args.until,
                verbose=not args.quiet
            )
            sys.exit(0 if count > 0 else 1)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
