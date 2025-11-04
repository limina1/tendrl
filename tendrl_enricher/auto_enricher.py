"""
Auto-enricher for referenced events.

Automatically detects and enriches events referenced in e/a tags
or nostr: URIs in content, making them available to templates
for contextual preview rendering.
"""

import sys
from typing import Dict, Any, List, Optional
from .reference_extractor import extract_all_references, get_reference_identifier
from .db import get_event_by_id, get_addressable_event
from .nip19 import decode_nevent, is_nevent, NIP19Error
import subprocess
import json


def fetch_event_from_relay(event_id: str, relays: List[str], timeout: int = 5) -> Optional[Dict[str, Any]]:
    """
    Fetch an event from relays using nak CLI.

    Args:
        event_id: Hex event ID
        relays: List of relay URLs
        timeout: Timeout in seconds

    Returns:
        Event dictionary or None if not found
    """
    if not relays:
        relays = ["wss://relay.damus.io", "wss://nos.lol"]

    # Build nak req command
    relay_args = " ".join(relays)
    cmd = f"nak req -i {event_id} {relay_args}"

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        if result.returncode == 0 and result.stdout.strip():
            # Parse first line (should be the event)
            for line in result.stdout.strip().split('\n'):
                if line.strip():
                    event = json.loads(line)
                    return event

    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        print(f"Warning: Failed to fetch event {event_id[:8]} from relays: {e}")

    return None


def auto_enrich_references(
    event: Dict[str, Any],
    enriched_event: Dict[str, Any],
    feed_db,
    profile_cache,
    enricher,
    max_references: int = 5,
    fetch_from_relays: bool = True,
    default_relays: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Automatically enrich referenced events from e/a tags and content.

    This function:
    1. Extracts all references from the event (tags and content)
    2. Fetches referenced events from database
    3. If not in DB and fetch_from_relays=True, tries to fetch from relays
    4. Enriches them with basic deps (author, stats)
    5. Adds them to deps['referenced_events'] for template access
    6. Tracks unfetched references in deps['unfetched_references']

    Args:
        event: Raw event dictionary
        enriched_event: Enriched event dictionary (will be modified)
        feed_db: Database connection for the feed
        profile_cache: Profile cache instance
        enricher: Enricher instance for enriching referenced events
        max_references: Maximum number of references to enrich (default: 5)
        fetch_from_relays: Whether to fetch missing events from relays
        default_relays: Default relays to use if no hints available

    Returns:
        Modified enriched_event with referenced_events added

    Examples:
        >>> event = {'tags': [['e', 'abc123...']], 'content': 'Check this out'}
        >>> enriched = auto_enrich_references(event, enriched, db, cache, enricher)
        >>> len(enriched['deps']['referenced_events'])
        1
    """
    # Extract all references
    refs = extract_all_references(event)
    all_refs = refs['tag_refs'] + refs['content_refs']

    if not all_refs:
        return enriched_event

    # Limit to avoid over-fetching
    all_refs = all_refs[:max_references]

    referenced_events = []
    unfetched_references = []

    # Basic dep config for referenced events (minimal enrichment)
    ref_dep_config = {
        'deps': {
            'author': {
                'kind': 0,
                'relation': 'author',
                'required': True
            }
        }
    }

    for ref in all_refs:
        identifier_type, identifier_value = get_reference_identifier(ref)

        if not identifier_type or not identifier_value:
            continue

        # Fetch the referenced event
        ref_event = None

        if identifier_type == 'event_id':
            ref_event = get_event_by_id(feed_db, identifier_value)

            # If not in DB and relay fetching enabled, try to fetch
            if not ref_event and fetch_from_relays:
                # Extract relay hints from reference if available
                relay_hints = ref.get('relays', [])
                if not relay_hints and default_relays:
                    relay_hints = default_relays

                if relay_hints:
                    print(f"Fetching referenced event {identifier_value[:8]} from relays...", file=sys.stderr)
                    ref_event = fetch_event_from_relay(identifier_value, relay_hints)

                    # Store fetched event to database for future use
                    if ref_event:
                        from .db import store_event
                        try:
                            store_event(feed_db, ref_event)
                            print(f"  Stored fetched event to database", file=sys.stderr)
                        except Exception as e:
                            print(f"  Warning: Failed to store event: {e}", file=sys.stderr)

        elif identifier_type == 'address':
            # Parse address: kind:pubkey:d_tag
            try:
                parts = identifier_value.split(':', 2)
                if len(parts) == 3:
                    a_kind = int(parts[0])
                    a_pubkey = parts[1]
                    a_d_tag = parts[2]
                    ref_event = get_addressable_event(feed_db, a_kind, a_pubkey, a_d_tag)

                    # Try fetching from relays if not in DB
                    if not ref_event and fetch_from_relays:
                        relay_hints = ref.get('relays', [])
                        if not relay_hints and default_relays:
                            relay_hints = default_relays

                        if relay_hints:
                            print(f"Fetching addressable event {a_kind}:{a_pubkey[:8]}:{a_d_tag} from relays...", file=sys.stderr)
                            # Use nak to fetch by kind, author, and d-tag
                            # Note: This is more complex for addressable events
                            # For now, skip - would need more sophisticated relay query
                            pass

            except (ValueError, IndexError):
                continue

        if ref_event:
            # Enrich the referenced event with minimal deps
            try:
                enriched_ref = enricher.enrich_event(ref_event, ref_dep_config, depth=0)

                # Add reference metadata
                enriched_ref['meta']['ref_source'] = ref.get('source', 'unknown')
                enriched_ref['meta']['ref_type'] = ref.get('type', 'unknown')

                referenced_events.append(enriched_ref)

            except Exception as e:
                # Log but don't fail if enrichment fails
                print(f"Warning: Failed to enrich referenced event {ref_event.get('id', 'unknown')[:8]}: {e}")
                continue
        else:
            # Event not found - track as unfetched
            unfetched_ref = {
                'type': ref.get('type'),
                'identifier': ref.get('identifier') if 'identifier' in ref else identifier_value,
                'source': ref.get('source'),
                'relays': ref.get('relays', [])
            }
            unfetched_references.append(unfetched_ref)

    # Add referenced events to deps
    if referenced_events:
        enriched_event['deps']['referenced_events'] = referenced_events

    # Add unfetched references for display
    if unfetched_references:
        enriched_event['deps']['unfetched_references'] = unfetched_references

    return enriched_event


def auto_enrich_quoted_events(
    event: Dict[str, Any],
    enriched_event: Dict[str, Any],
    feed_db,
    profile_cache,
    enricher
) -> Dict[str, Any]:
    """
    Specialized auto-enricher for quoted events (kind 1 with 'q' tag).

    According to NIP-18, quotes use the 'q' tag to reference the quoted event.
    This enricher specifically handles quoted notes.

    Args:
        event: Raw event dictionary
        enriched_event: Enriched event dictionary (will be modified)
        feed_db: Database connection
        profile_cache: Profile cache
        enricher: Enricher instance

    Returns:
        Modified enriched_event with quoted_event added

    Examples:
        >>> event = {'kind': 1, 'tags': [['q', 'abc123...']]}
        >>> enriched = auto_enrich_quoted_events(event, enriched, db, cache, enricher)
        >>> enriched['deps']['quoted_event'] is not None
        True
    """
    # Check for 'q' tag (quote tag per NIP-18)
    quoted_event_id = None
    for tag in event.get('tags', []):
        if tag and tag[0] == 'q' and len(tag) > 1:
            quoted_event_id = tag[1]
            break

    if not quoted_event_id:
        return enriched_event

    # Fetch quoted event
    quoted_event = get_event_by_id(feed_db, quoted_event_id)

    if not quoted_event:
        return enriched_event

    # Enrich with author
    ref_dep_config = {
        'deps': {
            'author': {
                'kind': 0,
                'relation': 'author',
                'required': True
            }
        }
    }

    try:
        enriched_quoted = enricher.enrich_event(quoted_event, ref_dep_config, depth=0)
        enriched_event['deps']['quoted_event'] = enriched_quoted

    except Exception as e:
        print(f"Warning: Failed to enrich quoted event: {e}")

    return enriched_event


def auto_enrich_contact_list(
    event: Dict[str, Any],
    enriched_event: Dict[str, Any],
    profile_cache
) -> Dict[str, Any]:
    """
    Specialized auto-enricher for contact lists (kind 3).

    Extracts followed pubkeys from 'p' tags and adds their profiles.

    Args:
        event: Raw event dictionary (kind 3)
        enriched_event: Enriched event dictionary (will be modified)
        profile_cache: Profile cache

    Returns:
        Modified enriched_event with contacts added

    Examples:
        >>> event = {'kind': 3, 'tags': [['p', 'pubkey1'], ['p', 'pubkey2']]}
        >>> enriched = auto_enrich_contact_list(event, enriched, cache)
        >>> len(enriched['deps']['contacts'])
        2
    """
    if event.get('kind') != 3:
        return enriched_event

    contacts = []

    for tag in event.get('tags', []):
        if not tag or tag[0] != 'p' or len(tag) < 2:
            continue

        pubkey = tag[1]
        relay = tag[2] if len(tag) > 2 else None
        petname = tag[3] if len(tag) > 3 else None

        # Get profile from cache
        profile = profile_cache.get(pubkey)

        if profile:
            contact_info = {
                'pubkey': pubkey,
                'profile': profile,
                'relay': relay,
                'petname': petname
            }
            contacts.append(contact_info)

    if contacts:
        enriched_event['deps']['contacts'] = contacts
        enriched_event['meta']['contact_count'] = len(contacts)

    return enriched_event


def apply_auto_enrichment(
    event: Dict[str, Any],
    enriched_event: Dict[str, Any],
    feed_db,
    profile_cache,
    enricher,
    enable_references: bool = True,
    enable_quotes: bool = True,
    enable_contacts: bool = True,
    fetch_from_relays: bool = False,
    default_relays: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Apply all auto-enrichment strategies to an event.

    This is the main entry point for auto-enrichment. It applies
    all enabled enrichment strategies based on event type.

    Args:
        event: Raw event dictionary
        enriched_event: Enriched event dictionary (will be modified)
        feed_db: Database connection
        profile_cache: Profile cache
        enricher: Enricher instance
        enable_references: Enable auto-enrichment of referenced events
        enable_quotes: Enable auto-enrichment of quoted events
        enable_contacts: Enable auto-enrichment of contact lists
        fetch_from_relays: Whether to fetch missing referenced events from relays
        default_relays: Default relays to use for fetching

    Returns:
        Modified enriched_event with auto-enriched data

    Examples:
        >>> enriched = apply_auto_enrichment(event, enriched, db, cache, enricher)
    """
    kind = event.get('kind')

    # Contact list enrichment (kind 3)
    if enable_contacts and kind == 3:
        enriched_event = auto_enrich_contact_list(event, enriched_event, profile_cache)

    # Quoted event enrichment (kind 1 with 'q' tag)
    if enable_quotes and kind == 1:
        enriched_event = auto_enrich_quoted_events(
            event, enriched_event, feed_db, profile_cache, enricher
        )

    # General reference enrichment (e/a tags, nostr: URIs)
    if enable_references:
        enriched_event = auto_enrich_references(
            event, enriched_event, feed_db, profile_cache, enricher,
            fetch_from_relays=fetch_from_relays,
            default_relays=default_relays
        )

    return enriched_event
