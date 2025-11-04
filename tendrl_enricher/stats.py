"""
Statistics computation for nostr-feeds enricher

Aggregate stats from related events: reactions, replies, zaps.
"""

import sqlite3
from typing import Dict, Any, List
from collections import Counter

from .db import get_events_by_tag
from .utils import parse_bolt11_amount, get_tag


def compute_reactions(
    conn: sqlite3.Connection,
    event_id: str,
    limit: int = 1000
) -> Dict[str, Any]:
    """
    Compute reaction statistics for an event.

    Reactions are kind 7 events that reference the event via e_tag.

    Args:
        conn: SQLite connection to feed database
        event_id: Event ID to get reactions for
        limit: Maximum reactions to fetch

    Returns:
        Dictionary with reaction stats:
        {
            'count': 42,
            'by_content': {'❤️': 30, '🔥': 8, '👍': 4}
        }

    Examples:
        >>> stats = compute_reactions(conn, 'abc123')
        >>> stats['count']
        42
        >>> stats['by_content']['❤️']
        30
    """
    reactions = get_events_by_tag(conn, kind=7, tag_type='e', tag_value=event_id, limit=limit)

    if not reactions:
        return {
            'count': 0,
            'by_content': {}
        }

    # Count reactions by content (emoji)
    content_counts = Counter()
    for reaction in reactions:
        content = reaction.get('content', '❤️').strip() or '❤️'
        content_counts[content] += 1

    return {
        'count': len(reactions),
        'by_content': dict(content_counts)
    }


def compute_replies(
    conn: sqlite3.Connection,
    event_id: str,
    limit: int = 1000
) -> Dict[str, Any]:
    """
    Compute reply statistics for an event.

    Replies are kind 1 events that reference the event via e_tag.

    Args:
        conn: SQLite connection to feed database
        event_id: Event ID to get replies for
        limit: Maximum replies to fetch

    Returns:
        Dictionary with reply stats:
        {
            'count': 7,
            'expandable': True
        }

    Examples:
        >>> stats = compute_replies(conn, 'abc123')
        >>> stats['count']
        7
    """
    replies = get_events_by_tag(conn, kind=1, tag_type='e', tag_value=event_id, limit=limit)

    return {
        'count': len(replies),
        'expandable': len(replies) > 0
    }


def compute_zaps(
    conn: sqlite3.Connection,
    event_id: str,
    author_pubkey: str,
    limit: int = 1000
) -> Dict[str, Any]:
    """
    Compute zap statistics for an event.

    Zaps are kind 9735 events (zap receipts) that reference:
    - The event via e_tag, OR
    - The author via p_tag (author zaps)

    Args:
        conn: SQLite connection to feed database
        event_id: Event ID to get zaps for
        author_pubkey: Author's pubkey (for author zaps)
        limit: Maximum zaps to fetch

    Returns:
        Dictionary with zap stats:
        {
            'count': 15,
            'total_sats': 21000,
            'expandable': True,
            'details': [...]  # Optional: full zap details
        }

    Examples:
        >>> stats = compute_zaps(conn, 'abc123', 'author_pubkey')
        >>> stats['total_sats']
        21000
    """
    # Get zaps that reference this event
    event_zaps = get_events_by_tag(conn, kind=9735, tag_type='e', tag_value=event_id, limit=limit)

    # Also get zaps that reference the author (general author zaps)
    author_zaps = get_events_by_tag(conn, kind=9735, tag_type='p', tag_value=author_pubkey, limit=limit)

    # Combine and deduplicate
    all_zaps = {zap['id']: zap for zap in event_zaps + author_zaps}.values()

    if not all_zaps:
        return {
            'count': 0,
            'total_sats': 0,
            'expandable': False
        }

    # Sum satoshi amounts from bolt11 invoices
    total_sats = 0
    zap_details = []

    for zap in all_zaps:
        # Extract amount from bolt11 tag
        bolt11 = get_tag(zap, 'bolt11')
        amount = parse_bolt11_amount(bolt11) if bolt11 else 0

        total_sats += amount

        # Get zapper info (from description tag)
        description = get_tag(zap, 'description')
        zapper_pubkey = zap.get('pubkey', '')

        zap_details.append({
            'zapper_pubkey': zapper_pubkey,
            'amount': amount,
            'bolt11': bolt11,
            'description': description
        })

    # Sort by amount (descending)
    zap_details.sort(key=lambda z: z['amount'], reverse=True)

    return {
        'count': len(all_zaps),
        'total_sats': total_sats,
        'expandable': len(all_zaps) > 0,
        'details': zap_details
    }


def compute_all_stats(
    conn: sqlite3.Connection,
    event_id: str,
    author_pubkey: str
) -> Dict[str, Any]:
    """
    Compute all statistics for an event.

    Convenience function that computes reactions, replies, and zaps.

    Args:
        conn: SQLite connection to feed database
        event_id: Event ID
        author_pubkey: Author's pubkey

    Returns:
        Dictionary with all stats:
        {
            'reactions': {...},
            'replies': {...},
            'zaps': {...}
        }

    Examples:
        >>> stats = compute_all_stats(conn, 'abc123', 'author_pubkey')
        >>> stats['reactions']['count']
        42
        >>> stats['zaps']['total_sats']
        21000
    """
    return {
        'reactions': compute_reactions(conn, event_id),
        'replies': compute_replies(conn, event_id),
        'zaps': compute_zaps(conn, event_id, author_pubkey)
    }


def expand_zap_details(
    conn: sqlite3.Connection,
    event_id: str,
    author_pubkey: str,
    profile_cache,
    limit: int = 100
) -> List[Dict[str, Any]]:
    """
    Get detailed zap information with zapper profiles.

    Used for stat expansion (when user presses 'e' on zap count).

    Args:
        conn: SQLite connection
        event_id: Event ID
        author_pubkey: Author pubkey
        profile_cache: ProfileCache instance
        limit: Maximum zaps to return

    Returns:
        List of zap detail dictionaries:
        [
            {
                'zapper_name': 'bob',
                'zapper_display_name': 'Bob Smith',
                'amount': 10000,
                'comment': 'Great post!',
                'timestamp': 1709876543
            },
            ...
        ]
    """
    zap_stats = compute_zaps(conn, event_id, author_pubkey, limit=limit)
    details = []

    for zap_detail in zap_stats.get('details', [])[:limit]:
        zapper_pubkey = zap_detail['zapper_pubkey']

        # Get zapper profile
        profile = profile_cache.get_or_default(zapper_pubkey)

        # Parse comment from description (kind 9734 zap request)
        # The description tag contains a JSON-encoded zap request
        # For now, we'll just use None (full parsing would require JSON decode)
        comment = None  # TODO: Parse from description JSON

        details.append({
            'zapper_pubkey': zapper_pubkey,
            'zapper_name': profile.get('name', 'unknown'),
            'zapper_display_name': profile.get('display_name', 'Unknown'),
            'amount': zap_detail['amount'],
            'comment': comment
        })

    return details


def expand_reply_details(
    conn: sqlite3.Connection,
    event_id: str,
    profile_cache,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Get detailed reply information with author profiles.

    Used for stat expansion (when user presses 'e' on reply count).

    Args:
        conn: SQLite connection
        event_id: Event ID
        profile_cache: ProfileCache instance
        limit: Maximum replies to return

    Returns:
        List of reply summaries:
        [
            {
                'author_name': 'alice',
                'content_preview': 'Great point! I think...',
                'created_at': 1709876543
            },
            ...
        ]
    """
    replies = get_events_by_tag(conn, kind=1, tag_type='e', tag_value=event_id, limit=limit)
    details = []

    for reply in replies:
        author_pubkey = reply['pubkey']
        profile = profile_cache.get_or_default(author_pubkey)

        # Truncate content for preview
        content = reply.get('content', '')
        preview = content[:100] + '...' if len(content) > 100 else content

        details.append({
            'author_pubkey': author_pubkey,
            'author_name': profile.get('name', 'unknown'),
            'author_display_name': profile.get('display_name', 'Unknown'),
            'content_preview': preview,
            'created_at': reply['created_at']
        })

    return details
