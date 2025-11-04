#!/usr/bin/env python3
"""
nostr-feeds fetch command - Fetch events from relays to populate databases
"""

import sys
import argparse

from .config import Config
from .db import DatabaseManager
from .feed_fetcher import FeedFetcher


def main():
    """Main entry point for fetch command."""
    parser = argparse.ArgumentParser(
        description='nostr-feeds fetch - Populate feeds from relays using nak'
    )

    parser.add_argument(
        '--config',
        '-c',
        default='~/.config/nostr-feeds/config.toml',
        help='Path to config.toml file'
    )

    # What to fetch (mutually exclusive)
    fetch_group = parser.add_mutually_exclusive_group(required=True)

    fetch_group.add_argument(
        '--feed',
        '-f',
        help='Fetch events for feed (e.g., timeline, global)'
    )

    fetch_group.add_argument(
        '--profile',
        '-p',
        help='Fetch profile events for user (npub or hex)'
    )

    fetch_group.add_argument(
        '--follows',
        action='store_true',
        help='Fetch kind 3 follow list for default user'
    )

    # Common options
    parser.add_argument(
        '--limit',
        '-l',
        type=int,
        default=100,
        help='Maximum events to fetch (default: 100)'
    )

    parser.add_argument(
        '--since',
        '-s',
        type=int,
        help='Only fetch events after this Unix timestamp'
    )

    parser.add_argument(
        '--kinds',
        '-k',
        type=int,
        nargs='+',
        help='Event kinds to fetch for profile (default: 0 1)'
    )

    parser.add_argument(
        '--relays',
        '-r',
        nargs='+',
        help='Relay URLs (defaults to user relays from config)'
    )

    args = parser.parse_args()

    # Load configuration
    try:
        config = Config.load(args.config)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        print(f"Create config at: {args.config}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        sys.exit(1)

    # Initialize database manager and fetcher
    db_dir = config.get_db_dir()
    db_manager = DatabaseManager(db_dir)
    fetcher = FeedFetcher(config, db_manager)

    try:
        if args.feed:
            # Fetch feed
            print(f"Fetching feed: {args.feed}")

            count = fetcher.fetch_feed(
                args.feed,
                limit=args.limit,
                since=args.since,
                relays=args.relays
            )

            print(f"✓ Fetched {count} new event(s)")

        elif args.profile:
            # Fetch profile
            print(f"Fetching profile: {args.profile}")

            kinds = args.kinds or [0, 1]

            count = fetcher.fetch_profile_events(
                args.profile,
                kinds=kinds,
                limit=args.limit,
                relays=args.relays
            )

            print(f"✓ Fetched {count} event(s)")

        elif args.follows:
            # Fetch follows list
            print("Fetching follow list...")

            count = fetcher.fetch_follows_list(relays=args.relays)

            print(f"✓ Following {count} user(s)")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)

    finally:
        db_manager.close_all()


if __name__ == '__main__':
    main()
