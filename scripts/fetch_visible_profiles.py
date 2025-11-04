#!/usr/bin/env python3
"""
Fetch profiles for currently visible feed authors.

This is called BEFORE rendering to ensure all visible notes have profiles.
Optimized for parallel fetching to handle 28+ profiles in under 30 seconds.
"""

import subprocess
import asyncio
import json
import sys
import time
from typing import Set, List, Tuple
from concurrent.futures import ThreadPoolExecutor

def get_visible_authors(config_path: str, feed_name: str, limit: int = 100) -> Set[str]:
    """Get authors from the visible feed"""
    result = subprocess.run(
        ['./target/release/tendrl_query', '--feed', feed_name, '--limit', str(limit), '--config', config_path],
        capture_output=True,
        text=True
    )

    authors = set()
    for line in result.stdout.strip().split('\n'):
        if line:
            event = json.loads(line)
            authors.add(event['pubkey'])

    return authors

def check_profile_exists(pubkey: str, config_path: str) -> bool:
    """Check if profile exists in DB"""
    result = subprocess.run(
        ['./target/release/tendrl_query', '--kinds', '0', '--author', pubkey, '--limit', '1', '--config', config_path],
        capture_output=True,
        text=True
    )
    return bool(result.stdout.strip())

async def fetch_profile(pubkey: str, relays: List[str], semaphore: asyncio.Semaphore) -> Tuple[str, str]:
    """Fetch profile from relays using nak (async with timeout)"""
    async with semaphore:  # Limit concurrent requests
        try:
            process = await asyncio.create_subprocess_exec(
                'nak', 'req', '-k', '0', '-a', pubkey, '-l', '1', *relays,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=5.0)
            return (pubkey, stdout.decode().strip())
        except asyncio.TimeoutError:
            return (pubkey, "")
        except Exception as e:
            return (pubkey, "")

def store_profile(profile_json: str) -> bool:
    """Store profile in nostrdb"""
    if not profile_json:
        return False

    try:
        result = subprocess.run(
            ['./target/release/tendrl_ingest'],
            input=profile_json,
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False

async def fetch_and_store_batch(missing_authors: List[str], relays: List[str], max_concurrent: int = 10) -> Tuple[int, int]:
    """Fetch multiple profiles concurrently"""
    semaphore = asyncio.Semaphore(max_concurrent)

    # Create all fetch tasks
    fetch_tasks = [fetch_profile(author, relays, semaphore) for author in missing_authors]

    # Execute all fetches concurrently
    results = await asyncio.gather(*fetch_tasks)

    # Process results and store profiles
    fetched = 0
    failed = 0

    for i, (pubkey, profile_json) in enumerate(results):
        print(f"[{i+1}/{len(results)}] {pubkey[:8]}...", end=' ', flush=True)

        if profile_json and store_profile(profile_json):
            print("✓")
            fetched += 1
        else:
            print("✗")
            failed += 1

    return fetched, failed

async def async_main(config_path: str, feed_name: str, limit: int):
    """Main async function"""
    # Default profile relays
    relays = [
        'wss://purplepag.es',
        'wss://relay.nostr.band',
        'wss://relay.damus.io'
    ]

    print(f"Fetching profiles for visible {feed_name} authors...")

    # Get visible authors (sync operation)
    authors = get_visible_authors(config_path, feed_name, limit)
    print(f"Found {len(authors)} unique authors in feed")

    # Check which need profiles (sync operation)
    missing = []
    for author in authors:
        if not check_profile_exists(author, config_path):
            missing.append(author)

    if not missing:
        print("✓ All authors have profiles!")
        return 0

    print(f"Missing {len(missing)} profiles - fetching now (parallel mode)...")

    start_time = time.time()

    # Fetch all profiles concurrently (max 10 concurrent requests)
    fetched, failed = await fetch_and_store_batch(missing, relays, max_concurrent=10)

    elapsed = time.time() - start_time

    print(f"\n✓ Fetched {fetched} profiles in {elapsed:.1f}s")
    if failed > 0:
        print(f"✗ Failed to fetch {failed} profiles (may not exist on relays)")

    return 0

def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else 'tendrl/tendrl_enrichment_test.toml'
    feed_name = sys.argv[2] if len(sys.argv) > 2 else 'notes_enriched'
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 100

    return asyncio.run(async_main(config_path, feed_name, limit))

if __name__ == '__main__':
    sys.exit(main())
