#!/usr/bin/env python3
"""
Tendrl Rendering Daemon

Continuous feed fetching + auto-rendering to HTML.
Watches for new events and regenerates pages automatically.

Features:
- Runs tendrl_daemon in background
- Runs profile_daemon in background (DEFAULT PATTERN - auto-fetches missing profiles)
- Polls nostrdb for new events
- Auto-regenerates HTML when new events arrive
- Backfilling on startup
- Live reload support
- Automatic profile enrichment from relays

Usage:
    python tendrl_render_daemon.py --config tendrl.toml --output-dir public/
"""

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Dict, Set, List
from datetime import datetime
import signal
import sys

from tendrl_render import TendrlRenderer


class RenderDaemon:
    """Daemon that continuously fetches and renders Nostr feeds"""

    def __init__(
        self,
        config_path: str,
        output_dir: str = "public",
        poll_interval: int = 30,
    ):
        self.config_path = Path(config_path)
        self.output_dir = Path(output_dir)
        self.poll_interval = poll_interval
        self.renderer = TendrlRenderer()

        self.tendrl_daemon_process = None
        self.profile_daemon_process = None
        self.running = True
        self.last_event_timestamps: Dict[str, int] = {}
        self._profile_poll_interval = 120  # Default, can be overridden

        # Ensure output directory exists
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, sig, frame):
        """Handle shutdown signals gracefully"""
        print(f"\n[{self._timestamp()}] Received signal {sig}, shutting down...")
        self.running = False
        if self.tendrl_daemon_process:
            self.tendrl_daemon_process.terminate()
        if self.profile_daemon_process:
            self.profile_daemon_process.terminate()
        sys.exit(0)

    def _timestamp(self) -> str:
        """Get formatted timestamp"""
        return datetime.now().strftime("%H:%M:%S")

    def start_tendrl_daemon(self):
        """Start tendrl_daemon in background"""
        print(f"[{self._timestamp()}] Starting tendrl_daemon...")

        daemon_path = "./target/release/tendrl_daemon"

        if not Path(daemon_path).exists():
            print(f"[{self._timestamp()}] ERROR: {daemon_path} not found")
            print(f"[{self._timestamp()}] Build it first: cargo build --release -p tendrl_daemon")
            sys.exit(1)

        try:
            self.tendrl_daemon_process = subprocess.Popen(
                [daemon_path, "--config", str(self.config_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            print(f"[{self._timestamp()}] ✓ tendrl_daemon started (PID: {self.tendrl_daemon_process.pid})")
            print(f"[{self._timestamp()}] Waiting 5s for daemon to connect to relays...")
            time.sleep(5)

        except Exception as e:
            print(f"[{self._timestamp()}] ERROR starting daemon: {e}")
            sys.exit(1)

    def start_profile_daemon(self, poll_interval: int = 120):
        """Start profile daemon in background"""
        print(f"[{self._timestamp()}] Starting profile daemon...")

        profile_daemon_path = "./tendrl_profile_daemon.py"

        if not Path(profile_daemon_path).exists():
            print(f"[{self._timestamp()}] WARNING: {profile_daemon_path} not found")
            print(f"[{self._timestamp()}] Profile auto-fetching will be disabled")
            return

        try:
            self.profile_daemon_process = subprocess.Popen(
                [
                    "python3",
                    profile_daemon_path,
                    "--config", str(self.config_path),
                    "--poll-interval", str(poll_interval)
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            print(f"[{self._timestamp()}] ✓ Profile daemon started (PID: {self.profile_daemon_process.pid})")
            print(f"[{self._timestamp()}] Profile daemon will check for missing profiles every {poll_interval}s")

        except Exception as e:
            print(f"[{self._timestamp()}] WARNING: Could not start profile daemon: {e}")
            print(f"[{self._timestamp()}] Profile auto-fetching will be disabled")

    def get_feed_list(self) -> List[str]:
        """Parse config to get list of feeds"""
        # Simple TOML parsing - just look for [feed.NAME] sections (not subsections)
        feeds = []
        with open(self.config_path, 'r') as f:
            for line in f:
                if line.startswith('[feed.'):
                    feed_name = line.split('[feed.')[1].split(']')[0]
                    # Only add top-level feed names (not subsections like .pattern, .root, .deps)
                    if '.' not in feed_name:
                        if feed_name not in feeds:
                            feeds.append(feed_name)
        return feeds

    def initial_render(self, feed_name: str):
        """Initial backfill render for a feed"""
        print(f"[{self._timestamp()}] Backfilling {feed_name}...")

        try:
            # FIRST: Fetch profiles for visible authors synchronously
            print(f"[{self._timestamp()}] Fetching profiles for visible authors...")
            fetch_result = subprocess.run(
                ['python3', 'fetch_visible_profiles.py', str(self.config_path), feed_name, '100'],
                capture_output=True,
                text=True,
                timeout=120
            )
            if fetch_result.returncode == 0:
                # Print summary line
                for line in fetch_result.stdout.strip().split('\n'):
                    if '✓ Fetched' in line or 'All authors have profiles' in line:
                        print(f"[{self._timestamp()}] {line}")

            # THEN: Fetch enriched events (now with profiles in DB)
            events = self.renderer.fetch_enriched_feed(
                feed_name=feed_name,
                config_path=str(self.config_path),
                limit=100
            )

            if events:
                # Track latest timestamp
                self.last_event_timestamps[feed_name] = max(
                    e.get('created_at', 0) for e in events
                )

                # Render to HTML
                output_file = self.output_dir / f"{feed_name}.html"
                html = self.renderer.render_feed(
                    events=events,
                    feed_name=feed_name.replace('_', ' ').title(),
                    feed_description=f"{len(events)} enriched notes"
                )

                output_file.write_text(html, encoding='utf-8')

                print(f"[{self._timestamp()}] ✓ Rendered {len(events)} events → {output_file}")

                # Create index.html symlink for main feed
                if feed_name == 'notes_enriched' or not (self.output_dir / 'index.html').exists():
                    index_file = self.output_dir / 'index.html'
                    index_file.write_text(html, encoding='utf-8')
                    print(f"[{self._timestamp()}] ✓ Created index.html")

            else:
                print(f"[{self._timestamp()}] No events found for {feed_name}")

        except Exception as e:
            print(f"[{self._timestamp()}] ERROR rendering {feed_name}: {e}")

    def check_for_updates(self, feed_name: str) -> bool:
        """Check if there are new events for a feed"""
        try:
            events = self.renderer.fetch_enriched_feed(
                feed_name=feed_name,
                config_path=str(self.config_path),
                limit=10  # Just check latest
            )

            if not events:
                return False

            latest_timestamp = max(e.get('created_at', 0) for e in events)
            last_known = self.last_event_timestamps.get(feed_name, 0)

            if latest_timestamp > last_known:
                self.last_event_timestamps[feed_name] = latest_timestamp
                return True

            return False

        except Exception as e:
            print(f"[{self._timestamp()}] ERROR checking {feed_name}: {e}")
            return False

    def render_feed(self, feed_name: str):
        """Render a feed to HTML"""
        try:
            # FIRST: Fetch profiles for visible authors synchronously
            print(f"[{self._timestamp()}] Fetching profiles for visible authors...")
            fetch_result = subprocess.run(
                ['python3', 'fetch_visible_profiles.py', str(self.config_path), feed_name, '100'],
                capture_output=True,
                text=True,
                timeout=120
            )
            if fetch_result.returncode == 0:
                # Print summary line
                for line in fetch_result.stdout.strip().split('\n'):
                    if '✓ Fetched' in line or 'All authors have profiles' in line:
                        print(f"[{self._timestamp()}] {line}")

            # THEN: Fetch enriched events (now with profiles in DB)
            events = self.renderer.fetch_enriched_feed(
                feed_name=feed_name,
                config_path=str(self.config_path),
                limit=100
            )

            if events:
                output_file = self.output_dir / f"{feed_name}.html"
                html = self.renderer.render_feed(
                    events=events,
                    feed_name=feed_name.replace('_', ' ').title(),
                    feed_description=f"{len(events)} enriched notes"
                )

                output_file.write_text(html, encoding='utf-8')

                # Update index if needed
                if feed_name == 'notes_enriched':
                    index_file = self.output_dir / 'index.html'
                    index_file.write_text(html, encoding='utf-8')

                return len(events)

            return 0

        except Exception as e:
            print(f"[{self._timestamp()}] ERROR rendering {feed_name}: {e}")
            return 0

    def copy_static_files(self):
        """Copy static assets to output directory"""
        import shutil

        static_src = Path("static")
        static_dest = self.output_dir / "static"

        if static_src.exists():
            print(f"[{self._timestamp()}] Copying static files...")
            if static_dest.exists():
                shutil.rmtree(static_dest)
            shutil.copytree(static_src, static_dest)
            print(f"[{self._timestamp()}] ✓ Static files copied")

    def run(self):
        """Main daemon loop"""
        print(f"\n{'='*60}")
        print(f"Tendrl Rendering Daemon")
        print(f"{'='*60}\n")

        print(f"[{self._timestamp()}] Config: {self.config_path}")
        print(f"[{self._timestamp()}] Output: {self.output_dir}")
        print(f"[{self._timestamp()}] Poll interval: {self.poll_interval}s\n")

        # Start tendrl_daemon
        self.start_tendrl_daemon()

        # Start profile daemon (DEFAULT pattern for auto-fetching profiles)
        if self._profile_poll_interval > 0:
            self.start_profile_daemon(poll_interval=self._profile_poll_interval)
        else:
            print(f"[{self._timestamp()}] Profile auto-fetching disabled (--profile-poll-interval 0)")

        # Get list of feeds
        feeds = self.get_feed_list()
        print(f"[{self._timestamp()}] Found {len(feeds)} feeds: {', '.join(feeds)}\n")

        # Initial backfill render
        print(f"[{self._timestamp()}] Starting initial backfill...\n")
        for feed_name in feeds:
            self.initial_render(feed_name)

        # Copy static files
        self.copy_static_files()

        print(f"\n[{self._timestamp()}] ✓ Initial backfill complete!")
        print(f"[{self._timestamp()}] Polling for updates every {self.poll_interval}s...")
        print(f"[{self._timestamp()}] Press Ctrl+C to stop\n")

        # Main polling loop
        poll_count = 0
        while self.running:
            time.sleep(self.poll_interval)
            poll_count += 1

            print(f"[{self._timestamp()}] Poll #{poll_count}: Checking for new events...")

            updates_found = False
            for feed_name in feeds:
                if self.check_for_updates(feed_name):
                    print(f"[{self._timestamp()}] ✓ New events in {feed_name}, re-rendering...")
                    count = self.render_feed(feed_name)
                    print(f"[{self._timestamp()}] ✓ Rendered {count} events")
                    updates_found = True

            if not updates_found:
                print(f"[{self._timestamp()}] No new events")

            # Check if daemons are still running
            if self.tendrl_daemon_process and self.tendrl_daemon_process.poll() is not None:
                print(f"[{self._timestamp()}] WARNING: tendrl_daemon stopped, restarting...")
                self.start_tendrl_daemon()

            if (self._profile_poll_interval > 0 and
                self.profile_daemon_process and
                self.profile_daemon_process.poll() is not None):
                print(f"[{self._timestamp()}] WARNING: profile_daemon stopped, restarting...")
                self.start_profile_daemon(poll_interval=self._profile_poll_interval)


def main():
    parser = argparse.ArgumentParser(
        description="Tendrl Rendering Daemon - Continuous fetching + auto-rendering"
    )
    parser.add_argument(
        '--config',
        default='./tendrl_enrichment_test.toml',
        help='Path to tendrl config file'
    )
    parser.add_argument(
        '--output-dir',
        default='public',
        help='Output directory for HTML files'
    )
    parser.add_argument(
        '--poll-interval',
        type=int,
        default=30,
        help='Seconds between polls for new events (default: 30)'
    )
    parser.add_argument(
        '--profile-poll-interval',
        type=int,
        default=120,
        help='Seconds between profile checks (default: 120, 0 to disable)'
    )

    args = parser.parse_args()

    daemon = RenderDaemon(
        config_path=args.config,
        output_dir=args.output_dir,
        poll_interval=args.poll_interval
    )

    # Override profile poll interval if specified
    daemon._profile_poll_interval = args.profile_poll_interval

    daemon.run()


if __name__ == '__main__':
    main()


"""
Example usage:

# Basic usage (includes automatic profile fetching)
python tendrl_render_daemon.py

# Custom config and output
python tendrl_render_daemon.py \\
  --config ~/my_tendrl.toml \\
  --output-dir ~/public_html \\
  --poll-interval 60

# Adjust profile fetching interval (checks every 5 minutes)
python tendrl_render_daemon.py --profile-poll-interval 300

# Disable automatic profile fetching
python tendrl_render_daemon.py --profile-poll-interval 0

# Run in background
nohup python tendrl_render_daemon.py > daemon.log 2>&1 &

# Check logs
tail -f daemon.log

# Stop daemon (also stops all child processes)
kill $(pgrep -f tendrl_render_daemon)

# What runs automatically:
# - tendrl_daemon (fetches events from relays)
# - profile_daemon (auto-fetches missing profiles) [DEFAULT PATTERN]
# - render loop (regenerates HTML on new events)
"""
