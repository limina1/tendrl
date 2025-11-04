"""
Highlight enricher for NIP-84 (kind 9802) events.

Handles enrichment of highlight events including:
- Extracting highlighted nostr events (e/a tags)
- Extracting external URLs (r tags with 'source' attribute)
- Attribution (p tags with roles: author, editor, mention)
- Context extraction
- Quote highlight detection (comment tag)
"""

import json
from typing import Dict, Any, Optional, List
from .db import get_event_by_id, get_addressable_event


def enrich_highlight(
    event: Dict[str, Any],
    feed_db,
    profile_cache,
    event_finder=None
) -> Dict[str, Any]:
    """
    Enrich a highlight event (kind 9802).

    Args:
        event: The highlight event
        feed_db: Database connection for the feed
        profile_cache: Profile cache for fetching profiles
        event_finder: EventFinder instance for multi-DB search

    Returns:
        Dictionary with enriched dependencies

    Note:
        Profiles should be batch-fetched in main.py before calling this function.
        This enricher only retrieves profiles from cache.
    """
    deps = {}

    # Get tags
    tags = event.get('tags', [])

    # 1. Extract highlighted content reference
    highlighted_event = None
    highlighted_url = None
    highlighted_event_kind = None
    referenced_address = None  # Store 'a' tag value even if event not found

    for tag in tags:
        if not tag:
            continue

        tag_type = tag[0]

        # e tag: regular event reference
        if tag_type == 'e' and len(tag) > 1:
            event_id = tag[1]
            # Try to find the highlighted event
            if event_finder:
                highlighted_event = event_finder.find_event(event_id)
            else:
                highlighted_event = get_event_by_id(feed_db, event_id)

            if highlighted_event:
                highlighted_event_kind = highlighted_event.get('kind')

        # a tag: addressable event reference (NIP-33)
        elif tag_type == 'a' and len(tag) > 1:
            # Format: kind:pubkey:d-tag
            a_value = tag[1]
            try:
                # Parse the 'a' tag value
                parts = a_value.split(':', 2)  # Split on first 2 colons only
                if len(parts) == 3:
                    a_kind = int(parts[0])
                    a_pubkey = parts[1]
                    a_d_tag = parts[2]

                    # Store the reference address (even if we don't find the event)
                    referenced_address = {
                        'kind': a_kind,
                        'pubkey': a_pubkey,
                        'd_tag': a_d_tag,
                        'address': a_value
                    }

                    # Try to find the addressable event
                    if event_finder:
                        # TODO: event_finder doesn't support addressable events yet
                        # For now, just try the feed_db
                        highlighted_event = get_addressable_event(feed_db, a_kind, a_pubkey, a_d_tag)
                    else:
                        highlighted_event = get_addressable_event(feed_db, a_kind, a_pubkey, a_d_tag)

                    if highlighted_event:
                        highlighted_event_kind = highlighted_event.get('kind')
            except (ValueError, IndexError):
                # Invalid 'a' tag format, skip
                pass

        # r tag: URL reference
        elif tag_type == 'r' and len(tag) > 1:
            url = tag[1]
            # Check if this is the source URL (not a mention in comment)
            if len(tag) > 2 and tag[2] == 'source':
                highlighted_url = url
            elif len(tag) == 2:
                # If no attribute, assume it's the source
                highlighted_url = url

    deps['highlighted_event'] = highlighted_event
    deps['highlighted_url'] = highlighted_url
    deps['referenced_address'] = referenced_address

    # 2. Get highlighted event author if we found the event
    if highlighted_event:
        highlighted_author_pubkey = highlighted_event.get('pubkey')
        if highlighted_author_pubkey:
            highlighted_author = profile_cache.get(highlighted_author_pubkey)
            deps['highlighted_author'] = highlighted_author

    # 3. Extract attribution (p tags with roles)
    content_authors = []
    content_editors = []
    mentioned_users = []

    for tag in tags:
        if not tag or tag[0] != 'p':
            continue

        if len(tag) < 2:
            continue

        pubkey = tag[1]
        role = tag[3] if len(tag) > 3 else None

        # Get profile from cache (already batch-fetched in main.py)
        profile = profile_cache.get(pubkey)

        if not profile:
            continue

        # Categorize by role
        if role == 'author':
            content_authors.append(profile)
        elif role == 'editor':
            content_editors.append(profile)
        elif role == 'mention':
            mentioned_users.append(profile)

    deps['content_authors'] = content_authors
    deps['content_editors'] = content_editors
    deps['mentioned_users'] = mentioned_users

    # 4. Extract context tag
    context = None
    for tag in tags:
        if tag and tag[0] == 'context' and len(tag) > 1:
            context = tag[1]
            break

    deps['context'] = context

    # 5. Check for quote highlight (comment tag)
    comment = None
    is_quote_highlight = False
    for tag in tags:
        if tag and tag[0] == 'comment' and len(tag) > 1:
            comment = tag[1]
            is_quote_highlight = True
            break

    deps['comment'] = comment
    deps['is_quote_highlight'] = is_quote_highlight

    # 6. Extract highlighted text from content
    deps['highlighted_text'] = event.get('content', '')

    return deps


def get_highlight_metadata(event: Dict[str, Any], deps: Dict[str, Any]) -> Dict[str, Any]:
    """
    Get metadata for highlight event.

    Args:
        event: The highlight event
        deps: Enriched dependencies

    Returns:
        Metadata dictionary
    """
    metadata = {
        'type': 'highlight',
        'is_quote': deps.get('is_quote_highlight', False),
        'has_context': bool(deps.get('context')),
        'has_comment': bool(deps.get('comment')),
    }

    # Add reference type
    if deps.get('highlighted_event'):
        metadata['reference_type'] = 'nostr'
        metadata['referenced_kind'] = deps['highlighted_event'].get('kind')
    elif deps.get('referenced_address'):
        # Addressable event reference (even if event not found in database)
        metadata['reference_type'] = 'nostr'
        metadata['referenced_kind'] = deps['referenced_address'].get('kind')
    elif deps.get('highlighted_url'):
        metadata['reference_type'] = 'url'
    else:
        metadata['reference_type'] = 'unknown'

    # Add author attribution
    if deps.get('content_authors'):
        metadata['has_attribution'] = True
        metadata['author_count'] = len(deps['content_authors'])

    return metadata
