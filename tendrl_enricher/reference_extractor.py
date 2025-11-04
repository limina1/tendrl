"""
Reference extraction for nostr events.

Extracts and decodes nostr: URIs and tag references from events,
enabling contextual rendering of referenced events.
"""

import re
import subprocess
import json
from typing import Dict, Any, List, Optional, Tuple


def extract_nostr_uris(content: str) -> List[Dict[str, Any]]:
    """
    Extract all nostr: URIs from event content.

    Args:
        content: Event content string

    Returns:
        List of reference dictionaries with decoded metadata

    Examples:
        >>> refs = extract_nostr_uris("Check out nostr:nevent1... and nostr:npub1...")
        >>> len(refs)
        2
        >>> refs[0]['type']
        'nevent'
    """
    # Pattern for nostr: URIs
    # Matches: nostr:nevent1..., nostr:npub1..., nostr:naddr1..., nostr:note1...
    pattern = r'nostr:((?:nevent|npub|note|nprofile|naddr|nsec)[a-zA-Z0-9]+)'

    matches = re.findall(pattern, content)
    references = []

    for identifier in matches:
        decoded = decode_identifier(identifier)
        if decoded:
            references.append(decoded)

    return references


def extract_event_references(event: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract event references from tags (e and a tags).

    Args:
        event: Event dictionary

    Returns:
        List of reference dictionaries

    Examples:
        >>> event = {'tags': [['e', 'abc123...'], ['a', '30023:pubkey:article']]}
        >>> refs = extract_event_references(event)
        >>> len(refs)
        2
    """
    references = []
    tags = event.get('tags', [])

    for tag in tags:
        if not tag or len(tag) < 2:
            continue

        tag_type = tag[0]
        tag_value = tag[1]

        # e tag: regular event reference
        if tag_type == 'e':
            ref = {
                'type': 'event',
                'source': 'e_tag',
                'event_id': tag_value,
                'relay': tag[2] if len(tag) > 2 else None,
                'marker': tag[3] if len(tag) > 3 else None
            }
            references.append(ref)

        # a tag: addressable event reference (NIP-33)
        elif tag_type == 'a':
            # Format: kind:pubkey:d-tag
            try:
                parts = tag_value.split(':', 2)
                if len(parts) == 3:
                    ref = {
                        'type': 'addressable',
                        'source': 'a_tag',
                        'kind': int(parts[0]),
                        'pubkey': parts[1],
                        'd_tag': parts[2],
                        'address': tag_value,
                        'relay': tag[2] if len(tag) > 2 else None
                    }
                    references.append(ref)
            except (ValueError, IndexError):
                # Invalid format, skip
                pass

    return references


def decode_identifier(identifier: str) -> Optional[Dict[str, Any]]:
    """
    Decode a bech32 nostr identifier using nak CLI.

    Args:
        identifier: Bech32 identifier (nevent1..., npub1..., naddr1..., etc.)

    Returns:
        Decoded reference dictionary or None if decoding fails

    Examples:
        >>> ref = decode_identifier('nevent1...')
        >>> ref['type']
        'nevent'
        >>> ref['event_id']
        'abc123...'
    """
    try:
        result = subprocess.run(
            ['nak', 'decode', identifier],
            capture_output=True,
            text=True,
            check=True,
            timeout=2
        )

        decoded = json.loads(result.stdout.strip())

        # Determine identifier type
        if identifier.startswith('nevent1'):
            return {
                'type': 'nevent',
                'source': 'content',
                'identifier': identifier,
                'event_id': decoded.get('id'),
                'kind': decoded.get('kind'),
                'author': decoded.get('author'),
                'relays': decoded.get('relays', [])
            }

        elif identifier.startswith('naddr1'):
            return {
                'type': 'naddr',
                'source': 'content',
                'identifier': identifier,
                'kind': decoded.get('kind'),
                'pubkey': decoded.get('pubkey'),
                'd_tag': decoded.get('identifier', ''),
                'relays': decoded.get('relays', [])
            }

        elif identifier.startswith('npub1'):
            return {
                'type': 'npub',
                'source': 'content',
                'identifier': identifier,
                'pubkey': decoded
            }

        elif identifier.startswith('nprofile1'):
            return {
                'type': 'nprofile',
                'source': 'content',
                'identifier': identifier,
                'pubkey': decoded.get('pubkey'),
                'relays': decoded.get('relays', [])
            }

        elif identifier.startswith('note1'):
            # note is deprecated, but handle it
            return {
                'type': 'note',
                'source': 'content',
                'identifier': identifier,
                'event_id': decoded
            }

        return None

    except (subprocess.CalledProcessError, json.JSONDecodeError,
            subprocess.TimeoutExpired, FileNotFoundError):
        return None


def extract_all_references(event: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Extract all references from an event (both content and tags).

    Args:
        event: Event dictionary

    Returns:
        Dictionary with 'content_refs' and 'tag_refs' lists

    Examples:
        >>> event = {
        ...     'content': 'Check out nostr:nevent1...',
        ...     'tags': [['e', 'abc123...']]
        ... }
        >>> refs = extract_all_references(event)
        >>> len(refs['content_refs'])
        1
        >>> len(refs['tag_refs'])
        1
    """
    content = event.get('content', '')

    return {
        'content_refs': extract_nostr_uris(content),
        'tag_refs': extract_event_references(event)
    }


def identify_reference_kind(ref: Dict[str, Any]) -> Optional[int]:
    """
    Identify the event kind of a reference.

    Args:
        ref: Reference dictionary from extraction

    Returns:
        Event kind number or None if unknown

    Examples:
        >>> ref = {'type': 'nevent', 'kind': 1}
        >>> identify_reference_kind(ref)
        1
        >>> ref = {'type': 'naddr', 'kind': 30023}
        >>> identify_reference_kind(ref)
        30023
    """
    # Direct kind in reference
    if 'kind' in ref and ref['kind'] is not None:
        return ref['kind']

    # For addressable events, kind is required
    if ref['type'] in ('naddr', 'addressable'):
        return ref.get('kind')

    # For regular events (nevent, event, note), kind might not be known
    # without fetching the event
    return None


def get_reference_identifier(ref: Dict[str, Any]) -> Tuple[str, str]:
    """
    Get the identifier type and value for fetching the referenced event.

    Args:
        ref: Reference dictionary

    Returns:
        Tuple of (identifier_type, identifier_value)
        - identifier_type: 'event_id' | 'address'
        - identifier_value: The ID or address string

    Examples:
        >>> ref = {'type': 'nevent', 'event_id': 'abc123...'}
        >>> get_reference_identifier(ref)
        ('event_id', 'abc123...')
        >>> ref = {'type': 'naddr', 'address': '30023:pubkey:article'}
        >>> get_reference_identifier(ref)
        ('address', '30023:pubkey:article')
    """
    if ref['type'] in ('nevent', 'event', 'note'):
        return ('event_id', ref.get('event_id'))

    elif ref['type'] in ('naddr', 'addressable'):
        # Construct address if not present
        if 'address' in ref:
            return ('address', ref['address'])
        else:
            kind = ref['kind']
            pubkey = ref['pubkey']
            d_tag = ref.get('d_tag', '')
            address = f"{kind}:{pubkey}:{d_tag}"
            return ('address', address)

    return (None, None)


def test_extraction():
    """Test reference extraction with sample data."""

    # Test content extraction
    content = """
    Check out this article: nostr:naddr1qq9rzd3cxycrqve5xqmnjv3nxqckuvfcqyg8wumn8ghj7mn0wd68ytn6dpexgcnyvf4xqmkzvfexymrsv3jxqcsqqqqqsqqqqa9l0g4

    And this note: nostr:nevent1qvzqqqqqqypzqak8r2hr5jglrk0wc37t59lz98x6gyf6pwaku6hpwakhvslznjh6qydhwumn8ghj7argv4nx7un9wd6zumn0wd68yvfwvdhk6tcqyz80sg96hrqcf5mqz9h23vtc3dpvvexj07qwmn24802pjxlm3njvylr463j
    """

    content_refs = extract_nostr_uris(content)
    print(f"Found {len(content_refs)} content references:")
    for ref in content_refs:
        print(f"  - {ref['type']}: kind={ref.get('kind', 'unknown')}")

    # Test tag extraction
    event = {
        'tags': [
            ['e', 'abc123def456'],
            ['a', '30023:pubkey123:my-article'],
            ['p', 'pubkey456']
        ]
    }

    tag_refs = extract_event_references(event)
    print(f"\nFound {len(tag_refs)} tag references:")
    for ref in tag_refs:
        print(f"  - {ref['type']}: {ref.get('event_id') or ref.get('address')}")


if __name__ == '__main__':
    test_extraction()
