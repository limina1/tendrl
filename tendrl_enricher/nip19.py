"""
NIP-19 bech32 encoding/decoding support for nostr-feeds

Handles decoding of nevent identifiers using nak CLI.
"""

import json
import subprocess
from typing import Dict, List, Optional, Any


class NIP19Error(Exception):
    """Base exception for NIP-19 related errors."""
    pass


def decode_nevent(nevent: str) -> Dict[str, Any]:
    """
    Decode nevent (NIP-19) using nak CLI.

    Args:
        nevent: nevent1... bech32 encoded event identifier

    Returns:
        Dictionary with:
        - event_id: Hex event ID (32 bytes)
        - relays: List of relay URLs (optional)
        - author: Hex pubkey (optional, if included in nevent)
        - kind: Event kind (optional, if included in nevent)

    Raises:
        NIP19Error: If decoding fails
        ValueError: If not a valid nevent

    Examples:
        >>> result = decode_nevent('nevent1qqsqm3...')
        >>> result['event_id']
        'abc123def456...'
        >>> result['relays']
        ['wss://relay.damus.io', 'wss://nos.lol']
    """
    # Validate format
    if not nevent.startswith('nevent1'):
        raise ValueError(f"Not a valid nevent: {nevent}")

    # Use nak decode to parse
    try:
        result = subprocess.run(
            ['nak', 'decode', nevent],
            capture_output=True,
            text=True,
            check=True,
            timeout=5
        )

        # nak decode returns JSON with TLV data
        decoded = json.loads(result.stdout.strip())

        # Extract event ID (required)
        event_id = decoded.get('id')
        if not event_id:
            raise NIP19Error(f"No event ID in decoded nevent: {decoded}")

        # Validate hex format
        if len(event_id) != 64 or not all(c in '0123456789abcdef' for c in event_id.lower()):
            raise NIP19Error(f"Invalid event ID format: {event_id}")

        # Build result
        result_dict = {
            'event_id': event_id.lower(),
            'relays': decoded.get('relays', []),
            'author': decoded.get('author'),
            'kind': decoded.get('kind')
        }

        return result_dict

    except subprocess.CalledProcessError as e:
        raise NIP19Error(f"nak decode failed: {e.stderr}")
    except subprocess.TimeoutExpired:
        raise NIP19Error("nak decode timed out")
    except FileNotFoundError:
        raise NIP19Error("nak CLI not found. Install from: https://github.com/fiatjaf/nak")
    except json.JSONDecodeError as e:
        raise NIP19Error(f"Failed to parse nak output: {e}")


def decode_naddr(naddr: str) -> Dict[str, Any]:
    """
    Decode naddr (NIP-19 addressable event coordinate) using nak CLI.

    Args:
        naddr: naddr1... bech32 encoded addressable event coordinate

    Returns:
        Dictionary with:
        - kind: Event kind (30000+)
        - pubkey: Author's hex pubkey
        - identifier: d-tag value
        - relays: List of relay URLs (optional)

    Raises:
        NIP19Error: If decoding fails
        ValueError: If not a valid naddr

    Examples:
        >>> result = decode_naddr('naddr1qqxnzd3c...')
        >>> result['kind']
        30023
        >>> result['identifier']
        'my-article-slug'
    """
    # Validate format
    if not naddr.startswith('naddr1'):
        raise ValueError(f"Not a valid naddr: {naddr}")

    # Use nak decode to parse
    try:
        result = subprocess.run(
            ['nak', 'decode', naddr],
            capture_output=True,
            text=True,
            check=True,
            timeout=5
        )

        # nak decode returns JSON with coordinate data
        decoded = json.loads(result.stdout.strip())

        # Extract required fields
        kind = decoded.get('kind')
        pubkey = decoded.get('pubkey')
        identifier = decoded.get('identifier', '')

        if not kind or not pubkey:
            raise NIP19Error(f"Missing required fields in decoded naddr: {decoded}")

        # Build result
        result_dict = {
            'kind': kind,
            'pubkey': pubkey,
            'identifier': identifier,
            'relays': decoded.get('relays', [])
        }

        return result_dict

    except subprocess.CalledProcessError as e:
        raise NIP19Error(f"nak decode failed: {e.stderr}")
    except subprocess.TimeoutExpired:
        raise NIP19Error("nak decode timed out")
    except FileNotFoundError:
        raise NIP19Error("nak CLI not found. Install from: https://github.com/fiatjaf/nak")
    except json.JSONDecodeError as e:
        raise NIP19Error(f"Failed to parse nak output: {e}")


def is_nevent(identifier: str) -> bool:
    """
    Check if string is a nevent identifier.

    Args:
        identifier: String to check

    Returns:
        True if starts with 'nevent1'

    Examples:
        >>> is_nevent('nevent1qqsqm3...')
        True
        >>> is_nevent('abc123def456...')
        False
    """
    return identifier.startswith('nevent1')


def is_hex_event_id(identifier: str) -> bool:
    """
    Check if string is a hex event ID.

    Args:
        identifier: String to check

    Returns:
        True if 64 hex characters

    Examples:
        >>> is_hex_event_id('abc123def456...' * 8)  # 64 chars
        True
        >>> is_hex_event_id('nevent1qqsqm3...')
        False
    """
    return (
        len(identifier) == 64 and
        all(c in '0123456789abcdef' for c in identifier.lower())
    )


def normalize_event_identifier(identifier: str) -> Dict[str, Any]:
    """
    Normalize event identifier (nevent or hex) to standard format.

    Args:
        identifier: nevent1... or hex event ID

    Returns:
        Dictionary with event_id and optional metadata

    Raises:
        ValueError: If identifier format is invalid
        NIP19Error: If nevent decoding fails

    Examples:
        >>> result = normalize_event_identifier('nevent1qqsqm3...')
        >>> result['event_id']
        'abc123...'
        >>> result = normalize_event_identifier('abc123...')
        >>> result['event_id']
        'abc123...'
    """
    # Check if nevent
    if is_nevent(identifier):
        return decode_nevent(identifier)

    # Check if hex event ID
    elif is_hex_event_id(identifier):
        return {
            'event_id': identifier.lower(),
            'relays': [],
            'author': None,
            'kind': None
        }

    else:
        raise ValueError(
            f"Invalid event identifier: {identifier}\n"
            f"Expected nevent1... or 64-character hex event ID"
        )


def encode_nevent_simple(event_id: str, author: Optional[str] = None) -> str:
    """
    Encode hex event ID as nevent (NIP-19) - modern replacement for 'note'.

    Note: 'note' encoding is deprecated. Use nevent for all regular events.

    Args:
        event_id: 64-char hex event ID
        author: Optional hex pubkey (recommended for better discoverability)

    Returns:
        nevent1... bech32 encoded identifier

    Examples:
        >>> encode_nevent_simple('abc123...')
        'nevent1...'
        >>> encode_nevent_simple('abc123...', author='def456...')
        'nevent1...'
    """
    try:
        cmd = ['nak', 'encode', 'nevent']

        # Add author hint if provided
        if author:
            cmd.extend(['--author', author])

        # Event ID is positional
        cmd.append(event_id)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=5
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        raise NIP19Error(f"nak encode failed: {e.stderr}")
    except subprocess.TimeoutExpired:
        raise NIP19Error("nak encode timed out")
    except FileNotFoundError:
        raise NIP19Error("nak CLI not found")


def encode_nevent(event_id: str, relays: Optional[List[str]] = None,
                  author: Optional[str] = None, kind: Optional[int] = None) -> str:
    """
    Encode event with metadata as nevent (NIP-19).

    Args:
        event_id: 64-char hex event ID
        relays: Optional list of relay URLs
        author: Optional hex pubkey
        kind: Optional event kind

    Returns:
        nevent1... bech32 encoded identifier with metadata

    Examples:
        >>> encode_nevent('abc123...', relays=['wss://relay.damus.io'])
        'nevent1...'
    """
    try:
        cmd = ['nak', 'encode', 'nevent']

        # Add relays
        if relays:
            for relay in relays:
                cmd.extend(['--relay', relay])

        # Add author
        if author:
            cmd.extend(['--author', author])

        # Add kind
        if kind is not None:
            cmd.extend(['--kind', str(kind)])

        # Event ID is positional argument (last)
        cmd.append(event_id)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=5
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        raise NIP19Error(f"nak encode failed: {e.stderr}")
    except subprocess.TimeoutExpired:
        raise NIP19Error("nak encode timed out")
    except FileNotFoundError:
        raise NIP19Error("nak CLI not found")


def encode_npub(pubkey: str) -> str:
    """
    Encode hex pubkey as npub (NIP-19).

    Args:
        pubkey: 64-char hex pubkey

    Returns:
        npub1... bech32 encoded pubkey

    Examples:
        >>> encode_npub('abc123...')
        'npub1...'
    """
    try:
        result = subprocess.run(
            ['nak', 'encode', 'npub', pubkey],
            capture_output=True,
            text=True,
            check=True,
            timeout=5
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        raise NIP19Error(f"nak encode failed: {e.stderr}")
    except subprocess.TimeoutExpired:
        raise NIP19Error("nak encode timed out")
    except FileNotFoundError:
        raise NIP19Error("nak CLI not found")


def encode_nprofile(pubkey: str, relays: Optional[List[str]] = None) -> str:
    """
    Encode pubkey with relays as nprofile (NIP-19).

    Args:
        pubkey: 64-char hex pubkey
        relays: Optional list of relay URLs

    Returns:
        nprofile1... bech32 encoded profile with relay hints

    Examples:
        >>> encode_nprofile('abc123...', relays=['wss://relay.damus.io'])
        'nprofile1...'
    """
    try:
        cmd = ['nak', 'encode', 'nprofile']

        # Add relays
        if relays:
            for relay in relays:
                cmd.extend(['--relay', relay])

        # Pubkey is positional argument (last)
        cmd.append(pubkey)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=5
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        raise NIP19Error(f"nak encode failed: {e.stderr}")
    except subprocess.TimeoutExpired:
        raise NIP19Error("nak encode timed out")
    except FileNotFoundError:
        raise NIP19Error("nak CLI not found")


def encode_naddr(kind: int, pubkey: str, d_tag: str = "",
                 relays: Optional[List[str]] = None) -> str:
    """
    Encode addressable/replaceable event as naddr (NIP-19).

    Used for kind 30000+ events (articles, channels, wiki pages, etc.)

    Args:
        kind: Event kind (30000+)
        pubkey: Author's hex pubkey
        d_tag: Identifier from 'd' tag (empty string for normal replaceable)
        relays: Optional relay hints

    Returns:
        naddr1... bech32 encoded addressable event coordinate

    Examples:
        >>> encode_naddr(30023, 'abc123...', 'my-article')
        'naddr1...'
    """
    try:
        cmd = ['nak', 'encode', 'naddr']

        # Build coordinate: kind:pubkey:d-tag
        cmd.extend(['--identifier', d_tag])
        cmd.extend(['--pubkey', pubkey])
        cmd.extend(['--kind', str(kind)])

        # Add relay hints
        if relays:
            for relay in relays:
                cmd.extend(['--relay', relay])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=5
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        raise NIP19Error(f"nak encode failed: {e.stderr}")
    except subprocess.TimeoutExpired:
        raise NIP19Error("nak encode timed out")
    except FileNotFoundError:
        raise NIP19Error("nak CLI not found")


def to_nostr_uri(identifier: str) -> str:
    """
    Convert bech32 identifier to nostr: URI (NIP-21).

    Args:
        identifier: note1.../nevent1.../npub1.../nprofile1... identifier

    Returns:
        nostr:... URI

    Examples:
        >>> to_nostr_uri('note1...')
        'nostr:note1...'
        >>> to_nostr_uri('npub1...')
        'nostr:npub1...'
    """
    if identifier.startswith('nostr:'):
        return identifier
    return f'nostr:{identifier}'


def test_decode_nevent():
    """
    Test nevent decoding with example from user.

    Example nevent from NIP-19 spec:
    nevent1qvzqqqqqqypzqak8r2hr5jglrk0wc37t59lz98x6gyf6pwaku6hpwakhvslznjh6qydhwumn8ghj7argv4nx7un9wd6zumn0wd68yvfwvdhk6tcqyz80sg96hrqcf5mqz9h23vtc3dpvvexj07qwmn24802pjxlm3njvylr463j
    """
    test_nevent = "nevent1qvzqqqqqqypzqak8r2hr5jglrk0wc37t59lz98x6gyf6pwaku6hpwakhvslznjh6qydhwumn8ghj7argv4nx7un9wd6zumn0wd68yvfwvdhk6tcqyz80sg96hrqcf5mqz9h23vtc3dpvvexj07qwmn24802pjxlm3njvylr463j"

    try:
        result = decode_nevent(test_nevent)
        print("✓ Decoded nevent:")
        print(f"  Event ID: {result['event_id']}")
        print(f"  Relays: {result['relays']}")
        print(f"  Author: {result.get('author', 'N/A')}")
        print(f"  Kind: {result.get('kind', 'N/A')}")
        return result
    except NIP19Error as e:
        print(f"✗ Failed to decode: {e}")
        return None


if __name__ == '__main__':
    # Test with user's example
    test_decode_nevent()
