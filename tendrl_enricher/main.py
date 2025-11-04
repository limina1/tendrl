#!/usr/bin/env python3
"""
Main entry point for nostr-feeds enricher

Loads configuration, enriches events, and outputs JSONL.
"""

import sys
import json
import argparse
import time
from typing import Dict, Any, Optional

from .config import Config
from .db import DatabaseManager, get_events, store_profile
from .profiles import ProfileCache
from .enricher import Enricher
from .follows import FollowsCache
from .renderer import Renderer
from .nip19 import normalize_event_identifier, NIP19Error
from .event_finder import find_event, EventNotFoundError
from .relay_enricher import enrich_event_from_relay
from .profile_fetcher import fetch_profiles_batch
from .repost_enricher import enrich_repost
from .zap_enricher import enrich_zap_receipt
from .highlight_enricher import enrich_highlight
from .feed_fetcher import FeedFetcher
from .fetch_stats import fetch_missing_stats_for_events
from .profile_enricher import enrich_profile_view
from .auto_enricher import apply_auto_enrichment


def fetch_and_enrich_feed(
    config: Config,
    feed_name: str,
    limit: int = 50,
    since: Optional[int] = None,
    until: Optional[int] = None,
    output_file = None,
    user_npub: Optional[str] = None,
    render: bool = False,
    template: Optional[str] = None
) -> None:
    """
    Fetch events from relays, check for missing dependencies, fetch them, then enrich and output.

    This is a comprehensive fetch-and-enrich pipeline that:
    1. Fetches focus events (main feed events) from relays with --since/--until
    2. Checks database for dependency events (profiles, reactions, replies, zaps)
    3. Fetches missing dependencies from relays
    4. Stores everything to appropriate databases
    5. Enriches and renders/outputs

    Args:
        config: Loaded configuration
        feed_name: Feed to fetch and enrich (e.g., 'timeline')
        limit: Maximum events to fetch
        since: Only fetch events after this timestamp
        until: Only fetch events before this timestamp
        output_file: Output file handle (defaults to stdout)
        user_npub: User's npub for follow filtering (defaults to config default)
        render: If True, render using templates instead of JSONL output
        template: Optional template name override

    Examples:
        >>> config = Config.load('config.toml')
        >>> # Fetch last hour of timeline and enrich
        >>> fetch_and_enrich_feed(config, 'timeline', since=int(time.time()) - 3600, render=True)
    """
    if output_file is None:
        output_file = sys.stdout

    # Get feed configuration
    feed_config = config.get_feed(feed_name)
    if not feed_config:
        print(f"Error: Feed '{feed_name}' not found in config", file=sys.stderr)
        sys.exit(1)

    # Setup database connections
    db_manager = DatabaseManager(config.get_db_dir())

    try:
        # Step 1: Fetch focus events from relays
        print(f"Fetching events for feed '{feed_name}' from relays...", file=sys.stderr)
        if since:
            print(f"  Since: {since} ({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(since))})", file=sys.stderr)
        if until:
            print(f"  Until: {until} ({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(until))})", file=sys.stderr)

        # Create feed fetcher
        feed_fetcher = FeedFetcher(config, db_manager)

        # Fetch events to database
        fetched_count = feed_fetcher.fetch_feed(
            feed_name,
            limit=limit,
            since=since,
            relays=None  # Use config relays
        )
        print(f"Fetched {fetched_count} new events from relays", file=sys.stderr)

        # Step 2: Now use the standard enrich pipeline which will:
        # - Query events from database (with since/until filters)
        # - Check for missing profiles and fetch them
        # - Check for missing stats and fetch them
        # - Enrich and output

        print(f"Enriching fetched events...", file=sys.stderr)

        # Connect to feed database
        feed_db_file = feed_config.get('db_file')
        feed_db = db_manager.get_connection(feed_db_file)

        # Connect to profiles database
        profiles_db_file = config.get_profiles_db()
        profiles_db = db_manager.get_connection(profiles_db_file)

        # Create profile cache with TTL from config
        profile_cache_ttl = config.get_profile_cache_ttl()
        profile_cache = ProfileCache(profiles_db, cache_ttl=profile_cache_ttl)

        # Create follows cache
        follows_cache = FollowsCache(feed_db)

        # Create enricher
        enricher = Enricher(feed_db, profiles_db, profile_cache)

        # Get events from database
        root_config = config.get_feed_root_config(feed_name)

        # Get display kinds
        display_kinds = feed_config.get('display_kinds')
        if not display_kinds:
            root_kind = root_config.get('kind') if root_config else 1
            display_kinds = [root_kind]

        kinds = display_kinds

        # Check if follow filtering is enabled
        filter_by_follows = feed_config.get('filter_by_follows', False)
        authors = None

        if filter_by_follows:
            npub = user_npub or config.get_default_npub()
            if not npub:
                print("Error: Follow filtering enabled but no user specified", file=sys.stderr)
                sys.exit(1)

            follows = follows_cache.get_follows(npub)
            authors = list(follows)

            from .config import npub_to_hex
            user_hex = npub_to_hex(npub) if npub.startswith('npub1') else npub
            if user_hex not in authors:
                authors.append(user_hex)

            print(f"Filtering by {len(authors)} follows (including self)...", file=sys.stderr)

        # Query events (with author filter if enabled)
        events = get_events(feed_db, limit=limit, since=since, until=until, kinds=kinds, authors=authors)

        print(f"Processing {len(events)} events...", file=sys.stderr)

        # Step 3: Fetch missing profiles
        print(f"Checking profiles for {len(events)} events...", file=sys.stderr)
        pubkeys = [e.get('pubkey') for e in events if e.get('pubkey')]
        stale_pubkeys = [pk for pk in pubkeys if profile_cache.is_stale(pk)]

        # Get default relays from config (needed for profile fetching and highlight enrichment)
        default_relays = config.get_default_relays()

        if stale_pubkeys:
            print(f"Fetching {len(stale_pubkeys)} missing/expired profiles from relays...", file=sys.stderr)

            # Batch fetch profiles
            fetched_profiles = fetch_profiles_batch(stale_pubkeys, default_relays, timeout=10)
            print(f"Fetched {len(fetched_profiles)} profiles", file=sys.stderr)

            # Store fetched profiles
            for pubkey, profile_data in fetched_profiles.items():
                profile_cache.set(pubkey, profile_data)
                try:
                    store_profile(profiles_db, profile_data)
                except Exception as e:
                    print(f"Warning: Failed to store profile {pubkey[:8]}: {e}", file=sys.stderr)

        # For highlight events, extract additional pubkeys needed for enrichment
        highlight_events = [e for e in events if e.get('kind') == 9802]
        if highlight_events:
            print(f"Checking profiles for {len(highlight_events)} highlight events...", file=sys.stderr)
            highlight_pubkeys = set()

            for event in highlight_events:
                tags = event.get('tags', [])
                for tag in tags:
                    if not tag:
                        continue

                    # Get pubkeys from p-tags (content authors, editors, mentions)
                    if tag[0] == 'p' and len(tag) > 1:
                        highlight_pubkeys.add(tag[1])

                    # Get pubkey from highlighted event's e-tag
                    if tag[0] == 'e' and len(tag) > 1:
                        event_id = tag[1]
                        # Look up the highlighted event to get its author
                        from .db import get_event_by_id
                        highlighted_event = get_event_by_id(feed_db, event_id)
                        if highlighted_event:
                            highlighted_author_pubkey = highlighted_event.get('pubkey')
                            if highlighted_author_pubkey:
                                highlight_pubkeys.add(highlighted_author_pubkey)

                    # Get pubkey from highlighted addressable event's a-tag (NIP-33)
                    if tag[0] == 'a' and len(tag) > 1:
                        # Format: kind:pubkey:d-tag
                        a_value = tag[1]
                        try:
                            parts = a_value.split(':', 2)
                            if len(parts) == 3:
                                a_pubkey = parts[1]  # Extract pubkey from a-tag
                                highlight_pubkeys.add(a_pubkey)

                                # Also try to look up the addressable event to get its author
                                # (though for addressable events, the a-tag pubkey IS the author)
                                from .db import get_addressable_event
                                a_kind = int(parts[0])
                                a_d_tag = parts[2]
                                addressable_event = get_addressable_event(feed_db, a_kind, a_pubkey, a_d_tag)
                                if addressable_event:
                                    # Verify the author matches (it should)
                                    addr_author_pubkey = addressable_event.get('pubkey')
                                    if addr_author_pubkey:
                                        highlight_pubkeys.add(addr_author_pubkey)
                        except (ValueError, IndexError):
                            # Invalid 'a' tag format, skip
                            pass

            # Check which highlight-related profiles are stale
            stale_highlight_pubkeys = [pk for pk in highlight_pubkeys if profile_cache.is_stale(pk)]

            if stale_highlight_pubkeys:
                print(f"Fetching {len(stale_highlight_pubkeys)} profiles for highlight attribution...", file=sys.stderr)
                fetched_profiles = fetch_profiles_batch(stale_highlight_pubkeys, default_relays, timeout=10)
                print(f"Fetched {len(fetched_profiles)} highlight-related profiles", file=sys.stderr)

                # Store fetched profiles
                for pubkey, profile_data in fetched_profiles.items():
                    profile_cache.set(pubkey, profile_data)
                    try:
                        store_profile(profiles_db, profile_data)
                    except Exception as e:
                        print(f"Warning: Failed to store profile {pubkey[:8]}: {e}", file=sys.stderr)

        # Step 4: Fetch missing stats (reactions, replies, zaps, reposts)
        print(f"Checking for missing stats events...", file=sys.stderr)
        event_ids = [e.get('id') for e in events if e.get('id')]

        # Get dependency kinds from config
        dep_tree = config.get_dependency_tree(feed_name)
        stat_kinds = []
        for dep_name, dep_config in dep_tree.items():
            dep_kind = dep_config.get('kind')
            if dep_kind in [6, 7, 9735]:  # Repost, reaction, zap
                stat_kinds.append(dep_kind)

        if stat_kinds and event_ids:
            print(f"Fetching missing stats (kinds {stat_kinds}) for {len(event_ids)} events...", file=sys.stderr)

            # Fetch stats for all events (using default_relays from earlier)
            stats_fetched = fetch_missing_stats_for_events(
                event_ids,
                stat_kinds,
                default_relays,
                feed_db,
                timeout=15
            )
            print(f"Fetched {stats_fetched} stat events", file=sys.stderr)

        # Step 5: Enrich events (same as enrich_feed_to_jsonl)
        enriched_events = []
        root_kind = root_config.get('kind', 1)

        for event in events:
            kind = event.get('kind')

            # Skip stats-only events UNLESS they're the feed's primary kind
            if kind in [7, 9735] and kind != root_kind:
                continue

            # Enrich based on kind
            if kind == 6:
                enriched = enrich_repost(
                    event,
                    profile_cache,
                    profile_fetcher=None,
                    event_db=feed_db
                )
            elif kind == 9735:
                enriched = enrich_zap_receipt(
                    event,
                    profile_cache,
                    profile_fetcher=None,
                    event_db=feed_db
                )
            elif kind == 9802:
                # Highlight - use standard enricher + highlight-specific enrichment
                # First get standard deps (author, reactions, replies, zaps)
                enriched = enricher.enrich_event(event, root_config, depth=0)

                # Then add highlight-specific deps (highlighted_event, content_authors, etc.)
                highlight_deps = enrich_highlight(
                    event,
                    feed_db,
                    profile_cache
                )
                # Merge highlight-specific deps into enriched deps
                enriched['deps'].update(highlight_deps)
            else:
                enriched = enricher.enrich_event(event, root_config, depth=0)

            # Apply auto-enrichment (referenced events, quoted events, contacts)
            enriched = apply_auto_enrichment(
                event, enriched, feed_db, profile_cache, enricher
            )

            enriched_events.append(enriched)

        # Step 6: Output based on mode
        if render:
            try:
                renderer = Renderer(config.get_template_dir(), profile_cache=profile_cache)

                for enriched in enriched_events:
                    event = enriched.get('event', {})

                    # Use template override if provided, otherwise select automatically
                    if template:
                        event_template = template
                    else:
                        # Use contextual template selection (full context for root events)
                        event_template = renderer.select_template(event, root_config, context='full')

                    rendered = renderer.render_event(enriched, event_template)
                    print(rendered, file=output_file)
                    output_file.flush()

            except FileNotFoundError as e:
                print(f"Error: {e}", file=sys.stderr)
                print("Make sure templates exist in ~/.config/nostr-feeds/templates/", file=sys.stderr)
                sys.exit(1)
        else:
            # JSONL mode
            for enriched in enriched_events:
                json_line = json.dumps(enriched, default=str)
                print(json_line, file=output_file)
                output_file.flush()

    finally:
        db_manager.close_all()


def enrich_feed_to_jsonl(
    config: Config,
    feed_name: str,
    limit: int = 50,
    since: Optional[int] = None,
    until: Optional[int] = None,
    output_file = None,
    user_npub: Optional[str] = None,
    render: bool = False,
    template: Optional[str] = None,
    html: bool = False
) -> None:
    """
    Enrich feed and output as JSONL or rendered text.

    Args:
        config: Loaded configuration
        feed_name: Feed to enrich (e.g., 'timeline')
        limit: Maximum events to process
        since: Only process events after this timestamp
        output_file: Output file handle (defaults to stdout)
        user_npub: User's npub for follow filtering (defaults to config default)
        render: If True, render using templates instead of JSONL output
        template: Optional template name override
        html: If True, render as complete HTML page (implies render=True)

    Examples:
        >>> config = Config.load('config.toml')
        >>> enrich_feed_to_jsonl(config, 'timeline', limit=10)
        {"event": {...}, "deps": {...}, "meta": {...}}
        ...
        >>> enrich_feed_to_jsonl(config, 'timeline', limit=10, render=True)
        <<<EVENT:abc123>>>
        ...
        <<</EVENT>>>
    """
    if output_file is None:
        output_file = sys.stdout

    # Get feed configuration
    feed_config = config.get_feed(feed_name)
    if not feed_config:
        print(f"Error: Feed '{feed_name}' not found in config", file=sys.stderr)
        sys.exit(1)

    # Setup database connections
    db_manager = DatabaseManager(config.get_db_dir())

    try:
        # Connect to feed database
        feed_db_file = feed_config.get('db_file')
        if not feed_db_file:
            print(f"Error: Feed '{feed_name}' has no db_file configured", file=sys.stderr)
            sys.exit(1)

        feed_db = db_manager.get_connection(feed_db_file)

        # Connect to profiles database
        profiles_db_file = config.get_profiles_db()
        profiles_db = db_manager.get_connection(profiles_db_file)

        # Create profile cache with TTL from config
        profile_cache_ttl = config.get_profile_cache_ttl()
        profile_cache = ProfileCache(profiles_db, cache_ttl=profile_cache_ttl)

        # Create follows cache
        follows_cache = FollowsCache(feed_db)

        # Create enricher
        enricher = Enricher(feed_db, profiles_db, profile_cache)

        # Get events from database
        root_config = config.get_feed_root_config(feed_name)

        # Get display kinds (which events to render as cards)
        # By default, only show root kind (1 for notes)
        # Kinds 6/7/9735 are stored for stats only
        display_kinds = feed_config.get('display_kinds')
        if not display_kinds:
            root_kind = root_config.get('kind') if root_config else 1
            display_kinds = [root_kind]

        kinds = display_kinds

        # Check if follow filtering is enabled
        filter_by_follows = feed_config.get('filter_by_follows', False)
        authors = None

        if filter_by_follows:
            # Get user npub (from arg, config, or prompt)
            npub = user_npub or config.get_default_npub()
            if not npub:
                print("Error: Follow filtering enabled but no user specified", file=sys.stderr)
                print("Set default_npub in config or use --user flag", file=sys.stderr)
                sys.exit(1)

            # Get follow list and filter in SQL
            follows = follows_cache.get_follows(npub)
            authors = list(follows)

            # Include user's own posts
            from .config import npub_to_hex
            user_hex = npub_to_hex(npub) if npub.startswith('npub1') else npub
            if user_hex not in authors:
                authors.append(user_hex)

            print(f"Filtering by {len(authors)} follows (including self)...", file=sys.stderr)

        # Query events (with author filter if enabled)
        events = get_events(feed_db, limit=limit, since=since, until=until, kinds=kinds, authors=authors)

        if filter_by_follows:
            print(f"Found {len(events)} events from followed users", file=sys.stderr)

        # Fetch missing or expired profiles from relays
        print(f"Checking profiles for {len(events)} events...", file=sys.stderr)
        pubkeys = [e.get('pubkey') for e in events if e.get('pubkey')]
        stale_pubkeys = [pk for pk in pubkeys if profile_cache.is_stale(pk)]

        # Get default relays from config (needed for profile fetching and highlight enrichment)
        default_relays = config.get_default_relays()

        if stale_pubkeys:
            print(f"Fetching {len(stale_pubkeys)} missing/expired profiles from relays...", file=sys.stderr)

            # Batch fetch profiles
            fetched_profiles = fetch_profiles_batch(stale_pubkeys, default_relays, timeout=10)
            print(f"Fetched {len(fetched_profiles)} profiles", file=sys.stderr)

            # Store fetched profiles
            for pubkey, profile_data in fetched_profiles.items():
                profile_cache.set(pubkey, profile_data)
                try:
                    store_profile(profiles_db, profile_data)
                except Exception as e:
                    print(f"Warning: Failed to store profile {pubkey[:8]}: {e}", file=sys.stderr)

        # For highlight events, extract additional pubkeys needed for enrichment
        highlight_events = [e for e in events if e.get('kind') == 9802]
        if highlight_events:
            print(f"Checking profiles for {len(highlight_events)} highlight events...", file=sys.stderr)
            highlight_pubkeys = set()

            for event in highlight_events:
                tags = event.get('tags', [])
                for tag in tags:
                    if not tag:
                        continue

                    # Get pubkeys from p-tags (content authors, editors, mentions)
                    if tag[0] == 'p' and len(tag) > 1:
                        highlight_pubkeys.add(tag[1])

                    # Get pubkey from highlighted event's e-tag
                    if tag[0] == 'e' and len(tag) > 1:
                        event_id = tag[1]
                        # Look up the highlighted event to get its author
                        from .db import get_event_by_id
                        highlighted_event = get_event_by_id(feed_db, event_id)
                        if highlighted_event:
                            highlighted_author_pubkey = highlighted_event.get('pubkey')
                            if highlighted_author_pubkey:
                                highlight_pubkeys.add(highlighted_author_pubkey)

                    # Get pubkey from highlighted addressable event's a-tag (NIP-33)
                    if tag[0] == 'a' and len(tag) > 1:
                        # Format: kind:pubkey:d-tag
                        a_value = tag[1]
                        try:
                            parts = a_value.split(':', 2)
                            if len(parts) == 3:
                                a_pubkey = parts[1]  # Extract pubkey from a-tag
                                highlight_pubkeys.add(a_pubkey)

                                # Also try to look up the addressable event to get its author
                                # (though for addressable events, the a-tag pubkey IS the author)
                                from .db import get_addressable_event
                                a_kind = int(parts[0])
                                a_d_tag = parts[2]
                                addressable_event = get_addressable_event(feed_db, a_kind, a_pubkey, a_d_tag)
                                if addressable_event:
                                    # Verify the author matches (it should)
                                    addr_author_pubkey = addressable_event.get('pubkey')
                                    if addr_author_pubkey:
                                        highlight_pubkeys.add(addr_author_pubkey)
                        except (ValueError, IndexError):
                            # Invalid 'a' tag format, skip
                            pass

            # Check which highlight-related profiles are stale
            stale_highlight_pubkeys = [pk for pk in highlight_pubkeys if profile_cache.is_stale(pk)]

            if stale_highlight_pubkeys:
                print(f"Fetching {len(stale_highlight_pubkeys)} profiles for highlight attribution...", file=sys.stderr)
                fetched_profiles = fetch_profiles_batch(stale_highlight_pubkeys, default_relays, timeout=10)
                print(f"Fetched {len(fetched_profiles)} highlight-related profiles", file=sys.stderr)

                # Store fetched profiles
                for pubkey, profile_data in fetched_profiles.items():
                    profile_cache.set(pubkey, profile_data)
                    try:
                        store_profile(profiles_db, profile_data)
                    except Exception as e:
                        print(f"Warning: Failed to store profile {pubkey[:8]}: {e}", file=sys.stderr)

        # Enrich events (handle different kinds appropriately)
        enriched_events = []

        # Get root kind to determine if this is a special feed (like zaps feed)
        root_kind = root_config.get('kind', 1)

        for event in events:
            kind = event.get('kind')

            # Skip stats-only events UNLESS they're the feed's primary kind
            # (e.g., don't skip zaps in the zaps feed)
            if kind in [7, 9735] and kind != root_kind:
                continue

            # Enrich based on kind
            if kind == 6:
                # Repost - use special enricher
                enriched = enrich_repost(
                    event,
                    profile_cache,
                    profile_fetcher=None,  # Profiles already fetched
                    event_db=feed_db
                )
            elif kind == 9735:
                # Zap - use zap enricher with event lookup
                enriched = enrich_zap_receipt(
                    event,
                    profile_cache,
                    profile_fetcher=None,  # Profiles already fetched
                    event_db=feed_db
                )
            elif kind == 9802:
                # Highlight - use standard enricher + highlight-specific enrichment
                # First get standard deps (author, reactions, replies, zaps)
                enriched = enricher.enrich_event(event, root_config, depth=0)

                # Then add highlight-specific deps (highlighted_event, content_authors, etc.)
                highlight_deps = enrich_highlight(
                    event,
                    feed_db,
                    profile_cache
                )
                # Merge highlight-specific deps into enriched deps
                enriched['deps'].update(highlight_deps)
            else:
                # Standard enrichment (kind 1, etc.)
                enriched = enricher.enrich_event(event, root_config, depth=0)

            # Apply auto-enrichment (referenced events, quoted events, contacts)
            enriched = apply_auto_enrichment(
                event, enriched, feed_db, profile_cache, enricher
            )

            enriched_events.append(enriched)

        # Output based on mode
        if render or html:
            # Render mode: Apply templates and output with semantic markers or HTML
            try:
                renderer = Renderer(config.get_template_dir(), html_mode=html, profile_cache=profile_cache)

                if html:
                    # HTML mode: Render complete HTML page with all events
                    html_output = renderer.render_html_feed(enriched_events, feed_name=feed_name.title())
                    print(html_output, file=output_file)
                    output_file.flush()
                else:
                    # Plain text mode: Render each event individually with semantic markers
                    for enriched in enriched_events:
                        event = enriched.get('event', {})

                        # Use template override if provided, otherwise select automatically
                        if template:
                            event_template = template  # CLI override
                        else:
                            # Use contextual template selection (full context for root events)
                            event_template = renderer.select_template(event, root_config, context='full')

                        rendered = renderer.render_event(enriched, event_template)
                        print(rendered, file=output_file)
                        output_file.flush()

            except FileNotFoundError as e:
                print(f"Error: {e}", file=sys.stderr)
                print("Make sure templates exist in ~/.config/nostr-feeds/templates/", file=sys.stderr)
                sys.exit(1)
        else:
            # JSONL mode: Output enriched JSON (default)
            for enriched in enriched_events:
                json_line = json.dumps(enriched, default=str)
                print(json_line, file=output_file)
                output_file.flush()

    finally:
        db_manager.close_all()


def enrich_single_event(
    config: Config,
    event_identifier: str,
    max_depth: int = 3,
    expand_replies: bool = True,
    output_file = None,
    render: bool = False,
    template: Optional[str] = None,
    fetch_from_relays: bool = False
) -> None:
    """
    Enrich single event and output with optional reply expansion.

    Args:
        config: Loaded configuration
        event_identifier: Event ID (hex) or nevent (NIP-19)
        max_depth: Maximum depth for recursive reply expansion
        expand_replies: If True, recursively expand replies
        output_file: Output file handle (defaults to stdout)
        render: If True, render using templates
        template: Optional template name override
        fetch_from_relays: If True, fetch from relays if not in database

    Examples:
        >>> config = Config.load('config.toml')
        >>> enrich_single_event(config, 'abc123...', max_depth=3)
        >>> enrich_single_event(config, 'nevent1qqsqm3...', render=True)
    """
    if output_file is None:
        output_file = sys.stdout

    # Setup database connections
    db_manager = DatabaseManager(config.get_db_dir())

    try:
        # Normalize identifier (nevent or hex)
        try:
            event_info = normalize_event_identifier(event_identifier)
            event_id = event_info['event_id']
            relay_hints = event_info.get('relays', [])

            print(f"Looking for event: {event_id[:16]}...", file=sys.stderr)
            if relay_hints:
                print(f"Relay hints: {', '.join(relay_hints)}", file=sys.stderr)

        except (ValueError, NIP19Error) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        # Find event in databases
        try:
            event = find_event(
                event_id,
                db_manager,
                relay_hints=relay_hints,
                fetch_from_relays=fetch_from_relays
            )

            # Check if event was found in database or fetched from relay
            was_fetched_from_relay = '_source_db' not in event
            source_db = event.get('_source_db')

            if source_db:
                print(f"Found in database: {source_db}", file=sys.stderr)
            else:
                print(f"Fetched from relay: {relay_hints[0] if relay_hints else 'unknown'}", file=sys.stderr)

        except EventNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            if not fetch_from_relays and relay_hints:
                print("Tip: Use --fetch to retrieve from relays", file=sys.stderr)
            sys.exit(1)

        # Connect to profiles database
        profiles_db_file = config.get_profiles_db()
        profiles_db = db_manager.get_connection(profiles_db_file)

        # Create profile cache with TTL from config
        profile_cache_ttl = config.get_profile_cache_ttl()
        profile_cache = ProfileCache(profiles_db, cache_ttl=profile_cache_ttl)

        # Fetch missing profile for event author
        event_pubkey = event.get('pubkey')
        if event_pubkey and not profile_cache.get(event_pubkey):
            print(f"Fetching profile for event author...", file=sys.stderr)

            # Get relays: prefer relay hints from nevent, otherwise use config default
            default_relays = relay_hints if relay_hints else config.get_default_relays()

            # Fetch profile
            from .profile_fetcher import fetch_profile_from_relay
            profile_data = fetch_profile_from_relay(event_pubkey, default_relays, timeout=5)
            if profile_data:
                profile_cache.set(event_pubkey, profile_data)
                try:
                    store_profile(profiles_db, profile_data)
                    print(f"Fetched profile for {profile_data.get('name', 'unknown')}", file=sys.stderr)
                except Exception as e:
                    print(f"Warning: Failed to store profile: {e}", file=sys.stderr)

        # Only create enricher if event was from database
        if not was_fetched_from_relay:
            # Get the database for enrichment queries
            feed_db = db_manager.get_connection(source_db)
            # Create enricher
            enricher = Enricher(feed_db, profiles_db, profile_cache)
        else:
            enricher = None

        # Create custom dep config for recursive reply expansion
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
                'zaps': {
                    'kind': 9735,
                    'relation': 'p_tag',
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
                },
                'replies': {
                    'kind': 1,
                    'relation': 'e_tag',
                    'mode': 'expanded' if expand_replies else 'aggregate',
                    'stats': ['count'],
                    'expandable': True,
                    'recursive': True,
                    'max_depth': max_depth
                }
            }
        }

        print(f"Enriching event (max_depth={max_depth}, expand_replies={expand_replies})...", file=sys.stderr)

        # Decide enrichment strategy based on source
        if was_fetched_from_relay:
            # Event was fetched from relay - use relay-based enrichment
            print("Using relay-based enrichment (fetching related events)...", file=sys.stderr)
            enriched = enrich_event_from_relay(
                event,
                relay_hints if relay_hints else ['wss://relay.damus.io'],
                profile_cache,
                max_depth=max_depth,
                current_depth=0,
                expand_replies=expand_replies,
                db_manager=db_manager  # Pass db_manager for profile fetching/storing
            )
        else:
            # Event was found in database - use database-based enrichment
            enriched = enricher.enrich_event(event, dep_config, depth=0)

        # Output based on mode
        if render:
            try:
                renderer = Renderer(config.get_template_dir(), profile_cache=profile_cache)

                event = enriched.get('event', {})

                # Use template override if provided
                if template is None:
                    if expand_replies:
                        # Use thread view for recursive reply display
                        event_template = 'thread-view.j2'
                    else:
                        # Use contextual template selection (focus/full context for single events)
                        event_template = renderer.select_template(event, context='full')
                else:
                    event_template = template

                rendered = renderer.render_event(enriched, event_template)
                print(rendered, file=output_file)
                output_file.flush()

            except FileNotFoundError as e:
                print(f"Error: {e}", file=sys.stderr)
                print("Make sure templates exist in ~/.config/nostr-feeds/templates/", file=sys.stderr)
                sys.exit(1)
        else:
            # JSONL mode
            json_line = json.dumps(enriched, default=str)
            print(json_line, file=output_file)
            output_file.flush()

    finally:
        db_manager.close_all()


def print_feed_info(config: Config, feed_name: str) -> None:
    """
    Print information about a feed.

    Args:
        config: Loaded configuration
        feed_name: Feed name
    """
    feed = config.get_feed(feed_name)
    if not feed:
        print(f"Error: Feed '{feed_name}' not found", file=sys.stderr)
        sys.exit(1)

    print(f"Feed: {feed_name}")
    print(f"  Type: {feed.get('type', 'unknown')}")
    print(f"  Database: {feed.get('db_file', 'N/A')}")
    print(f"  Refresh interval: {feed.get('refresh_interval', 0)}s")

    root = feed.get('root', {})
    print(f"  Root kind: {root.get('kind', 'N/A')}")
    print(f"  Template: {root.get('template', 'N/A')}")

    deps = root.get('deps', {})
    print(f"  Dependencies: {len(deps)}")
    for dep_name, dep_spec in deps.items():
        print(f"    - {dep_name}: kind {dep_spec.get('kind')} ({dep_spec.get('relation')})")


def check_and_refresh_if_stale(
    config: Config,
    feed_name: str,
    db_manager: Any
) -> bool:
    """
    Check if feed is stale and auto-refresh if needed.

    Returns:
        True if refresh was performed, False otherwise

    Examples:
        >>> refreshed = check_and_refresh_if_stale(config, 'timeline', db_manager)
        >>> if refreshed:
        >>>     print("Fetched new events")
    """
    import os

    feed_config = config.get_feed(feed_name)
    if not feed_config:
        return False

    refresh_interval = feed_config.get('refresh_interval', 0)
    if refresh_interval == 0:
        # Manual-only feed, no auto-refresh
        return False

    # Get latest event timestamp from database
    db_file = feed_config.get('db_file')
    if not db_file:
        return False

    db_path = os.path.join(config.get_db_dir(), db_file)
    if not os.path.exists(os.path.expanduser(db_path)):
        # Database doesn't exist, need to fetch
        print(f"Database not found, fetching initial events...", file=sys.stderr)
        from .feed_fetcher import FeedFetcher
        fetcher = FeedFetcher(config, db_manager)
        count = fetcher.fetch_feed(feed_name, limit=100)
        print(f"Fetched {count} initial events", file=sys.stderr)
        return True

    # Check staleness
    conn = db_manager.get_connection(db_file)
    cursor = conn.cursor()
    cursor.execute('SELECT MAX(created_at) FROM events')
    result = cursor.fetchone()

    if not result or not result[0]:
        # No events in database, fetch
        print(f"No events in database, fetching...", file=sys.stderr)
        from .feed_fetcher import FeedFetcher
        fetcher = FeedFetcher(config, db_manager)
        count = fetcher.fetch_feed(feed_name, limit=100)
        print(f"Fetched {count} events", file=sys.stderr)
        return True

    latest_ts = result[0]
    current_ts = int(time.time())
    time_since_fetch = current_ts - latest_ts

    if time_since_fetch > refresh_interval:
        # Stale, fetch new events
        print(f"Feed stale ({time_since_fetch}s old, refresh_interval={refresh_interval}s), fetching...", file=sys.stderr)
        from .feed_fetcher import FeedFetcher
        fetcher = FeedFetcher(config, db_manager)
        count = fetcher.fetch_feed(feed_name, limit=100, since=latest_ts)
        print(f"Fetched {count} new events", file=sys.stderr)
        return True

    # Fresh enough
    return False


def load_more_historical(
    config: Config,
    feed_name: str,
    db_manager: Any,
    limit: int = 50
) -> int:
    """
    Fetch older events (historical backfill).

    Args:
        config: Configuration
        feed_name: Feed name
        db_manager: Database manager
        limit: Number of older events to fetch

    Returns:
        Number of events fetched

    Examples:
        >>> count = load_more_historical(config, 'timeline', db_manager, 50)
        >>> print(f"Loaded {count} older events")
    """
    import os

    feed_config = config.get_feed(feed_name)
    if not feed_config:
        print(f"Error: Feed '{feed_name}' not found", file=sys.stderr)
        return 0

    # Get oldest event timestamp from database
    db_file = feed_config.get('db_file')
    if not db_file:
        print(f"Error: Feed '{feed_name}' has no db_file", file=sys.stderr)
        return 0

    db_path = os.path.join(config.get_db_dir(), db_file)
    if not os.path.exists(os.path.expanduser(db_path)):
        print(f"Database not found, cannot backfill", file=sys.stderr)
        return 0

    conn = db_manager.get_connection(db_file)
    cursor = conn.cursor()
    cursor.execute('SELECT MIN(created_at) FROM events')
    result = cursor.fetchone()

    if not result or not result[0]:
        print(f"No events in database to backfill from", file=sys.stderr)
        return 0

    oldest_ts = result[0]

    # Fetch events BEFORE oldest timestamp (downward/backward)
    print(f"Fetching {limit} events before {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(oldest_ts))}...", file=sys.stderr)

    from .feed_fetcher import FeedFetcher
    fetcher = FeedFetcher(config, db_manager)
    count = fetcher.fetch_feed(feed_name, limit=limit, since=0, until=oldest_ts)

    print(f"Loaded {count} older events (backfill)", file=sys.stderr)
    return count


def show_live_countdown(config: Config, feed_name: str) -> None:
    """
    Show live countdown timer in terminal (updates every second).

    Displays a real-time countdown showing when the next auto-refresh will occur.
    Press Ctrl+C to exit.

    Args:
        config: Loaded configuration
        feed_name: Feed name

    Examples:
        >>> show_live_countdown(config, 'timeline')
        Feed: timeline | Next refresh in: 29s | Events: 1234 | Ctrl+C to exit
    """
    import os
    from .db import DatabaseManager

    feed_config = config.get_feed(feed_name)
    if not feed_config:
        print(f"Error: Feed '{feed_name}' not found", file=sys.stderr)
        sys.exit(1)

    refresh_interval = feed_config.get('refresh_interval', 0)
    if refresh_interval <= 0:
        print(f"Feed '{feed_name}' has no auto-refresh configured (refresh_interval=0)", file=sys.stderr)
        print(f"Manual refresh only.", file=sys.stderr)
        sys.exit(1)

    db_manager = DatabaseManager(config.get_db_dir())
    db_file = feed_config.get('db_file')

    print(f"📊 Watching feed: {feed_name} (refresh every {refresh_interval}s)")
    print(f"Press Ctrl+C to exit\n")

    try:
        while True:
            # Get current status
            last_fetch = 0
            event_count = 0

            if db_file:
                try:
                    db_path = os.path.join(config.get_db_dir(), db_file)
                    if os.path.exists(os.path.expanduser(db_path)):
                        conn = db_manager.get_connection(db_file)
                        cursor = conn.cursor()

                        cursor.execute('SELECT MAX(created_at) FROM events')
                        result = cursor.fetchone()
                        if result and result[0]:
                            last_fetch = result[0]

                        cursor.execute('SELECT COUNT(*) FROM events')
                        result = cursor.fetchone()
                        if result and result[0]:
                            event_count = result[0]
                except:
                    pass

            # Calculate countdown
            if last_fetch > 0:
                current_time = int(time.time())
                time_since_fetch = current_time - last_fetch
                remaining = max(0, refresh_interval - time_since_fetch)

                # Format time
                if remaining > 60:
                    time_str = f"{remaining // 60}m {remaining % 60}s"
                else:
                    time_str = f"{remaining}s"

                # Print status line (overwrite previous line)
                status = f"\r⏱️  Next refresh in: {time_str:>8} | Events: {event_count:>6} | Last fetch: {time_since_fetch}s ago"
                print(status, end='', flush=True)

                if remaining == 0:
                    print("\n🔄 Refreshing now...")
                    # Auto-refresh could be triggered here
                    time.sleep(2)
            else:
                print("\r⏱️  No events yet - waiting for first fetch...", end='', flush=True)

            time.sleep(1)

    except KeyboardInterrupt:
        print("\n\n✓ Stopped watching")
        db_manager.close_all()
        sys.exit(0)
    finally:
        db_manager.close_all()


def show_feed_status(config: Config, feed_name: str, json_output: bool = True) -> None:
    """
    Show feed refresh status with countdown timer.

    Displays:
    - Refresh interval and strategy
    - Last fetch timestamp
    - Next refresh countdown
    - Manual refresh availability
    - Current event count in database

    Args:
        config: Loaded configuration
        feed_name: Feed name
        json_output: If True, output JSON (default), otherwise human-readable

    Examples:
        >>> show_feed_status(config, 'timeline')
        {
          "feed": "timeline",
          "refresh_interval": 30,
          "last_fetch": 1729123456,
          "next_refresh_in": 12,
          "can_manual_refresh": true,
          "event_count": 1234
        }
    """
    import os
    from .db import DatabaseManager

    # Get feed config
    feed_config = config.get_feed(feed_name)
    if not feed_config:
        print(f"Error: Feed '{feed_name}' not found", file=sys.stderr)
        sys.exit(1)

    # Get refresh interval
    refresh_interval = feed_config.get('refresh_interval', 0)

    # Get last fetch time from database metadata or FeedFetcher cache
    db_manager = DatabaseManager(config.get_db_dir())
    db_file = feed_config.get('db_file')

    last_fetch = 0
    event_count = 0

    if db_file:
        try:
            db_path = os.path.join(config.get_db_dir(), db_file)
            if os.path.exists(os.path.expanduser(db_path)):
                conn = db_manager.get_connection(db_file)
                cursor = conn.cursor()

                # Get latest event timestamp as proxy for last fetch
                cursor.execute('SELECT MAX(created_at) FROM events')
                result = cursor.fetchone()
                if result and result[0]:
                    last_fetch = result[0]

                # Get event count
                cursor.execute('SELECT COUNT(*) FROM events')
                result = cursor.fetchone()
                if result and result[0]:
                    event_count = result[0]

        except Exception as e:
            print(f"Warning: Could not read database: {e}", file=sys.stderr)
        finally:
            db_manager.close_all()

    # Calculate next refresh
    if refresh_interval > 0 and last_fetch > 0:
        current_time = int(time.time())
        time_since_fetch = current_time - last_fetch
        next_refresh_in = max(0, refresh_interval - time_since_fetch)
    else:
        next_refresh_in = None

    # Build status dict
    status = {
        'feed': feed_name,
        'refresh_interval': refresh_interval,
        'refresh_strategy': 'polling' if refresh_interval > 0 else 'manual',
        'last_fetch': last_fetch if last_fetch > 0 else None,
        'next_refresh_in': next_refresh_in,
        'can_manual_refresh': True,
        'event_count': event_count
    }

    # Output
    if json_output:
        print(json.dumps(status, indent=2))
    else:
        print(f"Feed: {feed_name}")
        print(f"  Strategy: {status['refresh_strategy']}")
        print(f"  Refresh interval: {refresh_interval}s")

        if last_fetch > 0:
            last_fetch_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(last_fetch))
            print(f"  Last fetch: {last_fetch_str}")
        else:
            print(f"  Last fetch: never")

        if next_refresh_in is not None:
            print(f"  Next refresh in: {next_refresh_in}s")
        else:
            print(f"  Next refresh: manual only")

        print(f"  Events in database: {event_count}")
        print(f"  Manual refresh: available")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='nostr-feeds enricher - Enrich Nostr events from SQLite databases',
        usage='%(prog)s [CONFIG] FEED [options]  (shorthand)\n'
              '       %(prog)s --config CONFIG --feed FEED [options]'
    )

    # Positional arguments (for convenience)
    parser.add_argument(
        'positional_args',
        nargs='*',
        help='Config path and feed name (positional)'
    )

    parser.add_argument(
        '--config',
        '-c',
        help='Path to config.toml file (default: ~/.config/nostr-feeds/config.toml)'
    )

    # Feed, event, or profile mode (mutually exclusive)
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        '--feed',
        '-f',
        help='Feed name to enrich (e.g., timeline, replies)'
    )
    mode_group.add_argument(
        '--event',
        '-e',
        help='Single event to enrich (hex ID or nevent1...)'
    )
    mode_group.add_argument(
        '--profile',
        '-p',
        help='Profile to view (npub or hex pubkey)'
    )

    parser.add_argument(
        '--limit',
        '-l',
        type=int,
        default=50,
        help='Maximum number of events to process (default: 50)'
    )

    parser.add_argument(
        '--since',
        '-s',
        type=int,
        help='Only process events after this Unix timestamp'
    )

    parser.add_argument(
        '--until',
        type=int,
        help='Only process events before this Unix timestamp'
    )

    parser.add_argument(
        '--output',
        '-o',
        help='Output file (default: stdout)'
    )

    parser.add_argument(
        '--info',
        action='store_true',
        help='Show feed info instead of enriching'
    )

    parser.add_argument(
        '--feed-status',
        action='store_true',
        help='Show feed refresh status with countdown timer (requires --feed)'
    )

    parser.add_argument(
        '--watch',
        action='store_true',
        help='Show live countdown timer in terminal (updates every second, requires --feed)'
    )

    parser.add_argument(
        '--list-feeds',
        action='store_true',
        help='List all configured feeds'
    )

    parser.add_argument(
        '--user',
        '-u',
        help='User npub (for follow filtering, defaults to config default_npub)'
    )

    parser.add_argument(
        '--render',
        '-r',
        action='store_true',
        help='Render using templates (outputs text with semantic markers instead of JSONL)'
    )

    parser.add_argument(
        '--template',
        '-t',
        help='Template name to use (overrides config, e.g., short-note-card.j2)'
    )

    parser.add_argument(
        '--html',
        action='store_true',
        help='Render as HTML (for browser viewing with interactive features)'
    )

    # Event-specific options
    parser.add_argument(
        '--max-depth',
        type=int,
        default=3,
        help='Maximum depth for recursive reply expansion (default: 3)'
    )

    parser.add_argument(
        '--expand-replies',
        action='store_true',
        default=True,
        help='Recursively expand replies (default: True, use --no-expand-replies to disable)'
    )

    parser.add_argument(
        '--no-expand-replies',
        dest='expand_replies',
        action='store_false',
        help='Disable recursive reply expansion'
    )

    parser.add_argument(
        '--fetch',
        action='store_true',
        help='Fetch from relays: For --event, fetch if not in database. For --feed, fetch new events from relays with complete dependency resolution'
    )

    parser.add_argument(
        '--auto-refresh',
        action='store_true',
        help='Auto-fetch new events if data is stale (checks refresh_interval), then render'
    )

    parser.add_argument(
        '--load-more',
        type=int,
        metavar='N',
        help='Fetch N older events before the oldest in database (historical backfill), then render'
    )

    parser.add_argument(
        '--local',
        '--dev',
        action='store_true',
        help='Use local repository templates and config (for development/testing)'
    )

    parser.add_argument(
        '--relay-mode',
        choices=['general', 'outbox'],
        default='general',
        help='Relay selection mode for --profile: general (use config relays) or outbox (use author write relays from NIP-65)'
    )

    parser.add_argument(
        '--show-replies',
        action='store_true',
        help='For --profile mode: show reply posts instead of top-level posts'
    )

    parser.add_argument(
        '--serve',
        action='store_true',
        help='Start HTTP server with interactive browser UI (requires --local or --config)'
    )

    parser.add_argument(
        '--port',
        type=int,
        default=8080,
        help='Port for HTTP server (default: 8080, only with --serve)'
    )

    parser.add_argument(
        '--host',
        default='localhost',
        help='Host for HTTP server (default: localhost, only with --serve)'
    )

    args = parser.parse_args()

    # Handle positional arguments for convenience
    # Supports: main.py config.toml feed
    if args.positional_args:
        if len(args.positional_args) >= 2 and not args.config and not args.feed:
            # Two positional args: config and feed
            args.config = args.positional_args[0]
            args.feed = args.positional_args[1]
        elif len(args.positional_args) == 1 and not args.feed:
            # One positional arg: feed (use default config)
            args.feed = args.positional_args[0]

    # Handle --local/--dev flag for development mode
    if args.local:
        # Use repository-local paths for development
        import os
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # Set config to local version if not explicitly specified
        if not args.config or args.config == '~/.config/nostr-feeds/config.toml':
            args.config = os.path.join(repo_root, 'enricher', 'config.toml')

        print(f"Development mode: Using local config {args.config}", file=sys.stderr)
    else:
        # Set default config if not specified
        if not args.config:
            args.config = '~/.config/nostr-feeds/config.toml'

    # Load configuration
    try:
        config = Config.load(args.config)

        # Override template directory in dev mode
        if args.local:
            import os
            repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            local_templates = os.path.join(repo_root, 'templates')
            config.global_config['template_dir'] = local_templates
            print(f"Development mode: Using local templates {local_templates}", file=sys.stderr)

    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        print(f"Create config at: {args.config}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        sys.exit(1)

    # List feeds mode
    if args.list_feeds:
        print("Configured feeds:")
        for feed_name in config.list_feeds():
            print(f"  - {feed_name}")
        sys.exit(0)

    # Server mode
    if args.serve:
        from .server import run_server
        run_server(args.config, args.host, args.port)
        sys.exit(0)

    # Require --feed, --event, or --profile for all other modes
    if not args.feed and not args.event and not args.profile:
        print("Error: --feed, --event, or --profile is required (or use --list-feeds)", file=sys.stderr)
        sys.exit(1)

    # Info mode (feed only)
    if args.info:
        if not args.feed:
            print("Error: --info requires --feed", file=sys.stderr)
            sys.exit(1)
        print_feed_info(config, args.feed)
        sys.exit(0)

    # Feed status mode (feed only)
    if args.feed_status:
        if not args.feed:
            print("Error: --feed-status requires --feed", file=sys.stderr)
            sys.exit(1)
        show_feed_status(config, args.feed, json_output=True)
        sys.exit(0)

    # Watch mode (live countdown timer)
    if args.watch:
        if not args.feed:
            print("Error: --watch requires --feed", file=sys.stderr)
            sys.exit(1)
        show_live_countdown(config, args.feed)
        sys.exit(0)

    # Profile mode
    if args.profile:
        try:
            if args.output:
                with open(args.output, 'w') as f:
                    enrich_profile_view(
                        config, args.profile, args.limit, args.relay_mode,
                        f, args.render, args.show_replies
                    )
            else:
                enrich_profile_view(
                    config, args.profile, args.limit, args.relay_mode,
                    render=args.render, show_replies=args.show_replies
                )

        except KeyboardInterrupt:
            print("\nInterrupted", file=sys.stderr)
            sys.exit(130)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            sys.exit(1)

        sys.exit(0)

    # Event mode
    if args.event:
        try:
            if args.output:
                with open(args.output, 'w') as f:
                    enrich_single_event(
                        config, args.event, args.max_depth, args.expand_replies,
                        f, args.render, args.template, args.fetch
                    )
            else:
                enrich_single_event(
                    config, args.event, args.max_depth, args.expand_replies,
                    render=args.render, template=args.template, fetch_from_relays=args.fetch
                )

        except KeyboardInterrupt:
            print("\nInterrupted", file=sys.stderr)
            sys.exit(130)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            sys.exit(1)

        sys.exit(0)

    # Feed mode
    try:
        # Setup database manager for auto-refresh and load-more
        db_manager = None
        if args.auto_refresh or args.load_more:
            db_manager = DatabaseManager(config.get_db_dir())

        # Handle --auto-refresh: Check staleness and fetch if needed
        if args.auto_refresh:
            check_and_refresh_if_stale(config, args.feed, db_manager)

        # Handle --load-more: Fetch older events (historical backfill)
        if args.load_more:
            load_more_historical(config, args.feed, db_manager, limit=args.load_more)

        # Choose fetch-and-enrich or database-only enrichment
        if args.fetch:
            # Fetch mode: Fetch from relays, check dependencies, enrich
            if args.output:
                with open(args.output, 'w') as f:
                    fetch_and_enrich_feed(
                        config, args.feed, args.limit, args.since, args.until,
                        f, args.user, args.render, args.template
                    )
            else:
                fetch_and_enrich_feed(
                    config, args.feed, args.limit, args.since, args.until,
                    user_npub=args.user, render=args.render, template=args.template
                )
        else:
            # Database mode: Only query and enrich from existing database
            if args.output:
                with open(args.output, 'w') as f:
                    enrich_feed_to_jsonl(
                        config, args.feed, args.limit, args.since, args.until,
                        f, args.user, args.render, args.template, args.html
                    )
            else:
                enrich_feed_to_jsonl(
                    config, args.feed, args.limit, args.since, args.until,
                    user_npub=args.user, render=args.render, template=args.template, html=args.html
                )

        # Clean up database manager
        if db_manager:
            db_manager.close_all()

    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
