"""
Feed scheduler for nostr-feeds enricher

Manages periodic feed updates with configurable intervals.
"""

import time
import threading
import sys
from typing import Dict, Optional, Callable
from datetime import datetime

from .config import Config
from .db import DatabaseManager
from .feed_fetcher import FeedFetcher
from .connectivity import is_online


class FeedScheduler:
    """
    Schedules periodic feed updates based on refresh intervals.

    Handles:
    - Different refresh rates per feed (30s for timeline, days for wikis)
    - Background execution with threading
    - Graceful start/stop
    - Error handling and logging
    """

    def __init__(
        self,
        config: Config,
        db_manager: DatabaseManager,
        log_callback: Optional[Callable[[str], None]] = None
    ):
        """
        Initialize feed scheduler.

        Args:
            config: Configuration object
            db_manager: Database manager
            log_callback: Optional callback for log messages (defaults to stderr)
        """
        self.config = config
        self.db_manager = db_manager
        self.fetcher = FeedFetcher(config, db_manager)

        self._log_callback = log_callback or self._default_log
        self._running = False
        self._threads: Dict[str, threading.Thread] = {}
        self._stop_events: Dict[str, threading.Event] = {}

    def start(self, feeds: Optional[list[str]] = None) -> None:
        """
        Start scheduler for specified feeds (or all feeds).

        Args:
            feeds: List of feed names to schedule (defaults to all feeds with refresh_interval)

        Examples:
            >>> scheduler = FeedScheduler(config, db_manager)
            >>> scheduler.start(['timeline', 'global'])
            >>> # Runs in background...
            >>> scheduler.stop()
        """
        if self._running:
            self._log("Scheduler already running")
            return

        self._running = True

        # Determine which feeds to schedule
        if feeds is None:
            feeds = [
                name for name, feed_config in self.config.feeds.items()
                if feed_config.get('refresh_interval')
            ]

        self._log(f"Starting scheduler for {len(feeds)} feed(s): {', '.join(feeds)}")

        # Start a thread for each feed
        for feed_name in feeds:
            feed_config = self.config.feeds.get(feed_name)
            if not feed_config:
                self._log(f"Warning: Unknown feed '{feed_name}'")
                continue

            refresh_interval = feed_config.get('refresh_interval')
            if not refresh_interval:
                self._log(f"Skipping '{feed_name}' (no refresh_interval)")
                continue

            # Create stop event for this feed
            stop_event = threading.Event()
            self._stop_events[feed_name] = stop_event

            # Create and start thread
            thread = threading.Thread(
                target=self._feed_loop,
                args=(feed_name, refresh_interval, stop_event),
                daemon=True,
                name=f"feed-{feed_name}"
            )
            self._threads[feed_name] = thread
            thread.start()

            self._log(f"Started '{feed_name}' (every {refresh_interval}s)")

    def stop(self, feeds: Optional[list[str]] = None) -> None:
        """
        Stop scheduler for specified feeds (or all feeds).

        Args:
            feeds: List of feed names to stop (defaults to all running feeds)

        Examples:
            >>> scheduler.stop(['timeline'])  # Stop specific feed
            >>> scheduler.stop()  # Stop all feeds
        """
        if not self._running:
            self._log("Scheduler not running")
            return

        # Determine which feeds to stop
        if feeds is None:
            feeds = list(self._threads.keys())

        self._log(f"Stopping scheduler for {len(feeds)} feed(s): {', '.join(feeds)}")

        # Signal threads to stop
        for feed_name in feeds:
            stop_event = self._stop_events.get(feed_name)
            if stop_event:
                stop_event.set()

        # Wait for threads to finish
        for feed_name in feeds:
            thread = self._threads.get(feed_name)
            if thread and thread.is_alive():
                thread.join(timeout=5)
                self._log(f"Stopped '{feed_name}'")

            # Clean up
            self._threads.pop(feed_name, None)
            self._stop_events.pop(feed_name, None)

        if not self._threads:
            self._running = False
            self._log("Scheduler stopped")

    def _feed_loop(self, feed_name: str, interval: int, stop_event: threading.Event) -> None:
        """
        Main loop for feed updates.

        Args:
            feed_name: Feed to update
            interval: Update interval in seconds
            stop_event: Event to signal stop
        """
        self._log(f"[{feed_name}] Starting update loop (interval={interval}s)")

        # Immediate first fetch
        self._fetch_feed(feed_name)

        while not stop_event.is_set():
            # Wait for interval or stop signal
            if stop_event.wait(timeout=interval):
                break

            # Fetch updates
            self._fetch_feed(feed_name)

        self._log(f"[{feed_name}] Update loop stopped")

    def _fetch_feed(self, feed_name: str) -> None:
        """
        Fetch updates for a feed with error handling.

        Args:
            feed_name: Feed to fetch
        """
        try:
            timestamp = datetime.now().strftime('%H:%M:%S')
            self._log(f"[{feed_name}] Fetching updates... ({timestamp})")

            count = self.fetcher.fetch_feed(feed_name, limit=100)

            if count > 0:
                self._log(f"[{feed_name}] ✓ Fetched {count} new event(s)")
            else:
                self._log(f"[{feed_name}] No new events")

        except Exception as e:
            self._log(f"[{feed_name}] ✗ Error: {e}")

    def status(self) -> Dict[str, Dict[str, any]]:
        """
        Get scheduler status.

        Returns:
            Dictionary with status per feed:
            {
                'timeline': {
                    'running': True,
                    'interval': 30,
                    'last_fetch': 1234567890
                },
                ...
            }

        Examples:
            >>> status = scheduler.status()
            >>> print(status['timeline']['running'])
            True
        """
        status = {}

        for feed_name in self.config.feeds:
            thread = self._threads.get(feed_name)
            feed_config = self.config.feeds[feed_name]

            status[feed_name] = {
                'running': thread is not None and thread.is_alive(),
                'interval': feed_config.get('refresh_interval'),
                'last_fetch': self.fetcher.get_last_fetch_time(feed_name)
            }

        return status

    def is_running(self, feed_name: Optional[str] = None) -> bool:
        """
        Check if scheduler is running.

        Args:
            feed_name: Check specific feed (checks any feed if None)

        Returns:
            True if running

        Examples:
            >>> scheduler.is_running('timeline')
            True
            >>> scheduler.is_running()  # Check if any feed is running
            True
        """
        if feed_name:
            thread = self._threads.get(feed_name)
            return thread is not None and thread.is_alive()
        else:
            return self._running and any(
                thread.is_alive() for thread in self._threads.values()
            )

    def _log(self, message: str) -> None:
        """
        Log message using callback or default.

        Args:
            message: Log message
        """
        self._log_callback(message)

    @staticmethod
    def _default_log(message: str) -> None:
        """
        Default logging to stderr.

        Args:
            message: Log message
        """
        print(f"[feed-scheduler] {message}", file=sys.stderr, flush=True)


def run_daemon(config_path: str, feeds: Optional[list[str]] = None) -> None:
    """
    Run feed scheduler as a daemon process.

    Args:
        config_path: Path to config.toml
        feeds: List of feeds to schedule (defaults to all)

    Examples:
        >>> # Run in background
        >>> run_daemon('config.toml', ['timeline', 'global'])
    """
    from .config import Config
    from .db import DatabaseManager

    # Load config
    config = Config.load(config_path)

    # Initialize database manager
    db_dir = config.global_config.get('db_dir', '~/.local/share/nostr-feeds')
    db_manager = DatabaseManager(db_dir)

    # Create and start scheduler
    scheduler = FeedScheduler(config, db_manager)

    try:
        scheduler.start(feeds)

        # Keep daemon running
        print("Feed scheduler daemon started. Press Ctrl+C to stop.", file=sys.stderr)

        while scheduler.is_running():
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nStopping scheduler...", file=sys.stderr)
        scheduler.stop()
        db_manager.close_all()
        print("Scheduler stopped", file=sys.stderr)


if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m enricher.feed_scheduler <config.toml> [feed1 feed2 ...]", file=sys.stderr)
        sys.exit(1)

    config_path = sys.argv[1]
    feeds = sys.argv[2:] if len(sys.argv) > 2 else None

    run_daemon(config_path, feeds)
