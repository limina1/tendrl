#!/usr/bin/env python3
"""
Real-time stream viewer with enrichment and rendering

Streams events from relays and renders them as cards in real-time.
"""

import sys
import signal
from typing import Dict, Any, Optional
from pathlib import Path

from .config import Config
from .db import DatabaseManager, store_profile
from .relay_resolver import RelayResolver
from .stream_manager import StreamManager
from .renderer import Renderer
from .profiles import ProfileCache
from .enricher import Enricher
from .zap_enricher import enrich_zap_receipt
from .repost_enricher import enrich_repost
from .profile_fetcher import fetch_profile_from_relay, fetch_profiles_batch


class StreamViewer:
    """
    Real-time stream viewer with enrichment and rendering.

    Watches a feed stream and renders events as cards as they arrive.
    """

    def __init__(self, config: Config, feed_name: str, render: bool = True):
        """
        Initialize stream viewer.

        Args:
            config: Configuration object
            feed_name: Feed to watch
            render: If True, render cards; if False, output JSONL
        """
        self.config = config
        self.feed_name = feed_name
        self.render_mode = render

        # Get feed config
        self.feed_config = config.get_feed(feed_name)
        if not self.feed_config:
            raise ValueError(f"Feed not found: {feed_name}")

        # Initialize components
        db_dir = config.get_db_dir()
        self.db_manager = DatabaseManager(db_dir)

        # Profile cache (needs connection not path)
        profiles_db_file = config.get_profiles_db()
        profiles_conn = self.db_manager.get_connection(profiles_db_file)
        profile_cache_ttl = config.get_profile_cache_ttl()
        self.profile_cache = ProfileCache(profiles_conn, cache_ttl=profile_cache_ttl)

        # Feed database connection
        feed_db_file = self.feed_config.get('db_file')
        if not feed_db_file:
            raise ValueError(f"Feed '{feed_name}' has no db_file configured")
        self.feed_db = self.db_manager.get_connection(feed_db_file)

        # Create enricher for full stats computation
        self.enricher = Enricher(self.feed_db, profiles_conn, self.profile_cache)

        # Renderer (if render mode)
        if self.render_mode:
            template_dir = config.get_template_dir()
            self.renderer = Renderer(template_dir)

        # Relay resolver
        self.relay_resolver = RelayResolver(config, self.db_manager)

        # Stream manager with custom handler
        self.stream_manager = StreamManager(
            self.db_manager,
            log_callback=self._log
        )

        # Override event handler
        self.stream_manager._handle_event = self._handle_event

        # Event counter
        self.event_count = 0

        # Get relays for profile fetching
        self.default_relays = self._get_default_relays()

    def _log(self, message: str):
        """Log to stderr."""
        print(f"[viewer] {message}", file=sys.stderr, flush=True)

    def _get_default_relays(self) -> list[str]:
        """Get default relays from config for profile fetching."""
        from .config import npub_to_hex

        default_npub = self.config.global_config.get('default_npub')
        if default_npub:
            user_config = self.config.get_user_config(default_npub)
            if user_config and 'relays' in user_config:
                return user_config['relays']

        # Fallback to relays from feed config
        if 'relays' in self.feed_config:
            return self.feed_config['relays']

        # Fallback to common relays
        return ['wss://relay.damus.io', 'wss://nos.lol', 'wss://relay.nostr.band']

    def _fetch_and_cache_profile(self, pubkey: str) -> Optional[Dict[str, Any]]:
        """
        Fetch profile from relay and cache it.

        Args:
            pubkey: Hex pubkey

        Returns:
            Profile dictionary or None
        """
        if not pubkey:
            return None

        # Try to fetch from relay
        profile = fetch_profile_from_relay(pubkey, self.default_relays, timeout=3)

        if profile:
            # Store in cache
            self.profile_cache.set(pubkey, profile)

            # Store in database
            try:
                profiles_db_file = self.config.get_profiles_db()
                profiles_conn = self.db_manager.get_connection(profiles_db_file)
                store_profile(profiles_conn, profile)
            except Exception as e:
                self._log(f"Error storing profile: {e}")

            return profile

        return None

    def _handle_event(self, feed_name: str, feed_config: Dict, event: Dict):
        """
        Handle incoming event: filter, store, enrich with full stats, and render.

        Args:
            feed_name: Feed name
            feed_config: Feed configuration
            event: Raw event from stream
        """
        try:
            self.event_count += 1

            # Post-filter by follows if enabled (for non-outbox modes)
            # Only filter kind 1 notes by author
            # For engagement events (6, 7, 9735), store them all (stats need them)
            kind = event.get('kind')
            if self.follow_filter_authors is not None and kind == 1:
                pubkey = event.get('pubkey')
                if pubkey not in self.follow_filter_authors:
                    # Skip notes from non-followed authors
                    return

            # Store to database first (so stats queries can find related events)
            if feed_config.get('store_events', True):
                from .db import store_event
                store_event(self.feed_db, event)

            # Fetch missing profile if needed
            pubkey = event.get('pubkey')
            if pubkey and not self.profile_cache.get(pubkey):
                self._fetch_and_cache_profile(pubkey)

            # Determine which kinds to render as cards
            # Get the feed's primary kind (what it's designed to show)
            root_config = feed_config.get('root', {})
            root_kind = root_config.get('kind', 1)

            # Skip stats-only events (reactions, zaps) UNLESS this feed is specifically for them
            # Timeline feed: root_kind=1, so skip reactions (7) and zaps (9735)
            # Zaps feed: root_kind=9735, so render zaps but skip reactions
            if kind in [7, 9735] and kind != root_kind:
                # These are collected for stats but not rendered
                return

            # Enrich and render based on kind
            if kind == 6:
                # Repost - always render (users want to see what's being reposted)
                enriched = enrich_repost(
                    event,
                    self.profile_cache,
                    profile_fetcher=self._fetch_and_cache_profile,
                    event_db=self.feed_db
                )
                template = 'repost-card.j2'
            elif kind == 9735:
                # Zap - use zap enricher (only reached if this IS a zaps feed)
                enriched = enrich_zap_receipt(
                    event,
                    self.profile_cache,
                    profile_fetcher=self._fetch_and_cache_profile,
                    event_db=self.feed_db
                )
                template = 'zap-card.j2'
            elif kind == 1:
                # Note - standard enrichment with stats
                enriched = self.enricher.enrich_event(event, root_config, depth=0)
                template = root_config.get('template', 'short-note-card.j2')
            else:
                # Unknown kind - skip rendering
                return

            if self.render_mode:
                # Render with appropriate template
                rendered = self.renderer.render_event(enriched, template)
                print(rendered, flush=True)
            else:
                # Output as JSONL
                import json
                print(json.dumps(enriched), flush=True)

        except Exception as e:
            self._log(f"Error processing event: {e}")

    def start(self):
        """Start streaming and viewing."""
        print("=" * 80, file=sys.stderr)
        print(f"Streaming feed: {self.feed_name}", file=sys.stderr)
        print(f"Mode: {'rendered cards' if self.render_mode else 'JSONL'}", file=sys.stderr)
        print("Press Ctrl+C to stop", file=sys.stderr)
        print("=" * 80, file=sys.stderr)
        print(file=sys.stderr)

        # Resolve relays
        relay_mode = self.feed_config.get('relay_mode', 'general')
        print(f"Relay mode: {relay_mode}", file=sys.stderr)

        # Get user pubkey if needed
        user_pubkey = None
        if relay_mode == 'inbox':
            from .config import npub_to_hex
            default_npub = self.config.global_config.get('default_npub')
            if default_npub:
                user_pubkey = npub_to_hex(default_npub)

        # Get authors if feed filters by follows (works with any relay mode)
        filter_by_follows = self.feed_config.get('filter_by_follows', False)
        print(f"Filter by follows: {filter_by_follows}", file=sys.stderr)

        self.follow_filter_authors = None
        if filter_by_follows:
            self.follow_filter_authors = self._get_feed_authors()
            if self.follow_filter_authors:
                print(f"Filtering to {len(self.follow_filter_authors)} followed authors", file=sys.stderr)
            else:
                print("Warning: Follow filtering enabled but no follows found", file=sys.stderr)

        # For outbox mode, we can pre-filter by passing authors to nak
        # For other modes, we'll post-filter in _handle_event
        authors = None
        if filter_by_follows and relay_mode == 'outbox':
            authors = self.follow_filter_authors
            print(f"Using outbox relay optimization with {len(authors)} authors", file=sys.stderr)

        # Resolve relays
        relay_resolution = self.relay_resolver.resolve(
            relay_mode,
            feed_config=self.feed_config,
            user_pubkey=user_pubkey,
            authors=authors
        )

        # Start stream
        self.stream_manager.start_feed_stream(
            self.feed_name,
            self.feed_config,
            relay_resolution
        )

        # Keep running
        try:
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print(file=sys.stderr)
            print("=" * 80, file=sys.stderr)
            print(f"Received {self.event_count} events", file=sys.stderr)
            print("Stopping...", file=sys.stderr)
            print("=" * 80, file=sys.stderr)
            self.stop()

    def stop(self):
        """Stop streaming."""
        self.stream_manager.stop_all()
        self.db_manager.close_all()

    def _get_feed_authors(self):
        """Get list of authors for feed (from follows)."""
        from .config import npub_to_hex
        from .follows import FollowsCache

        default_npub = self.config.global_config.get('default_npub')
        if not default_npub:
            return []

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


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description='Real-time stream viewer with enrichment and rendering'
    )
    parser.add_argument('config', help='Path to config.toml')
    parser.add_argument('feed', help='Feed name to watch')
    parser.add_argument('--jsonl', action='store_true', help='Output JSONL instead of rendered cards')
    parser.add_argument('--no-store', action='store_true', help='Do not store events to database')

    args = parser.parse_args()

    # Load config
    config = Config.load(args.config)

    # Override store_events if requested
    if args.no_store:
        feed_config = config.get_feed(args.feed)
        if feed_config:
            feed_config['store_events'] = False

    # Create viewer
    viewer = StreamViewer(
        config,
        args.feed,
        render=not args.jsonl
    )

    # Handle Ctrl+C gracefully
    def signal_handler(sig, frame):
        viewer.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    # Start streaming
    viewer.start()


if __name__ == '__main__':
    main()
