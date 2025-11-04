"""
Unified feed daemon for nostr-feeds enricher

Coordinates all fetch strategies: stream, periodic, on_load, manual.
"""

import sys
import time
from typing import Dict, List, Optional, Any

from .config import Config, npub_to_hex
from .db import DatabaseManager
from .relay_resolver import RelayResolver
from .stream_manager import StreamManager
from .feed_scheduler import FeedScheduler
from .follows import FollowsCache


class FeedDaemon:
    """
    Unified daemon for all fetch strategies.

    Handles:
    - stream: Persistent nak --stream connections (StreamManager)
    - periodic: Polling at intervals (FeedScheduler)
    - on_load: Fetch when view opened (handled externally)
    - manual: Only on user refresh (handled externally)
    """

    def __init__(self, config: Config, db_manager: DatabaseManager):
        """
        Initialize feed daemon.

        Args:
            config: Configuration object
            db_manager: Database manager
        """
        self.config = config
        self.db_manager = db_manager

        # Create subsystems
        self.relay_resolver = RelayResolver(config, db_manager)
        self.stream_manager = StreamManager(db_manager)
        self.periodic_scheduler = FeedScheduler(config, db_manager)

        self._running_feeds: Dict[str, str] = {}  # feed_name -> strategy

    def start(self, feeds: Optional[List[str]] = None):
        """
        Start daemon for specified feeds.

        Args:
            feeds: List of feed names (defaults to all feeds with fetch_strategy)
        """
        if feeds is None:
            feeds = [
                name for name, feed_config in self.config.feeds.items()
                if feed_config.get('fetch_strategy') in ['stream', 'periodic']
            ]

        if not feeds:
            print("No feeds configured with fetch_strategy", file=sys.stderr)
            return

        print(f"Starting daemon for {len(feeds)} feed(s)...", file=sys.stderr)

        for feed_name in feeds:
            try:
                self._start_feed(feed_name)
            except Exception as e:
                print(f"Error starting '{feed_name}': {e}", file=sys.stderr)

        print("Daemon started", file=sys.stderr)

    def _start_feed(self, feed_name: str):
        """Start a single feed based on its strategy."""
        feed_config = self.config.get_feed(feed_name)
        if not feed_config:
            raise ValueError(f"Unknown feed: {feed_name}")

        strategy = feed_config.get('fetch_strategy', 'periodic')

        if strategy == 'stream':
            self._start_stream(feed_name, feed_config)
        elif strategy == 'periodic':
            self._start_periodic(feed_name, feed_config)
        elif strategy in ['on_load', 'manual']:
            print(f"'{feed_name}': {strategy} mode (no background process)", file=sys.stderr)
        else:
            raise ValueError(f"Unknown fetch_strategy: {strategy}")

        self._running_feeds[feed_name] = strategy

    def _start_stream(self, feed_name: str, feed_config: Dict[str, Any]):
        """Start streaming for feed."""
        print(f"[{feed_name}] Starting stream...", file=sys.stderr)

        relay_mode = feed_config.get('relay_mode', 'general')

        # Resolve relays
        relay_resolution = self._resolve_relays(feed_name, feed_config, relay_mode)

        # Start stream
        self.stream_manager.start_feed_stream(feed_name, feed_config, relay_resolution)

        print(f"[{feed_name}] Stream started ({relay_mode} mode)", file=sys.stderr)

    def _start_periodic(self, feed_name: str, feed_config: Dict[str, Any]):
        """Start periodic polling for feed."""
        print(f"[{feed_name}] Starting periodic updates...", file=sys.stderr)

        interval = feed_config.get('refresh_interval')
        if not interval:
            raise ValueError(f"'{feed_name}': periodic strategy requires refresh_interval")

        # Use existing FeedScheduler
        self.periodic_scheduler.start([feed_name])

        print(f"[{feed_name}] Periodic updates started (every {interval}s)", file=sys.stderr)

    def _resolve_relays(self, feed_name: str, feed_config: Dict, relay_mode: str):
        """Resolve relays for a feed."""
        # Get user pubkey if needed
        user_pubkey = None
        if relay_mode == 'inbox':
            default_npub = self.config.global_config.get('default_npub')
            if default_npub:
                user_pubkey = npub_to_hex(default_npub)

        # Get authors if feed filters by follows
        authors = None
        if feed_config.get('filter_by_follows') and relay_mode == 'outbox':
            authors = self._get_feed_authors(feed_config)

        # Resolve
        return self.relay_resolver.resolve(
            relay_mode,
            feed_config=feed_config,
            user_pubkey=user_pubkey,
            authors=authors
        )

    def _get_feed_authors(self, feed_config: Dict) -> List[str]:
        """Get list of authors for a feed (from follows)."""
        default_npub = self.config.global_config.get('default_npub')
        if not default_npub:
            return []

        # Get follow list from database
        try:
            # Try timeline.db first
            timeline_db = self.db_manager.get_connection('timeline.db')
            follows_cache = FollowsCache(timeline_db)
            follows = follows_cache.get_follows(default_npub)

            # Include self
            user_hex = npub_to_hex(default_npub)
            if user_hex not in follows:
                follows.add(user_hex)

            return list(follows)

        except FileNotFoundError:
            print("Warning: timeline.db not found, no follow list available", file=sys.stderr)
            return []

    def stop(self, feeds: Optional[List[str]] = None):
        """
        Stop daemon for specified feeds.

        Args:
            feeds: List of feed names (defaults to all running feeds)
        """
        if feeds is None:
            feeds = list(self._running_feeds.keys())

        print(f"Stopping {len(feeds)} feed(s)...", file=sys.stderr)

        for feed_name in feeds:
            try:
                self._stop_feed(feed_name)
            except Exception as e:
                print(f"Error stopping '{feed_name}': {e}", file=sys.stderr)

        print("Daemon stopped", file=sys.stderr)

    def _stop_feed(self, feed_name: str):
        """Stop a single feed."""
        if feed_name not in self._running_feeds:
            return

        strategy = self._running_feeds[feed_name]

        if strategy == 'stream':
            self.stream_manager.stop_feed(feed_name)
        elif strategy == 'periodic':
            self.periodic_scheduler.stop([feed_name])

        del self._running_feeds[feed_name]
        print(f"[{feed_name}] Stopped", file=sys.stderr)

    def status(self) -> Dict[str, Dict[str, Any]]:
        """
        Get status of all feeds.

        Returns:
            Dictionary mapping feed names to status info
        """
        status = {}

        # Get stream status
        stream_status = self.stream_manager.get_status()
        for feed_name, info in stream_status.items():
            status[feed_name] = {
                'strategy': 'stream',
                'running': info['running'],
                'details': info
            }

        # Get periodic status
        periodic_status = self.periodic_scheduler.status()
        for feed_name, info in periodic_status.items():
            if feed_name not in status:  # Don't override stream status
                status[feed_name] = {
                    'strategy': 'periodic',
                    'running': info['running'],
                    'details': info
                }

        # Add configured feeds that aren't running
        for feed_name in self.config.feeds:
            if feed_name not in status:
                feed_config = self.config.feeds[feed_name]
                strategy = feed_config.get('fetch_strategy', 'periodic')
                status[feed_name] = {
                    'strategy': strategy,
                    'running': False,
                    'details': {}
                }

        return status

    def is_running(self, feed_name: Optional[str] = None) -> bool:
        """
        Check if daemon is running.

        Args:
            feed_name: Check specific feed (checks any feed if None)

        Returns:
            True if running
        """
        if feed_name:
            return feed_name in self._running_feeds
        else:
            return len(self._running_feeds) > 0


def run_daemon(config_path: str, feeds: Optional[List[str]] = None):
    """
    Run feed daemon (convenience function).

    Args:
        config_path: Path to config.toml
        feeds: List of feeds to start (defaults to all with fetch_strategy)
    """
    from .config import Config
    from .db import DatabaseManager

    # Load config
    config = Config.load(config_path)

    # Initialize database manager
    db_dir = config.global_config.get('db_dir', '~/.local/share/nostr-feeds')
    db_manager = DatabaseManager(db_dir)

    # Create and start daemon
    daemon = FeedDaemon(config, db_manager)

    try:
        daemon.start(feeds)

        print("\nFeed daemon running. Press Ctrl+C to stop.\n", file=sys.stderr)

        # Keep daemon running
        while daemon.is_running():
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nStopping daemon...", file=sys.stderr)
        daemon.stop()
        db_manager.close_all()
        print("Daemon stopped", file=sys.stderr)


if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m enricher.feed_daemon <config.toml> [feed1 feed2 ...]", file=sys.stderr)
        sys.exit(1)

    config_path = sys.argv[1]
    feeds = sys.argv[2:] if len(sys.argv) > 2 else None

    run_daemon(config_path, feeds)
