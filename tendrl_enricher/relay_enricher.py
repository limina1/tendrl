"""
Relay-based enrichment for events fetched from relays

When an event is fetched from a relay (not from local DB), we need to also
fetch its related events (replies, reactions, zaps) from the relay for enrichment.
"""

import json
import subprocess
import sys
from typing import Dict, List, Any, Optional

from .profiles import ProfileCache
from .db import DatabaseManager


def fetch_related_events_from_relay(
    event_id: str,
    relays: List[str],
    timeout: int = 15
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Fetch all related events for enrichment from relays.

    Args:
        event_id: Event ID to fetch related events for
        relays: List of relay URLs
        timeout: Timeout in seconds

    Returns:
        Dictionary with:
        - replies: List of kind 1 events with e tag = event_id
        - reactions: List of kind 7 events with e tag = event_id
        - zaps: List of kind 9735 events with e or p tag = event_id
        - reposts: List of kind 6 events with e tag = event_id
    """
    related = {
        'replies': [],
        'reactions': [],
        'zaps': [],
        'reposts': []
    }

    # Fetch replies (kind 1)
    related['replies'] = fetch_events_by_tag(event_id, 1, relays, limit=100, timeout=timeout)

    # Fetch reactions (kind 7)
    related['reactions'] = fetch_events_by_tag(event_id, 7, relays, limit=100, timeout=timeout)

    # Fetch zaps (kind 9735)
    related['zaps'] = fetch_events_by_tag(event_id, 9735, relays, limit=100, timeout=timeout)

    # Fetch reposts (kind 6)
    related['reposts'] = fetch_events_by_tag(event_id, 6, relays, limit=50, timeout=timeout)

    return related


def fetch_events_by_tag(
    tag_value: str,
    kind: int,
    relays: List[str],
    limit: int = 100,
    timeout: int = 10
) -> List[Dict[str, Any]]:
    """
    Fetch events of specific kind that reference a tag value.

    Args:
        tag_value: Tag value to search for (event ID or pubkey)
        kind: Event kind
        relays: List of relay URLs
        limit: Maximum events to fetch
        timeout: Timeout in seconds

    Returns:
        List of event dictionaries
    """
    try:
        # Use nak req to fetch events
        cmd = ['nak', 'req', '-k', str(kind), '-e', tag_value, '-l', str(limit)] + relays

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        # Parse JSONL output
        events = []
        for line in result.stdout.strip().split('\n'):
            if line:
                try:
                    event = json.loads(line)
                    events.append(event)
                except json.JSONDecodeError:
                    continue

        return events

    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def fetch_profile_from_relay(
    pubkey: str,
    relays: List[str],
    timeout: int = 10
) -> Optional[Dict[str, Any]]:
    """
    Fetch kind 0 profile metadata from relays.

    Args:
        pubkey: Hex pubkey to fetch profile for
        relays: List of relay URLs
        timeout: Timeout in seconds

    Returns:
        Profile event dict if found, None otherwise
    """
    try:
        # Use nak req to fetch kind 0 for this author
        cmd = ['nak', 'req', '-k', '0', '-a', pubkey, '-l', '1'] + relays

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        # Parse JSONL output (should be single profile event)
        for line in result.stdout.strip().split('\n'):
            if line:
                try:
                    event = json.loads(line)
                    if event.get('kind') == 0 and event.get('pubkey') == pubkey:
                        return event
                except json.JSONDecodeError:
                    continue

        return None

    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def store_profile_to_db(
    profile_event: Dict[str, Any],
    db_manager: DatabaseManager
) -> bool:
    """
    Store profile metadata to profiles.db.

    Args:
        profile_event: Kind 0 profile event
        db_manager: Database manager instance

    Returns:
        True if stored successfully, False otherwise
    """
    try:
        # Get profiles database
        profiles_db = db_manager.get_connection('profiles.db')

        # Extract profile data from event content
        try:
            profile_data = json.loads(profile_event.get('content', '{}'))
            profile_data['pubkey'] = profile_event['pubkey']
            profile_data['created_at'] = profile_event.get('created_at', 0)

            # Store the profile
            from .db import store_profile
            store_profile(profiles_db, profile_data)

            return True

        except json.JSONDecodeError:
            print(f"Warning: Invalid JSON in profile content", file=sys.stderr)
            return False

    except Exception as e:
        print(f"Warning: Failed to store profile to DB: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return False


def enrich_event_from_relay(
    event: Dict[str, Any],
    relays: List[str],
    profile_cache: ProfileCache,
    max_depth: int = 3,
    current_depth: int = 0,
    expand_replies: bool = True,
    db_manager: Optional[DatabaseManager] = None
) -> Dict[str, Any]:
    """
    Enrich event using data fetched from relays.

    Args:
        event: Event dictionary
        relays: List of relay URLs
        profile_cache: Profile cache for author lookups
        max_depth: Maximum recursive depth for replies
        current_depth: Current recursion depth
        expand_replies: Whether to recursively expand replies
        db_manager: Optional database manager for storing fetched profiles

    Returns:
        Enriched event dictionary
    """
    from .utils import format_relative_time, format_stats

    event_id = event.get('id', '')
    pubkey = event.get('pubkey', '')
    created_at = event.get('created_at', 0)

    enriched = {
        'event': event,
        'deps': {},
        'meta': {
            'depth': current_depth
        }
    }

    # Fetch author profile (check if stale and refetch if needed)
    if profile_cache.is_stale(pubkey):
        # Profile is missing or expired - fetch from relay
        if db_manager:
            print(f"Fetching profile for {pubkey[:8]}... from relay (stale/missing)", file=sys.stderr)
            profile_event = fetch_profile_from_relay(pubkey, relays)
            if profile_event:
                # Store to profiles.db
                if store_profile_to_db(profile_event, db_manager):
                    print(f"  ✓ Stored profile for {pubkey[:8]}... to profiles.db", file=sys.stderr)
                    # Get the newly stored profile from cache (it will fetch from DB)
                    author_profile = profile_cache.get(pubkey)
                    if author_profile:
                        enriched['deps']['author'] = author_profile
                    else:
                        enriched['deps']['author'] = profile_cache.get_or_default(pubkey)
                else:
                    enriched['deps']['author'] = profile_cache.get_or_default(pubkey)
            else:
                print(f"  ✗ Profile not found for {pubkey[:8]}...", file=sys.stderr)
                enriched['deps']['author'] = profile_cache.get_or_default(pubkey)
        else:
            enriched['deps']['author'] = profile_cache.get_or_default(pubkey)
    else:
        # Profile is fresh, use from cache
        author_profile = profile_cache.get(pubkey)
        if author_profile:
            enriched['deps']['author'] = author_profile
        else:
            enriched['deps']['author'] = profile_cache.get_or_default(pubkey)

    # Fetch related events from relay
    print(f"Fetching related events from relay (depth={current_depth})...", flush=True)
    related = fetch_related_events_from_relay(event_id, relays)

    # Process reactions (aggregate)
    reactions = related['reactions']
    if reactions:
        from collections import Counter
        content_counts = Counter(
            r.get('content', '❤️').strip() or '❤️'
            for r in reactions
        )
        enriched['deps']['reactions'] = {
            'count': len(reactions),
            'by_content': dict(content_counts),
            'expandable': False
        }

    # Process zaps (aggregate)
    zaps = related['zaps']
    if zaps:
        from .utils import parse_bolt11_amount, get_tag
        total_sats = sum(
            parse_bolt11_amount(get_tag(z, 'bolt11') or '')
            for z in zaps
        )
        enriched['deps']['zaps'] = {
            'count': len(zaps),
            'total_sats': total_sats,
            'expandable': False
        }

    # Process reposts (aggregate)
    reposts = related['reposts']
    if reposts:
        enriched['deps']['reposts'] = {
            'count': len(reposts),
            'expandable': False
        }

    # Process replies (recursive if enabled)
    replies = related['replies']
    if replies:
        if expand_replies and current_depth < max_depth:
            # Recursive enrichment
            enriched_replies = []
            for reply in replies[:10]:  # Limit to 10 direct replies per level
                enriched_reply = enrich_event_from_relay(
                    reply,
                    relays,
                    profile_cache,
                    max_depth,
                    current_depth + 1,
                    expand_replies,
                    db_manager  # Pass through for recursive profile fetching
                )
                enriched_replies.append(enriched_reply)

            enriched['deps']['replies'] = enriched_replies
        else:
            # Aggregate only
            enriched['deps']['replies'] = {
                'count': len(replies),
                'expandable': True
            }

    # Add formatted metadata
    enriched['meta']['formatted_time'] = format_relative_time(created_at)
    enriched['meta']['formatted_stats'] = format_stats(enriched['deps'])

    return enriched
