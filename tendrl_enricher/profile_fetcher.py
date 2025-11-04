"""
Profile fetcher for missing profiles

Fetches kind 0 profiles from relays using nak when not in cache.
"""

import subprocess
import json
from typing import Optional, List, Dict, Any


def fetch_profile_from_relay(
    pubkey: str,
    relays: List[str],
    timeout: int = 5
) -> Optional[Dict[str, Any]]:
    """
    Fetch kind 0 profile from relays using nak.

    Args:
        pubkey: Hex pubkey to fetch
        relays: List of relay URLs
        timeout: Timeout in seconds

    Returns:
        Profile dictionary or None if not found

    Examples:
        >>> relays = ['wss://relay.damus.io', 'wss://nos.lol']
        >>> profile = fetch_profile_from_relay('abc123...', relays)
        >>> profile['name']
        'alice'
    """
    if not pubkey or not relays:
        return None

    try:
        # Build nak command: nak req -k 0 -a <pubkey> --limit 1 <relays...>
        cmd = ['nak', 'req', '-k', '0', '-a', pubkey, '--limit', '1']
        cmd.extend(relays)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        if result.returncode != 0:
            return None

        # Parse first line (JSONL)
        for line in result.stdout.strip().split('\n'):
            if line.strip():
                try:
                    event = json.loads(line)

                    # Verify it's kind 0 and matches pubkey
                    if event.get('kind') == 0 and event.get('pubkey') == pubkey:
                        # Parse content JSON
                        content = event.get('content', '{}')
                        try:
                            profile_data = json.loads(content)
                            profile_data['pubkey'] = pubkey
                            profile_data['created_at'] = event.get('created_at')
                            return profile_data
                        except json.JSONDecodeError:
                            return {'pubkey': pubkey}

                except json.JSONDecodeError:
                    continue

        return None

    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return None


def fetch_profiles_batch(
    pubkeys: List[str],
    relays: List[str],
    timeout: int = 10
) -> Dict[str, Dict[str, Any]]:
    """
    Fetch multiple profiles in one request.

    Args:
        pubkeys: List of hex pubkeys
        relays: List of relay URLs
        timeout: Timeout in seconds

    Returns:
        Dictionary mapping pubkey to profile

    Examples:
        >>> pubkeys = ['abc123...', 'def456...']
        >>> profiles = fetch_profiles_batch(pubkeys, relays)
        >>> profiles['abc123']['name']
        'alice'
    """
    if not pubkeys or not relays:
        return {}

    try:
        # Build nak command with multiple -a flags
        cmd = ['nak', 'req', '-k', '0']
        for pubkey in pubkeys[:100]:  # Limit to 100 at a time
            cmd.extend(['-a', pubkey])
        cmd.extend(['--limit', str(len(pubkeys))])
        cmd.extend(relays)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        if result.returncode != 0:
            return {}

        # Parse all profiles
        profiles = {}
        for line in result.stdout.strip().split('\n'):
            if line.strip():
                try:
                    event = json.loads(line)

                    if event.get('kind') == 0:
                        pubkey = event.get('pubkey')
                        if pubkey:
                            content = event.get('content', '{}')
                            try:
                                profile_data = json.loads(content)
                                profile_data['pubkey'] = pubkey
                                profile_data['created_at'] = event.get('created_at')
                                profiles[pubkey] = profile_data
                            except json.JSONDecodeError:
                                profiles[pubkey] = {'pubkey': pubkey}

                except json.JSONDecodeError:
                    continue

        return profiles

    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return {}
