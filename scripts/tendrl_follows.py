#!/usr/bin/env python3
"""
Tendrl Follows Helper

Fetches a user's kind-3 follow list and generates filtered feeds.

Usage:
    # Fetch follow list for an npub
    python tendrl_follows.py --npub npub1... --output follows.json

    # Query feed with follows filter
    python tendrl_follows.py --npub npub1... --query-feed notes_enriched
"""

import argparse
import json
import subprocess
from typing import List, Set
import sys


def npub_to_hex(npub: str) -> str:
    """Convert npub to hex pubkey using nak"""
    try:
        result = subprocess.run(
            ['nak', 'decode', npub],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Error decoding npub: {e.stderr}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("Error: 'nak' not found. Install: go install github.com/fiatjaf/nak@latest", file=sys.stderr)
        sys.exit(1)


def hex_to_npub(hex_pk: str) -> str:
    """Convert hex pubkey to npub using nak"""
    try:
        result = subprocess.run(
            ['nak', 'encode', 'npub', hex_pk],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except:
        return hex_pk  # Return hex if conversion fails


def fetch_follow_list(pubkey_hex: str, config_path: str = "./tendrl/tendrl_enrichment_test.toml") -> Set[str]:
    """Fetch kind-3 follow list from nostrdb"""
    print(f"Fetching follow list for {pubkey_hex[:8]}...", file=sys.stderr)

    try:
        # Query nostrdb for kind-3 event
        result = subprocess.run(
            [
                './target/release/tendrl_query',
                '--kinds', '3',
                '--author', pubkey_hex,
                '--limit', '1',
                '--config', config_path
            ],
            capture_output=True,
            text=True,
            check=True
        )

        if not result.stdout.strip():
            print(f"Warning: No follow list found for {pubkey_hex[:8]}", file=sys.stderr)
            return set()

        # Parse the kind-3 event
        follow_event = json.loads(result.stdout)

        # Extract p-tags (followed pubkeys)
        follows = set()
        for tag in follow_event.get('tags', []):
            if len(tag) >= 2 and tag[0] == 'p':
                follows.add(tag[1])

        print(f"Found {len(follows)} follows", file=sys.stderr)
        return follows

    except subprocess.CalledProcessError as e:
        print(f"Error querying follow list: {e.stderr}", file=sys.stderr)
        return set()
    except json.JSONDecodeError as e:
        print(f"Error parsing follow list: {e}", file=sys.stderr)
        return set()


def query_follows_feed(
    pubkey_hex: str,
    feed_name: str = "notes_enriched",
    config_path: str = "./tendrl/tendrl_enrichment_test.toml",
    limit: int = 50
) -> List[dict]:
    """Query feed filtered by follow list"""

    # Fetch follow list
    follows = fetch_follow_list(pubkey_hex, config_path)

    if not follows:
        print("Warning: Empty follow list, showing global feed", file=sys.stderr)

    # Query events from follows
    print(f"Querying {feed_name} for {len(follows)} authors...", file=sys.stderr)

    try:
        # Query enriched feed
        result = subprocess.run(
            [
                './target/release/tendrl_query',
                '--feed', feed_name,
                '--config', config_path,
                '--limit', str(limit)
            ],
            capture_output=True,
            text=True,
            check=True
        )

        # Parse JSONL
        events = []
        for line in result.stdout.strip().split('\n'):
            if line:
                event = json.loads(line)

                # Filter by follows (if we have a follow list)
                if follows and event['pubkey'] not in follows:
                    continue

                events.append(event)

        print(f"Found {len(events)} events from follows", file=sys.stderr)
        return events

    except subprocess.CalledProcessError as e:
        print(f"Error querying feed: {e.stderr}", file=sys.stderr)
        return []
    except json.JSONDecodeError as e:
        print(f"Error parsing feed: {e}", file=sys.stderr)
        return []


def save_follow_list(follows: Set[str], output_path: str):
    """Save follow list to JSON file"""
    data = {
        'count': len(follows),
        'follows': list(follows)
    }

    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"Saved {len(follows)} follows to {output_path}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Tendrl Follows Helper - Filter feeds by follow list"
    )
    parser.add_argument(
        '--npub',
        help='User npub (will be converted to hex)'
    )
    parser.add_argument(
        '--pubkey',
        help='User pubkey (hex format)'
    )
    parser.add_argument(
        '--config',
        default='./tendrl/tendrl_enrichment_test.toml',
        help='Tendrl config path'
    )
    parser.add_argument(
        '--query-feed',
        help='Query this feed filtered by follows'
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=50,
        help='Limit number of events'
    )
    parser.add_argument(
        '--output',
        help='Save follow list to file'
    )

    args = parser.parse_args()

    # Get pubkey
    if args.npub:
        pubkey_hex = npub_to_hex(args.npub)
        print(f"Converted npub to: {pubkey_hex}", file=sys.stderr)
    elif args.pubkey:
        pubkey_hex = args.pubkey
    else:
        parser.error("Either --npub or --pubkey required")

    # Fetch follow list
    follows = fetch_follow_list(pubkey_hex, args.config)

    # Save if requested
    if args.output:
        save_follow_list(follows, args.output)

    # Query feed if requested
    if args.query_feed:
        events = query_follows_feed(
            pubkey_hex,
            args.query_feed,
            args.config,
            args.limit
        )

        # Output JSONL
        for event in events:
            print(json.dumps(event))

    # If neither output nor query, just show stats
    if not args.output and not args.query_feed:
        print(f"\nFollow list for {pubkey_hex[:8]}:")
        print(f"  Total follows: {len(follows)}")
        print(f"\nFirst 10 follows:")
        for pk in list(follows)[:10]:
            npub = hex_to_npub(pk)
            print(f"  - {pk[:8]}... ({npub[:16]}...)")


if __name__ == '__main__':
    main()


"""
Example usage:

# 1. Fetch and save follow list
python tendrl_follows.py \\
  --npub npub1m3xdppkd0njmrqe2ma8a6ys39zvgp5k8u22mev8xsnqp4nh80srqhqa5sf \\
  --output my_follows.json

# 2. Query feed filtered by follows
python tendrl_follows.py \\
  --npub npub1m3xdppkd0njmrqe2ma8a6ys39zvgp5k8u22mev8xsnqp4nh80srqhqa5sf \\
  --query-feed notes_enriched \\
  --limit 100

# 3. Pipe to renderer
python tendrl_follows.py \\
  --npub npub1... \\
  --query-feed notes_enriched \\
  --limit 50 | python tendrl_render.py --stdin

# 4. Just show follow list stats
python tendrl_follows.py --npub npub1...
"""
