"""
Utility functions for nostr-feeds enricher

Formatting helpers, tag parsing, and time utilities.
"""

import json
import time
from typing import List, Optional, Dict, Any


def format_relative_time(timestamp: int) -> str:
    """
    Format Unix timestamp as relative time string.

    Args:
        timestamp: Unix timestamp (seconds since epoch)

    Returns:
        Human-readable relative time string (e.g., "2h ago", "3d ago")

    Examples:
        >>> format_relative_time(time.time() - 3600)
        '1h ago'
        >>> format_relative_time(time.time() - 86400)
        '1d ago'
    """
    now = time.time()
    delta = int(now - timestamp)

    if delta < 0:
        return "in the future"
    elif delta < 60:
        return "just now"
    elif delta < 3600:
        minutes = delta // 60
        return f"{minutes}m ago"
    elif delta < 86400:
        hours = delta // 3600
        return f"{hours}h ago"
    elif delta < 604800:
        days = delta // 86400
        return f"{days}d ago"
    else:
        weeks = delta // 604800
        return f"{weeks}w ago"


def format_sats(value: int) -> str:
    """
    Format satoshi amounts for display.

    Args:
        value: Amount in satoshis

    Returns:
        Formatted string (e.g., "21k", "0.5 BTC")

    Examples:
        >>> format_sats(1000)
        '1k'
        >>> format_sats(100_000_000)
        '1.00 BTC'
    """
    if value >= 100_000_000:
        btc = value / 100_000_000
        return f"{btc:.2f} BTC"
    elif value >= 1000:
        k = value / 1000
        return f"{k:.0f}k"
    else:
        return str(value)


def parse_tags(tags_json: str) -> List[List[str]]:
    """
    Parse tags from JSON string to list of tag arrays.

    Args:
        tags_json: JSON string representing tags array

    Returns:
        List of tag arrays (each tag is a list of strings)

    Examples:
        >>> parse_tags('[["e", "abc123"], ["p", "def456"]]')
        [['e', 'abc123'], ['p', 'def456']]
    """
    if not tags_json:
        return []

    try:
        tags = json.loads(tags_json)
        return tags if isinstance(tags, list) else []
    except json.JSONDecodeError:
        return []


def get_tag(event: Dict[str, Any], tag_type: str) -> Optional[str]:
    """
    Get first tag of specific type from event.

    Args:
        event: Event dict with 'tags' or 'tags_json' field
        tag_type: Tag identifier (e.g., 'e', 'p', 'a')

    Returns:
        Tag value (second element) or None if not found

    Examples:
        >>> event = {'tags': [['e', 'abc123'], ['p', 'def456']]}
        >>> get_tag(event, 'e')
        'abc123'
        >>> get_tag(event, 'nonexistent')
        None
    """
    # Try direct tags field first
    tags = event.get('tags', [])

    # If not found, try parsing tags_json
    if not tags and 'tags_json' in event:
        tags = parse_tags(event['tags_json'])

    # Find first matching tag
    for tag in tags:
        if isinstance(tag, list) and len(tag) >= 2 and tag[0] == tag_type:
            return tag[1]

    return None


def get_all_tags(event: Dict[str, Any], tag_type: str) -> List[str]:
    """
    Get all tags of specific type from event.

    Args:
        event: Event dict with 'tags' or 'tags_json' field
        tag_type: Tag identifier (e.g., 'e', 'p')

    Returns:
        List of tag values (second elements)

    Examples:
        >>> event = {'tags': [['e', 'abc'], ['e', 'def'], ['p', 'ghi']]}
        >>> get_all_tags(event, 'e')
        ['abc', 'def']
    """
    # Try direct tags field first
    tags = event.get('tags', [])

    # If not found, try parsing tags_json
    if not tags and 'tags_json' in event:
        tags = parse_tags(event['tags_json'])

    # Collect all matching tags
    return [
        tag[1] for tag in tags
        if isinstance(tag, list) and len(tag) >= 2 and tag[0] == tag_type
    ]


def parse_bolt11_amount(bolt11: str) -> int:
    """
    Extract satoshi amount from bolt11 invoice.

    This is a simplified parser that looks for the amount field
    in the human-readable part of the invoice.

    Args:
        bolt11: Lightning Network invoice string

    Returns:
        Amount in satoshis, or 0 if parsing fails

    Examples:
        >>> parse_bolt11_amount('lnbc10n1...')  # 10 nano-BTC = 1 sat
        1
    """
    if not bolt11 or not bolt11.startswith('ln'):
        return 0

    try:
        # Extract the human-readable part
        # Format: ln<network><amount><multiplier>1<data>
        # Examples: lnbc10n1... (10 nano-BTC = 1 sat)
        #           lnbc100u1... (100 micro-BTC = 10000 sats)

        # Find the first '1' which separates human-readable from data
        separator_idx = bolt11.index('1', 4)
        hrp = bolt11[:separator_idx]

        # Remove 'ln' and network prefix (bc, tb, etc.)
        amount_str = hrp[4:]  # Skip 'lnbc' or similar

        # Parse amount and multiplier
        if not amount_str:
            return 0

        # Find where digits end
        i = 0
        while i < len(amount_str) and (amount_str[i].isdigit() or amount_str[i] == '.'):
            i += 1

        if i == 0:
            return 0

        amount = float(amount_str[:i])
        multiplier = amount_str[i] if i < len(amount_str) else 'n'

        # Convert to satoshis based on multiplier
        # p = pico-BTC (0.00000000001 BTC) = 0.001 sat
        # n = nano-BTC (0.000000001 BTC) = 0.1 sat
        # u = micro-BTC (0.000001 BTC) = 100 sats
        # m = milli-BTC (0.001 BTC) = 100,000 sats
        multipliers = {
            'p': 0.001,
            'n': 0.1,
            'u': 100,
            'm': 100_000,
        }

        satoshis = amount * multipliers.get(multiplier, 1)
        return int(satoshis)

    except (ValueError, IndexError):
        return 0


def truncate_content(content: str, max_length: int = 140, suffix: str = "...") -> str:
    """
    Truncate content to max length with suffix.

    Args:
        content: Content string to truncate
        max_length: Maximum length (default 140)
        suffix: Suffix to add when truncating (default "...")

    Returns:
        Truncated string

    Examples:
        >>> truncate_content("Hello world!", 5)
        'He...'
    """
    if len(content) <= max_length:
        return content

    return content[:max_length - len(suffix)] + suffix


def format_stats(deps: Dict[str, Any]) -> str:
    """
    Format aggregated stats for display.

    Args:
        deps: Dependencies dict with stats

    Returns:
        Formatted stats string (e.g., "💬 7  ❤️ 42  ⚡ 21k")

    Examples:
        >>> deps = {
        ...     'reactions': {'count': 42},
        ...     'replies': {'count': 7},
        ...     'zaps': {'count': 15, 'total_sats': 21000}
        ... }
        >>> format_stats(deps)
        '💬 7  ❤️ 42  ⚡ 21k'
    """
    parts = []

    # Replies (handle both aggregate mode {count: N} and expanded mode [events...])
    if 'replies' in deps:
        replies_data = deps['replies']
        if isinstance(replies_data, dict):
            # Aggregate mode
            count = replies_data.get('count', 0)
        elif isinstance(replies_data, list):
            # Expanded mode
            count = len(replies_data)
        else:
            count = 0

        if count > 0:
            parts.append(f"💬 {count}")

    # Reactions
    if 'reactions' in deps and isinstance(deps['reactions'], dict):
        count = deps['reactions'].get('count', 0)
        if count > 0:
            parts.append(f"❤️ {count}")

    # Zaps
    if 'zaps' in deps and isinstance(deps['zaps'], dict):
        count = deps['zaps'].get('count', 0)
        if count > 0:
            total_sats = deps['zaps'].get('total_sats', 0)
            parts.append(f"⚡ {format_sats(total_sats)}")

    return "  ".join(parts)
