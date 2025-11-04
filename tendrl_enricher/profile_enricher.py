"""
Profile view enricher for nostr-feeds

Fetches and enriches user profile views with kind 0 metadata and top-level posts.
Supports general and outbox relay modes for relay selection.
"""

import json
import subprocess
import sys
import time
from typing import Dict, Any, List, Optional

from .config import Config, npub_to_hex
from .db import (
    DatabaseManager, get_profile, store_profile, get_events_by_author,
    store_event, get_top_level_posts, get_reply_posts
)
from .profiles import ProfileCache
from .enricher import Enricher
from .relay_resolver import RelayResolver
from .profile_fetcher import fetch_profile_from_relay
from .fetch_stats import fetch_missing_stats_for_events


def fetch_user_posts_from_relay(
    pubkey: str,
    relays: List[str],
    limit: int = 50,
    timeout: int = 15
) -> List[Dict[str, Any]]:
    """
    Fetch all kind 1 posts (including replies) from relays for a specific author.

    Note: We fetch ALL posts and store them to database. SQL filtering will be used
    to separate top-level posts from replies, allowing future reply views to reuse
    the same data.

    Args:
        pubkey: Author's hex pubkey
        relays: List of relay URLs
        limit: Maximum posts to fetch
        timeout: Timeout in seconds

    Returns:
        List of kind 1 event dictionaries (all posts, including replies)
    """
    try:
        # Fetch all kind 1 events from this author (no filtering)
        cmd = ['nak', 'req', '-k', '1', '-a', pubkey, '-l', str(limit)] + relays

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        # Parse all events (no filtering)
        events = []
        for line in result.stdout.strip().split('\n'):
            if line:
                try:
                    event = json.loads(line)
                    events.append(event)
                except json.JSONDecodeError:
                    continue

        return events

    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"Error fetching posts: {e}", file=sys.stderr)
        return []


def enrich_profile_view(
    config: Config,
    profile_identifier: str,
    limit: int = 50,
    relay_mode: str = 'general',
    output_file=None,
    render: bool = False,
    show_replies: bool = False
) -> None:
    """
    Enrich profile view with kind 0 metadata and posts.

    Args:
        config: Configuration object
        profile_identifier: npub or hex pubkey
        limit: Maximum posts to display
        relay_mode: 'general' or 'outbox' for relay selection
        output_file: Output file handle (defaults to stdout)
        render: If True, render using profile-view.j2 template
        show_replies: If True, show reply posts. If False, show top-level posts only

    Examples:
        >>> config = Config.load('config.toml')
        >>> # Show top-level posts only
        >>> enrich_profile_view(config, 'npub1abc...', limit=10, relay_mode='outbox', render=True)
        >>> # Show replies only
        >>> enrich_profile_view(config, 'npub1abc...', limit=10, show_replies=True, render=True)
    """
    if output_file is None:
        output_file = sys.stdout

    # Normalize identifier to hex
    try:
        if profile_identifier.startswith('npub1'):
            pubkey = npub_to_hex(profile_identifier)
        else:
            pubkey = profile_identifier

        print(f"Fetching profile for: {pubkey[:16]}...", file=sys.stderr)

    except Exception as e:
        print(f"Error: Invalid profile identifier: {e}", file=sys.stderr)
        sys.exit(1)

    # Setup database manager
    db_manager = DatabaseManager(config.get_db_dir())

    try:
        # Get profiles database
        profiles_db_file = config.get_profiles_db()
        profiles_db = db_manager.get_connection(profiles_db_file)

        # Create profile cache
        profile_cache_ttl = config.get_profile_cache_ttl()
        profile_cache = ProfileCache(profiles_db, cache_ttl=profile_cache_ttl)

        # Step 1: Resolve relays based on mode
        resolver = RelayResolver(config, db_manager)

        if relay_mode == 'general':
            print(f"Using general relay mode (configured relays)", file=sys.stderr)
            relay_resolution = resolver.resolve('general')
            relays = relay_resolution.relays
        elif relay_mode == 'outbox':
            print(f"Using outbox relay mode (author's write relays)", file=sys.stderr)
            relay_resolution = resolver.resolve('outbox', authors=[pubkey])

            if relay_resolution.strategy == 'grouped':
                # Extract relays for this author
                relays = list(relay_resolution.relays.keys())
            else:
                relays = relay_resolution.relays

            print(f"Resolved {len(relays)} outbox relays for author", file=sys.stderr)
        else:
            print(f"Error: Unknown relay_mode: {relay_mode}", file=sys.stderr)
            sys.exit(1)

        # Step 2: Fetch kind 0 profile
        print(f"Fetching kind 0 profile...", file=sys.stderr)

        # Check cache first
        profile_data = profile_cache.get(pubkey)

        if not profile_data or profile_cache.is_stale(pubkey):
            print(f"Profile not cached or stale, fetching from relays...", file=sys.stderr)
            profile_event = fetch_profile_from_relay(pubkey, relays, timeout=10)

            if profile_event:
                # Extract profile data from event content
                try:
                    content_data = json.loads(profile_event.get('content', '{}'))
                    content_data['pubkey'] = pubkey
                    content_data['created_at'] = profile_event.get('created_at', 0)

                    # Store and cache
                    store_profile(profiles_db, content_data)
                    profile_cache.set(pubkey, content_data)
                    profile_data = content_data

                    print(f"Fetched profile: {content_data.get('name', 'unknown')}", file=sys.stderr)
                except json.JSONDecodeError:
                    print(f"Warning: Invalid profile content", file=sys.stderr)
                    profile_data = profile_cache.get_or_default(pubkey)
            else:
                print(f"Warning: Profile not found, using default", file=sys.stderr)
                profile_data = profile_cache.get_or_default(pubkey)
        else:
            print(f"Using cached profile: {profile_data.get('name', 'unknown')}", file=sys.stderr)

        # Step 3: Fetch all posts (kind 1, including replies)
        print(f"Fetching posts (limit={limit})...", file=sys.stderr)
        events = fetch_user_posts_from_relay(pubkey, relays, limit=limit, timeout=20)
        print(f"Fetched {len(events)} posts from relay", file=sys.stderr)

        # Step 4: Store events to temporary database for enrichment
        # Use a temporary profile view database
        temp_db_file = f"profile_{pubkey[:8]}.db"

        try:
            temp_db = db_manager.get_connection(temp_db_file)
        except FileNotFoundError:
            # Create temporary database
            print(f"Creating temporary database: {temp_db_file}", file=sys.stderr)
            from .init_db import init_feed_db
            db_path = db_manager.db_dir / temp_db_file
            init_feed_db(db_path)  # Pass Path object, not string
            temp_db = db_manager.get_connection(temp_db_file)

        # Store events
        stored_count = 0
        for event in events:
            if store_event(temp_db, event):
                stored_count += 1
        print(f"Stored {stored_count} new events to temporary database", file=sys.stderr)

        # Step 4.5: Query for posts based on show_replies flag
        if show_replies:
            print(f"Querying for reply posts (limit={limit})...", file=sys.stderr)
            filtered_events = get_reply_posts(temp_db, pubkey, limit=limit)
            print(f"Found {len(filtered_events)} replies", file=sys.stderr)
        else:
            print(f"Querying for top-level posts (limit={limit})...", file=sys.stderr)
            filtered_events = get_top_level_posts(temp_db, pubkey, limit=limit)
            print(f"Found {len(filtered_events)} top-level posts", file=sys.stderr)

        # Step 5: Fetch missing stats (reactions, replies, zaps, reposts)
        print(f"Fetching stats for {len(filtered_events)} posts...", file=sys.stderr)
        event_ids = [e.get('id') for e in filtered_events if e.get('id')]
        stat_kinds = [6, 7, 9735]  # Reposts, reactions, zaps

        if event_ids:
            stats_fetched = fetch_missing_stats_for_events(
                event_ids,
                stat_kinds,
                relays,
                temp_db,
                timeout=15
            )
            print(f"Fetched {stats_fetched} stat events", file=sys.stderr)

        # Step 6: Enrich posts with dependencies
        print(f"Enriching {len(filtered_events)} posts with dependencies...", file=sys.stderr)
        enricher = Enricher(temp_db, profiles_db, profile_cache)

        # Build dependency config
        dep_config = {
            'deps': {
                'author': {
                    'kind': 0,
                    'relation': 'author',
                    'required': True
                },
                'reactions': {
                    'kind': 7,
                    'relation': 'e_tag',
                    'mode': 'aggregate',
                    'stats': ['count', 'by_content']
                },
                'replies': {
                    'kind': 1,
                    'relation': 'e_tag',
                    'mode': 'aggregate',
                    'stats': ['count'],
                    'expandable': True
                },
                'zaps': {
                    'kind': 9735,
                    'relation': 'e_tag',
                    'mode': 'aggregate',
                    'stats': ['count', 'total_sats'],
                    'expandable': True
                },
                'reposts': {
                    'kind': 6,
                    'relation': 'e_tag',
                    'mode': 'aggregate',
                    'stats': ['count'],
                    'expandable': True
                }
            }
        }

        enriched_posts = []
        for event in filtered_events:
            enriched = enricher.enrich_event(event, dep_config, depth=0)
            enriched_posts.append(enriched)

        # Step 7: Output based on mode
        if render:
            # Render using profile-view.j2 template
            from .renderer import Renderer

            try:
                renderer = Renderer(config.get_template_dir())

                # Get the template directly from Jinja2 environment
                template = renderer.env.get_template('profile-view.j2')

                # Render profile view (combines header + posts)
                rendered = template.render(
                    profile=profile_data,
                    posts=enriched_posts
                )

                print(rendered, file=output_file)
                output_file.flush()

            except FileNotFoundError as e:
                print(f"Error: {e}", file=sys.stderr)
                print("Make sure profile-view.j2 exists in templates/", file=sys.stderr)
                sys.exit(1)
        else:
            # JSONL mode: output profile + enriched posts
            # First line: profile header
            profile_line = json.dumps({
                'type': 'profile_header',
                'profile': profile_data
            }, default=str)
            print(profile_line, file=output_file)

            # Subsequent lines: enriched posts
            for enriched in enriched_posts:
                json_line = json.dumps(enriched, default=str)
                print(json_line, file=output_file)
                output_file.flush()

    finally:
        db_manager.close_all()
