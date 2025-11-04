#!/usr/bin/env python3
"""
Repost enrichment for kind 6 events

Parses embedded events and enriches with profiles.
"""

import json
from typing import Dict, Any, Optional, Callable

from .profiles import ProfileCache
from .utils import format_relative_time


def enrich_repost(
    repost_event: Dict[str, Any],
    profile_cache: ProfileCache,
    profile_fetcher: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
    event_db = None
) -> Dict[str, Any]:
    """
    Enrich kind 6 repost with parsed original event and profiles.

    Per NIP-18:
    - Content contains stringified JSON of original note (or empty)
    - Must have 'e' tag with original event ID + relay URL
    - Should have 'p' tag with original author's pubkey

    Args:
        repost_event: Raw repost event (kind 6)
        profile_cache: Profile cache for fetching profiles
        profile_fetcher: Optional callback to fetch missing profiles from relays
        event_db: Optional database connection to look up original event

    Returns:
        Enriched repost data for template rendering

    Examples:
        >>> repost = {'kind': 6, 'content': '{"id":"abc", ...}', 'tags': [['e', 'abc']]}
        >>> enriched = enrich_repost(repost, profile_cache)
        >>> enriched['deps']['reposted_event']['content']
        'Original note content'
    """
    # Get reposter's profile
    reposter_pubkey = repost_event.get('pubkey')
    reposter = None
    if reposter_pubkey:
        reposter = profile_cache.get(reposter_pubkey)
        if not reposter and profile_fetcher:
            reposter = profile_fetcher(reposter_pubkey)

    # Parse embedded event from content (NIP-18: content = stringified JSON)
    reposted_event = None
    reposted_author = None

    content = repost_event.get('content', '')
    if content:
        try:
            reposted_event = json.loads(content)
        except json.JSONDecodeError:
            pass

    # Fallback: Try to find original event via 'e' tag
    if not reposted_event and event_db:
        e_tag = next((t for t in repost_event.get('tags', []) if t[0] == 'e'), None)
        if e_tag and len(e_tag) > 1:
            original_id = e_tag[1]
            # Try to fetch from database
            try:
                from .db import get_event_by_id
                reposted_event = get_event_by_id(event_db, original_id)
            except:
                pass

    # Get original author's profile
    if reposted_event:
        original_author_pubkey = reposted_event.get('pubkey')
        if original_author_pubkey:
            reposted_author = profile_cache.get(original_author_pubkey)
            if not reposted_author and profile_fetcher:
                reposted_author = profile_fetcher(original_author_pubkey)

    # If still no author, try 'p' tag
    if not reposted_author:
        p_tag = next((t for t in repost_event.get('tags', []) if t[0] == 'p'), None)
        if p_tag and len(p_tag) > 1:
            original_author_pubkey = p_tag[1]
            reposted_author = profile_cache.get(original_author_pubkey)
            if not reposted_author and profile_fetcher:
                reposted_author = profile_fetcher(original_author_pubkey)

    # Format time
    formatted_time = format_relative_time(repost_event.get('created_at', 0))

    return {
        'event': repost_event,
        'deps': {
            'author': reposter,  # The person who reposted
            'reposted_event': reposted_event,  # Original event
            'reposted_author': reposted_author  # Original author
        },
        'meta': {
            'formatted_time': formatted_time,
            'depth': 0
        }
    }
