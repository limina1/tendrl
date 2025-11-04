"""
Zap enrichment for kind 9735 events

Parses bolt11 invoices, zap requests, and enriches with profiles.
"""

import re
import json
from typing import Dict, Any, Optional, Callable

from .profiles import ProfileCache
from .utils import format_relative_time


def parse_bolt11_amount(bolt11: str) -> int:
    """
    Extract amount in sats from bolt11 invoice.

    Args:
        bolt11: Lightning invoice string

    Returns:
        Amount in satoshis

    Examples:
        >>> parse_bolt11_amount('lnbc420n1...')
        42
        >>> parse_bolt11_amount('lnbc21000n1...')
        2100
    """
    if not bolt11:
        return 0

    # bolt11 format: lnbc{amount}{multiplier}...
    # multipliers: m=milli (0.001), u=micro (0.000001), n=nano (0.000000001), p=pico
    match = re.match(r'lnbc(\d+)([munp])?', bolt11.lower())
    if not match:
        return 0

    amount = int(match.group(1))
    multiplier = match.group(2)

    # Convert to millisatoshis
    # 1 BTC = 100,000,000 sats = 100,000,000,000 millisats
    if multiplier == 'm':    # milli-BTC (0.001 BTC)
        msats = amount * 100_000_000
    elif multiplier == 'u':  # micro-BTC (0.000001 BTC)
        msats = amount * 100_000
    elif multiplier == 'n':  # nano-BTC (0.000000001 BTC) - most common
        msats = amount * 100
    elif multiplier == 'p':  # pico-BTC (0.000000000001 BTC)
        msats = amount // 10  # 1 pico-BTC = 0.1 millisats
    else:
        msats = amount * 100_000_000  # Default to milli-BTC

    return msats // 1000  # Return sats


def parse_zap_request(zap_event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract zap request from description tag.

    Args:
        zap_event: Zap receipt event (kind 9735)

    Returns:
        Dictionary with sender_pubkey, message, event_id

    Examples:
        >>> zap = {'tags': [['description', '{"pubkey":"abc","content":"Great!"}']]}
        >>> result = parse_zap_request(zap)
        >>> result['sender_pubkey']
        'abc'
    """
    description_tag = next(
        (t[1] for t in zap_event.get('tags', []) if t[0] == 'description'),
        None
    )

    if not description_tag:
        return {}

    try:
        zap_request = json.loads(description_tag)

        sender_pubkey = zap_request.get('pubkey')
        message = zap_request.get('content', '')

        # Extract event ID from e tag
        event_id = next(
            (t[1] for t in zap_request.get('tags', []) if t[0] == 'e'),
            None
        )

        return {
            'sender_pubkey': sender_pubkey,
            'message': message,
            'event_id': event_id
        }
    except json.JSONDecodeError:
        return {}


def enrich_zap_receipt(
    zap_event: Dict[str, Any],
    profile_cache: ProfileCache,
    profile_fetcher: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
    event_db = None
) -> Dict[str, Any]:
    """
    Enrich zap receipt with parsed data and profiles.

    Per NIP-57, zap receipts include:
    - p tag: recipient pubkey
    - P tag: sender pubkey (zapper)
    - e tag: optional event being zapped
    - description tag: JSON-encoded zap request
    - bolt11 tag: lightning invoice

    Args:
        zap_event: Raw zap receipt event (kind 9735)
        profile_cache: Profile cache for fetching profiles
        profile_fetcher: Optional callback to fetch missing profiles from relays
        event_db: Optional database connection to look up target event

    Returns:
        Enriched zap data for template rendering

    Examples:
        >>> zap = {'kind': 9735, 'tags': [...], 'created_at': 1234567890}
        >>> enriched = enrich_zap_receipt(zap, profile_cache)
        >>> enriched['meta']['amount_sats']
        21000
    """
    # Parse bolt11 for amount
    bolt11_tag = next(
        (t[1] for t in zap_event.get('tags', []) if t[0] == 'bolt11'),
        None
    )
    amount_sats = parse_bolt11_amount(bolt11_tag) if bolt11_tag else 0

    # Parse zap request (from description tag) for message
    zap_req = parse_zap_request(zap_event)
    sender_pubkey_from_desc = zap_req.get('sender_pubkey')
    message = zap_req.get('message', '')

    # Get tags from zap receipt (per NIP-57 Appendix E)
    tags = zap_event.get('tags', [])

    # Get recipient from p tag (required per NIP-57)
    recipient_pubkey = next(
        (t[1] for t in tags if t[0] == 'p' and len(t) > 1),
        None
    )

    # Get sender from P tag (zapper) - optional but recommended
    sender_pubkey = next(
        (t[1] for t in tags if t[0] == 'P' and len(t) > 1),
        sender_pubkey_from_desc  # Fallback to description if no P tag
    )

    # Get target event ID from e tag (optional - present if zapping an event)
    target_event_id = next(
        (t[1] for t in tags if t[0] == 'e' and len(t) > 1),
        None
    )

    # Fetch profiles (with fallback to relay fetch)
    sender = None
    if sender_pubkey:
        sender = profile_cache.get(sender_pubkey)
        if not sender and profile_fetcher:
            sender = profile_fetcher(sender_pubkey)

    recipient = None
    if recipient_pubkey:
        recipient = profile_cache.get(recipient_pubkey)
        if not recipient and profile_fetcher:
            recipient = profile_fetcher(recipient_pubkey)

    # Fetch target event from database if available
    target_event = None
    target_event_author = None

    if target_event_id and event_db:
        try:
            from .db import get_event_by_id
            target_event = get_event_by_id(event_db, target_event_id)

            if target_event:
                # Fetch target event author's profile
                target_author_pubkey = target_event.get('pubkey')
                if target_author_pubkey:
                    target_event_author = profile_cache.get(target_author_pubkey)
                    if not target_event_author and profile_fetcher:
                        target_event_author = profile_fetcher(target_author_pubkey)
        except Exception as e:
            # If event not found in DB, that's okay - it might be from another feed
            pass

    # Format time
    formatted_time = format_relative_time(zap_event.get('created_at', 0))

    return {
        'event': zap_event,
        'deps': {
            'sender': sender,
            'recipient': recipient,
            'target_event': target_event,
            'target_event_author': target_event_author
        },
        'meta': {
            'amount_sats': amount_sats,
            'amount_msats': amount_sats * 1000,
            'message': message,
            'formatted_time': formatted_time,
            'target_event_id': target_event_id,  # For debugging
            'is_profile_zap': target_event_id is None,
            'is_event_zap': target_event_id is not None
        }
    }
