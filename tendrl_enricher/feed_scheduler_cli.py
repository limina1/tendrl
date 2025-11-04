#!/usr/bin/env python3
"""
nostr-feeds daemon command - Manage background feed scheduler
"""

import sys
import argparse
import time

from .config import Config
from .db import DatabaseManager
from .feed_scheduler import FeedScheduler


def main():
    """Main entry point for daemon command."""
    parser = argparse.ArgumentParser(
        description='nostr-feeds daemon - Manage background feed scheduler'
    )

    parser.add_argument(
        '--config',
        '-c',
        default='~/.config/nostr-feeds/config.toml',
        help='Path to config.toml file'
    )

    # Daemon action
    parser.add_argument(
        'action',
        choices=['start', 'status'],
        help='Daemon action: start (run scheduler), status (show feed status)'
    )

    # Feed selection
    parser.add_argument(
        '--feeds',
        '-f',
        nargs='+',
        help='Feeds to schedule (defaults to all feeds with refresh_interval)'
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

    # Initialize database manager and scheduler
    db_dir = config.get_db_dir()
    db_manager = DatabaseManager(db_dir)
    scheduler = FeedScheduler(config, db_manager)

    if args.action == 'start':
        # Start daemon
        try:
            scheduler.start(feeds=args.feeds)

            print("Feed scheduler daemon started. Press Ctrl+C to stop.", file=sys.stderr)
            print(f"Monitoring: {', '.join(args.feeds or ['all feeds'])}", file=sys.stderr)
            print()

            # Keep running
            while scheduler.is_running():
                time.sleep(1)

        except KeyboardInterrupt:
            print("\nStopping scheduler...", file=sys.stderr)
            scheduler.stop()
            db_manager.close_all()
            print("Scheduler stopped", file=sys.stderr)

        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            scheduler.stop()
            db_manager.close_all()
            sys.exit(1)

    elif args.action == 'status':
        # Show status
        status = scheduler.status()

        print("Feed scheduler status:\n")

        for feed_name, feed_status in status.items():
            running = feed_status['running']
            interval = feed_status['interval']
            last_fetch = feed_status['last_fetch']

            status_icon = "✓" if running else "✗"
            running_text = "running" if running else "stopped"

            print(f"  {status_icon} {feed_name}: {running_text}")

            if interval:
                print(f"      Interval: {interval}s")
            else:
                print(f"      Interval: not configured")

            if last_fetch > 0:
                import datetime
                dt = datetime.datetime.fromtimestamp(last_fetch)
                print(f"      Last fetch: {dt.strftime('%Y-%m-%d %H:%M:%S')}")
            else:
                print(f"      Last fetch: never")

            print()

        db_manager.close_all()


if __name__ == '__main__':
    main()
