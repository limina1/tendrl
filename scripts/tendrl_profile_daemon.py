#!/usr/bin/env python3
"""
Tendrl Profile Daemon

Monitors events in nostrdb and fetches missing profiles from relays.

For every event:
1. Check if author's profile (kind 0) exists in DB
2. If not, fetch from relays
3. Store in nostrdb

This ensures profiles are always available for enrichment.

Usage:
    python tendrl_profile_daemon.py --config tendrl.toml
"""

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Set, List
from datetime import datetime
import signal
import sys

# Try importing toml - required for reading config
try:
    import toml
    TOML_AVAILABLE = True
except ImportError:
    TOML_AVAILABLE = False


class ProfileDaemon:
    """Fetches missing profiles for all events in nostrdb"""

    def __init__(self, config_path: str, poll_interval: int = 60):
        if not TOML_AVAILABLE:
            print("Error: toml library not found. Install with: pip install toml")
            sys.exit(1)

        self.config_path = Path(config_path)
        self.poll_interval = poll_interval
        self.running = True
        self.seen_pubkeys: Set[str] = set()
        self.fetched_pubkeys: Set[str] = set()

        # Load profile fetcher relays from config
        self.profile_relays = self._load_profile_relays()

        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, sig, frame):
        """Handle shutdown signals"""
        print(f"\n[{self._timestamp()}] Shutting down...")
        self.running = False
        sys.exit(0)

    def _timestamp(self) -> str:
        """Get formatted timestamp"""
        return datetime.now().strftime("%H:%M:%S")

    def _load_profile_relays(self) -> List[str]:
        """Load profile fetcher relays from config"""
        try:
            with open(self.config_path, 'r') as f:
                config = toml.load(f)

            # Try to get profile_fetchers.relays from config
            if 'profile_fetchers' in config and 'relays' in config['profile_fetchers']:
                relays = config['profile_fetchers']['relays']
                if relays:
                    print(f"Loaded {len(relays)} profile fetcher relays from config")
                    return relays

            # Fallback to default relays if not in config
            print("Warning: No profile_fetchers.relays in config, using defaults")
            return [
                "wss://purplepag.es",
                "wss://indexer.coracle.social",
                "wss://user.kindpag.es",
                "wss://relay.nostr.band",
                "wss://relay.damus.io"
            ]

        except Exception as e:
            print(f"Error loading config: {e}")
            print("Using default profile fetcher relays")
            return [
                "wss://purplepag.es",
                "wss://indexer.coracle.social",
                "wss://user.kindpag.es",
                "wss://relay.nostr.band",
                "wss://relay.damus.io"
            ]

    def get_feeds_from_config(self) -> list:
        """Get list of feed names from config"""
        try:
            with open(self.config_path, 'r') as f:
                config = toml.load(f)

            if 'feed' not in config:
                return []

            return list(config['feed'].keys())
        except Exception as e:
            print(f"[{self._timestamp()}] Error reading feeds from config: {e}")
            return []

    def get_all_authors(self) -> Set[str]:
        """Get all unique author pubkeys from configured feeds"""
        try:
            # Get feed names from config
            feeds = self.get_feeds_from_config()

            if not feeds:
                print(f"[{self._timestamp()}] No feeds found in config, falling back to all kind-1")
                feeds = [None]  # Will query all kind-1 events

            pubkeys = set()

            for feed_name in feeds:
                if feed_name:
                    # Query specific feed
                    result = subprocess.run(
                        [
                            './target/release/tendrl_query',
                            '--feed', feed_name,
                            '--limit', '500',  # Reasonable limit per feed
                            '--config', str(self.config_path)
                        ],
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
                else:
                    # Fallback: query all kind-1
                    result = subprocess.run(
                        [
                            './target/release/tendrl_query',
                            '--kinds', '1',
                            '--limit', '500',
                            '--config', str(self.config_path)
                        ],
                        capture_output=True,
                        text=True,
                        timeout=30
                    )

                if result.returncode != 0:
                    continue

                # Extract unique pubkeys from this feed
                for line in result.stdout.strip().split('\n'):
                    if line:
                        try:
                            event = json.loads(line)
                            pubkeys.add(event['pubkey'])
                        except:
                            continue

            return pubkeys

        except Exception as e:
            print(f"[{self._timestamp()}] Error getting authors: {e}")
            return set()

    def check_profile_exists(self, pubkey: str) -> bool:
        """Check if profile exists in nostrdb"""
        try:
            result = subprocess.run(
                [
                    './target/release/tendrl_query',
                    '--kinds', '0',
                    '--author', pubkey,
                    '--limit', '1',
                    '--config', str(self.config_path)
                ],
                capture_output=True,
                text=True,
                timeout=5
            )

            return result.returncode == 0 and result.stdout.strip() != ""

        except:
            return False

    def fetch_profile_from_relay(self, pubkey: str) -> bool:
        """Fetch profile from relay using nak"""
        try:
            print(f"[{self._timestamp()}] Fetching profile for {pubkey[:8]}... from relays")

            # Build nak command with relays from config
            nak_cmd = [
                'nak', 'req',
                '-k', '0',
                '-a', pubkey,
                '-l', '1',
            ] + self.profile_relays

            # Use nak to fetch from profile indexers (specialized for kind-0)
            result = subprocess.run(
                nak_cmd,
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0 or not result.stdout.strip():
                print(f"[{self._timestamp()}] No profile found on relays for {pubkey[:8]}")
                return False

            # Get the profile event JSON
            profile_event = result.stdout.strip()

            # Store in nostrdb using tendrl_ingest
            ingest_result = subprocess.run(
                ['./target/release/tendrl_ingest'],
                input=profile_event,
                capture_output=True,
                text=True,
                timeout=5
            )

            if ingest_result.returncode == 0:
                print(f"[{self._timestamp()}] ✓ Fetched and stored profile for {pubkey[:8]}")
                return True
            else:
                print(f"[{self._timestamp()}] ✗ Failed to store profile for {pubkey[:8]}: {ingest_result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            print(f"[{self._timestamp()}] Timeout fetching profile for {pubkey[:8]}")
            return False
        except Exception as e:
            print(f"[{self._timestamp()}] Error fetching profile for {pubkey[:8]}: {e}")
            return False

    def process_missing_profiles(self):
        """Find and fetch missing profiles"""
        # Get all author pubkeys from configured feeds
        feeds = self.get_feeds_from_config()
        print(f"[{self._timestamp()}] Scanning authors from {len(feeds)} feed(s): {', '.join(feeds)}")

        authors = self.get_all_authors()

        if not authors:
            print(f"[{self._timestamp()}] No authors found")
            return

        print(f"[{self._timestamp()}] Found {len(authors)} unique authors across all feeds")

        # Find authors without profiles
        missing = []
        for pubkey in authors:
            if pubkey in self.fetched_pubkeys:
                continue  # Already tried fetching

            if not self.check_profile_exists(pubkey):
                missing.append(pubkey)

        if not missing:
            print(f"[{self._timestamp()}] All profiles present ✓")
            return

        print(f"[{self._timestamp()}] Missing {len(missing)} profiles")

        # Fetch missing profiles (with rate limiting)
        success_count = 0
        for i, pubkey in enumerate(missing[:20]):  # Limit to 20 per batch
            print(f"[{self._timestamp()}] [{i+1}/{min(20, len(missing))}] Fetching {pubkey[:8]}...")

            if self.fetch_profile_from_relay(pubkey):
                success_count += 1
                self.fetched_pubkeys.add(pubkey)

            # Rate limit: 1 request per 2 seconds
            if i < len(missing) - 1:
                time.sleep(2)

        print(f"[{self._timestamp()}] ✓ Fetched {success_count}/{min(20, len(missing))} profiles")

    def run(self):
        """Main daemon loop"""
        print(f"\n{'='*60}")
        print(f"Tendrl Profile Daemon")
        print(f"{'='*60}\n")
        print(f"[{self._timestamp()}] Config: {self.config_path}")
        print(f"[{self._timestamp()}] Poll interval: {self.poll_interval}s")
        print(f"[{self._timestamp()}] Profile relays: {len(self.profile_relays)} configured")
        for relay in self.profile_relays:
            print(f"[{self._timestamp()}]   - {relay}")
        print(f"[{self._timestamp()}] Press Ctrl+C to stop\n")

        poll_count = 0
        while self.running:
            poll_count += 1
            print(f"\n[{self._timestamp()}] Poll #{poll_count}")

            self.process_missing_profiles()

            if self.running:
                print(f"[{self._timestamp()}] Sleeping {self.poll_interval}s...")
                time.sleep(self.poll_interval)


def main():
    parser = argparse.ArgumentParser(
        description="Tendrl Profile Daemon - Auto-fetch missing profiles"
    )
    parser.add_argument(
        '--config',
        default='./tendrl/tendrl_enrichment_test.toml',
        help='Tendrl config path'
    )
    parser.add_argument(
        '--poll-interval',
        type=int,
        default=60,
        help='Seconds between profile checks (default: 60)'
    )

    args = parser.parse_args()

    daemon = ProfileDaemon(
        config_path=args.config,
        poll_interval=args.poll_interval
    )

    daemon.run()


if __name__ == '__main__':
    main()


"""
Example usage:

# Run profile daemon
python tendrl_profile_daemon.py --config tendrl/tendrl_enrichment_test.toml

# With custom poll interval (check every 5 minutes)
python tendrl_profile_daemon.py --poll-interval 300

# Run in background
nohup python tendrl_profile_daemon.py > profile_daemon.log 2>&1 &

# Run alongside main daemon
# Terminal 1:
python tendrl_render_daemon.py

# Terminal 2:
python tendrl_profile_daemon.py

# Or use tmux/screen to run both
tmux new-session -d -s tendrl 'python tendrl_render_daemon.py'
tmux split-window -t tendrl 'python tendrl_profile_daemon.py'
tmux attach -t tendrl
"""
