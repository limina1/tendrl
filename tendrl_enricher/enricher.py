"""
Core enrichment engine for nostr-feeds

Traverses dependency trees, fetches related events,
computes stats, and produces enriched event dictionaries.
"""

import sqlite3
from typing import Dict, Any, Optional, List

from .profiles import ProfileCache
from .stats import compute_all_stats
from .utils import format_relative_time, format_stats
from .db import get_events_by_tag, get_events_by_author


class Enricher:
    """
    Event enricher with dependency tree traversal.

    Takes raw events and enriches them with:
    - Author profiles
    - Aggregated stats (reactions, replies, zaps)
    - Formatted metadata
    """

    def __init__(
        self,
        feed_db_conn: sqlite3.Connection,
        profiles_db_conn: sqlite3.Connection,
        profile_cache: ProfileCache
    ):
        """
        Initialize enricher.

        Args:
            feed_db_conn: Connection to feed database (e.g., timeline.db)
                         Contains both content events AND engagement events
            profiles_db_conn: Connection to shared profiles.db
            profile_cache: Profile cache instance
        """
        self.feed_db = feed_db_conn
        self.profiles_db = profiles_db_conn
        self.profile_cache = profile_cache

    def enrich_event(
        self,
        event: Dict[str, Any],
        dep_config: Dict[str, Any],
        depth: int = 0
    ) -> Dict[str, Any]:
        """
        Enrich single event based on dependency configuration.

        This is the core enrichment algorithm from STREAMING-ARCHITECTURE.org.

        Args:
            event: Raw event dictionary from database
            dep_config: Dependency configuration from TOML
            depth: Current recursion depth (for nested replies)

        Returns:
            Enriched event dictionary with structure:
            {
                'event': {...},           # Original event
                'deps': {                 # Dependencies
                    'author': {...},      # Profile (kind 0)
                    'reactions': {...},   # Stats
                    'replies': {...},     # Stats or expanded events
                    'zaps': {...}         # Stats
                },
                'meta': {                 # Metadata
                    'depth': 0,
                    'formatted_time': '2h ago',
                    'formatted_stats': '💬 7  ❤️ 42  ⚡ 21k'
                }
            }

        Examples:
            >>> enricher = Enricher(feed_db, profiles_db, cache)
            >>> event = {'id': 'abc123', 'pubkey': 'deadbeef...', ...}
            >>> enriched = enricher.enrich_event(event, dep_config)
            >>> enriched['deps']['author']['name']
            'alice'
        """
        enriched = {
            'event': event,
            'deps': {},
            'meta': {
                'depth': depth
            }
        }

        # Get event metadata
        event_id = event.get('id', '')
        event_pubkey = event.get('pubkey', '')
        created_at = event.get('created_at', 0)

        # Process each dependency
        deps = dep_config.get('deps', {})

        # Debug: Log if we're enriching replies without author dep
        if depth > 0 and 'author' not in deps:
            import sys
            print(f"WARNING: Enriching depth={depth} event without author dep. deps keys: {list(deps.keys())}", file=sys.stderr)

        for dep_name, dep_spec in deps.items():
            relation = dep_spec.get('relation')
            kind = dep_spec.get('kind')
            mode = dep_spec.get('mode', 'aggregate')
            required = dep_spec.get('required', False)

            # Author dependency (profile by pubkey)
            if relation == 'author':
                profile = self.profile_cache.get(event_pubkey)

                if profile:
                    enriched['deps'][dep_name] = profile
                elif required:
                    # Use placeholder if required
                    enriched['deps'][dep_name] = self.profile_cache.get_or_default(event_pubkey)

            # e_tag dependency (events referencing this event)
            elif relation == 'e_tag':
                if mode == 'single':
                    # Single mode: Get parent event (event THIS event references)
                    # Extract e-tags FROM this event
                    e_tags = [t for t in event.get('tags', []) if t and t[0] == 'e' and len(t) > 1]

                    parent_event = None
                    if e_tags:
                        from .db import get_event_by_id

                        # NIP-10: Try to find 'reply' marker first, then use last e-tag (immediate parent)
                        reply_tag = next((t for t in e_tags if len(t) > 3 and t[3] == 'reply'), None)
                        if reply_tag:
                            parent_id = reply_tag[1]
                        else:
                            # Use last e-tag as immediate parent (NIP-10 convention)
                            parent_id = e_tags[-1][1]

                        # Fetch parent event
                        parent_event = get_event_by_id(self.feed_db, parent_id)

                    # Always create dep key (None if not found)
                    enriched['deps'][dep_name] = parent_event
                else:
                    # Aggregate/expanded modes: Get events that reference THIS event (children)
                    related_events = get_events_by_tag(
                        self.feed_db,
                        kind=kind,
                        tag_type='e',
                        tag_value=event_id
                    )
                    # Debug logging
                    if dep_name == 'replies':
                        import sys
                        print(f"DEBUG: Querying replies for event {event_id[:16]}... (kind {kind})", file=sys.stderr)
                        print(f"DEBUG: Found {len(related_events)} replies", file=sys.stderr)

                    if mode == 'aggregate':
                        # Compute stats only
                        enriched['deps'][dep_name] = self._aggregate_related_events(
                            related_events,
                            kind,
                            event_id,
                            event_pubkey,
                            dep_spec
                        )

                    elif mode == 'expanded':
                        # Include full events (recursive)
                        max_depth = dep_spec.get('max_depth', 10)
                        recursive = dep_spec.get('recursive', False)

                        if recursive and depth < max_depth:
                            # Recursively enrich each related event
                            enriched['deps'][dep_name] = [
                                self.enrich_event(e, dep_spec, depth + 1)
                                for e in related_events
                            ]
                        else:
                            # Just include raw events
                            enriched['deps'][dep_name] = related_events

            # p_tag dependency (events referencing event's author)
            elif relation == 'p_tag':
                related_events = get_events_by_tag(
                    self.feed_db,
                    kind=kind,
                    tag_type='p',
                    tag_value=event_pubkey
                )

                if mode == 'aggregate':
                    enriched['deps'][dep_name] = self._aggregate_related_events(
                        related_events,
                        kind,
                        event_id,
                        event_pubkey,
                        dep_spec
                    )

            # a_tag dependency (addressable events - kind 30000+)
            elif relation == 'a_tag':
                # Build address for this event (kind:pubkey:d-tag)
                address = self._build_address(event)

                # Find events referencing this address via 'a' tag
                related_events = get_events_by_tag(
                    self.feed_db,
                    kind=kind,
                    tag_type='a',
                    tag_value=address
                )

                if mode == 'aggregate':
                    # Compute stats only
                    enriched['deps'][dep_name] = self._aggregate_related_events(
                        related_events,
                        kind,
                        event_id,
                        event_pubkey,
                        dep_spec
                    )

                elif mode == 'tree':
                    # Build hierarchical tree (for publications)
                    max_depth = dep_spec.get('max_depth', 10)

                    if depth < max_depth:
                        enriched['deps'][dep_name] = self._build_tree(
                            related_events,
                            dep_spec,
                            depth,
                            max_depth
                        )
                    else:
                        # At max depth, return empty list
                        enriched['deps'][dep_name] = []

                elif mode == 'expanded':
                    # Include full events
                    max_depth = dep_spec.get('max_depth', 10)

                    if depth < max_depth:
                        # Enrich each related event
                        enriched['deps'][dep_name] = [
                            self.enrich_event(e, dep_spec, depth + 1)
                            for e in related_events
                        ]
                    else:
                        # Just include raw events
                        enriched['deps'][dep_name] = related_events

            # repost_content dependency (parse JSON from kind 6 content field)
            elif relation == 'repost_content':
                # Kind 6 reposts contain stringified JSON of original event in content
                content = event.get('content', '')
                reposted_event = None

                if content:
                    try:
                        import json
                        reposted_event = json.loads(content)
                    except json.JSONDecodeError:
                        # Fall back to e-tag if content isn't valid JSON
                        e_tag = next((t for t in event.get('tags', []) if t[0] == 'e'), None)
                        if e_tag and len(e_tag) > 1:
                            from .db import get_event_by_id
                            reposted_event = get_event_by_id(self.feed_db, e_tag[1])

                # Recursively enrich the reposted event if found
                if reposted_event:
                    # Check if we should enrich recursively (based on mode)
                    mode = dep_spec.get('mode', 'single')
                    if mode == 'expanded' and depth < dep_spec.get('max_depth', 5):
                        # Enrich the reposted event with its own dependencies
                        enriched['deps'][dep_name] = self.enrich_event(
                            reposted_event,
                            dep_config,
                            depth + 1
                        )
                    else:
                        # Return raw event
                        enriched['deps'][dep_name] = reposted_event
                else:
                    # Always create dep key (None if not found) - indicates enrichment was attempted
                    enriched['deps'][dep_name] = None

            # reposted_author dependency (get author of reposted event)
            elif relation == 'reposted_author':
                # Get the reposted event first (from reposted_event dep or parse content)
                reposted_event = enriched['deps'].get('reposted_event')

                if not reposted_event:
                    # Try to parse from content
                    content = event.get('content', '')
                    if content:
                        try:
                            import json
                            reposted_event = json.loads(content)
                        except json.JSONDecodeError:
                            pass

                # Initialize as None
                profile = None

                # Get author profile
                if reposted_event and isinstance(reposted_event, dict):
                    author_pubkey = reposted_event.get('pubkey')
                    if author_pubkey:
                        profile = self.profile_cache.get(author_pubkey)
                        if not profile and required:
                            profile = self.profile_cache.get_or_default(author_pubkey)

                # Fallback to p-tag if no reposted event
                if not profile:
                    p_tag = next((t for t in event.get('tags', []) if t[0] == 'p'), None)
                    if p_tag and len(p_tag) > 1:
                        profile = self.profile_cache.get(p_tag[1])

                # Always create dep key (None if not found) - indicates enrichment was attempted
                enriched['deps'][dep_name] = profile

            # quoted_event dependency (extract nevent/naddr from nostr: URI in content)
            elif relation == 'quoted_event':
                import re
                from .nip19 import decode_nevent, decode_naddr
                from .db import get_event_by_id, get_addressable_event

                quoted = None
                content = event.get('content', '')

                # Try nevent first (regular events)
                match = re.search(r'nostr:(nevent1\w+)', content)
                if match:
                    nevent = match.group(1)
                    try:
                        decoded = decode_nevent(nevent)
                        event_id = decoded.get('event_id')
                        if event_id:
                            quoted = get_event_by_id(self.feed_db, event_id)
                    except Exception:
                        pass  # Ignore decode errors

                # Try naddr if nevent didn't match (addressable events like articles)
                if not quoted:
                    match = re.search(r'nostr:(naddr1\w+)', content)
                    if match:
                        naddr = match.group(1)
                        try:
                            decoded = decode_naddr(naddr)
                            kind = decoded.get('kind')
                            pubkey = decoded.get('pubkey')
                            identifier = decoded.get('identifier', '')
                            if kind and pubkey:
                                # Try current database first
                                quoted = get_addressable_event(self.feed_db, kind, pubkey, identifier)

                                # If not found, search other databases (e.g., publications.db for articles)
                                if not quoted:
                                    # Determine which database to search based on kind
                                    search_dbs = []
                                    if kind == 30023:  # Long-form articles
                                        search_dbs = ['articles.db', 'publications.db']
                                    elif kind == 30040:  # Publications
                                        search_dbs = ['publications.db']
                                    elif kind == 30818:  # Wiki pages
                                        search_dbs = ['publications.db']

                                    # Try each database
                                    for db_name in search_dbs:
                                        try:
                                            from .db import DatabaseManager
                                            # Create temporary connection (enricher doesn't have db_manager)
                                            # This is a limitation - enricher should have access to db_manager
                                            # For now, just try current db
                                            pass
                                        except Exception:
                                            pass
                        except Exception:
                            pass  # Ignore decode errors

                # Always create dep key (None if not found) - indicates enrichment was attempted
                enriched['deps'][dep_name] = quoted

            # quoted_author dependency (get author of quoted event)
            elif relation == 'quoted_author':
                profile = None
                quoted_event = enriched['deps'].get('quoted_event')
                if quoted_event and isinstance(quoted_event, dict):
                    author_pubkey = quoted_event.get('pubkey')
                    if author_pubkey:
                        profile = self.profile_cache.get(author_pubkey)
                        if not profile and required:
                            profile = self.profile_cache.get_or_default(author_pubkey)

                # Always create dep key (None if not found) - indicates enrichment was attempted
                enriched['deps'][dep_name] = profile

            # mentioned_profiles dependency (extract nprofile and npub URIs from content)
            elif relation == 'mentioned_profiles':
                # Temporarily disabled to avoid enrichment errors
                # TODO: Re-enable with proper error handling
                # Always create dep key (None indicates disabled) - shows enrichment was attempted
                enriched['deps'][dep_name] = None

            # parent_author dependency (get author of parent note being replied to)
            elif relation == 'parent_author':
                profile = None
                parent_note = enriched['deps'].get('parent_note')
                if parent_note and isinstance(parent_note, dict):
                    author_pubkey = parent_note.get('pubkey')
                    if author_pubkey:
                        profile = self.profile_cache.get(author_pubkey)
                        if not profile and required:
                            profile = self.profile_cache.get_or_default(author_pubkey)

                # Always create dep key (None if not found) - indicates enrichment was attempted
                enriched['deps'][dep_name] = profile

            # parent_thread dependency (recursive parent chain going upward)
            elif relation == 'parent_thread':
                from .db import get_event_by_id

                # Extract e-tags from THIS event (they point to parents)
                e_tags = [t for t in event.get('tags', []) if t and t[0] == 'e' and len(t) > 1]

                parent_enriched = None
                if e_tags:
                    # Get the root parent (usually first e-tag or marked with 'root' marker)
                    # NIP-10: First e-tag is root, last is immediate parent
                    parent_id = e_tags[-1][1]  # Last e-tag is immediate parent

                    parent_event = get_event_by_id(self.feed_db, parent_id)
                    if parent_event:
                        # Recursively enrich parent with its own parents
                        max_depth = dep_spec.get('max_depth', 10)
                        if depth < max_depth:
                            parent_enriched = self.enrich_event(parent_event, dep_spec, depth + 1)
                        else:
                            # At max depth, just include raw event
                            parent_enriched = parent_event

                # Always create dep key (None if not found) - indicates enrichment was attempted
                enriched['deps'][dep_name] = parent_enriched

            # a_tag_children dependency (events LISTED IN this event's a-tags)
            # This is for publications that list their sections via a-tags (NKBIP-01)
            elif relation == 'a_tag_children':
                from .db import get_addressable_event

                # Extract a-tags FROM this event's tags
                child_addresses = [
                    tag[1] for tag in event.get('tags', [])
                    if tag and tag[0] == 'a' and len(tag) > 1
                ]

                # Filter by kind if specified
                if kind:
                    child_addresses = [
                        addr for addr in child_addresses
                        if addr.startswith(f"{kind}:")
                    ]

                # Fetch each referenced event
                related_events = []
                for address in child_addresses:
                    parts = address.split(':')
                    if len(parts) == 3:
                        child_kind = int(parts[0])
                        child_pubkey = parts[1]
                        child_d_tag = parts[2]

                        child_event = get_addressable_event(
                            self.feed_db,
                            child_kind,
                            child_pubkey,
                            child_d_tag
                        )

                        if child_event:
                            related_events.append(child_event)

                if mode == 'aggregate':
                    # Compute stats only
                    enriched['deps'][dep_name] = self._aggregate_related_events(
                        related_events,
                        kind,
                        event_id,
                        event_pubkey,
                        dep_spec
                    )

                elif mode == 'tree':
                    # Build hierarchical tree
                    max_depth = dep_spec.get('max_depth', 10)

                    if depth < max_depth:
                        enriched['deps'][dep_name] = self._build_tree(
                            related_events,
                            dep_spec,
                            depth,
                            max_depth
                        )
                    else:
                        enriched['deps'][dep_name] = []

                elif mode == 'expanded':
                    # Include full events
                    max_depth = dep_spec.get('max_depth', 10)

                    if depth < max_depth:
                        # Recursively enrich each child
                        enriched['deps'][dep_name] = [
                            self.enrich_event(e, dep_spec, depth + 1)
                            for e in related_events
                        ]
                    else:
                        enriched['deps'][dep_name] = related_events

        # Add formatted metadata
        enriched['meta']['formatted_time'] = format_relative_time(created_at)
        enriched['meta']['formatted_stats'] = format_stats(enriched['deps'])

        return enriched

    def _aggregate_related_events(
        self,
        events: List[Dict[str, Any]],
        kind: int,
        event_id: str,
        event_pubkey: str,
        dep_spec: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Aggregate related events into stats.

        Args:
            events: List of related events
            kind: Event kind (7=reactions, 1=replies, 9735=zaps)
            event_id: Parent event ID
            event_pubkey: Parent event author pubkey
            dep_spec: Dependency specification

        Returns:
            Aggregated stats dictionary
        """
        stats_to_compute = dep_spec.get('stats', ['count'])

        result = {
            'count': len(events),
            'expandable': dep_spec.get('expandable', False)
        }

        # Kind-specific stats
        if kind == 7:  # Reactions
            if 'by_content' in stats_to_compute:
                from collections import Counter
                content_counts = Counter(
                    e.get('content', '❤️').strip() or '❤️'
                    for e in events
                )
                result['by_content'] = dict(content_counts)

            # Collect detailed reaction data (who reacted with what emoji)
            details = []
            for e in events:
                reaction_pubkey = e.get('pubkey', '')
                reaction_content = e.get('content', '❤️').strip() or '❤️'
                reaction_created_at = e.get('created_at', 0)

                # Get reactor profile
                profile = self.profile_cache.get(reaction_pubkey) if reaction_pubkey else None

                details.append({
                    'pubkey': reaction_pubkey,
                    'name': profile.get('name') if profile else None,
                    'display_name': profile.get('display_name') if profile else None,
                    'picture': profile.get('picture') if profile else None,
                    'content': reaction_content,
                    'created_at': reaction_created_at
                })

            result['details'] = details

        elif kind == 9735:  # Zaps
            if 'total_sats' in stats_to_compute:
                from .utils import parse_bolt11_amount, get_tag
                total_sats = sum(
                    parse_bolt11_amount(get_tag(e, 'bolt11') or '')
                    for e in events
                )
                result['total_sats'] = total_sats

            # Collect detailed zap data (who zapped, amounts, comments)
            from .utils import parse_bolt11_amount, get_tag
            from .zap_enricher import parse_zap_request

            details = []
            for e in events:
                bolt11 = get_tag(e, 'bolt11') or ''
                amount = parse_bolt11_amount(bolt11)

                # Parse zap request from description tag
                zap_req = parse_zap_request(e)
                sender_pubkey = zap_req.get('sender_pubkey', '')
                comment = zap_req.get('message', '')

                # Get zapper profile
                profile = self.profile_cache.get(sender_pubkey) if sender_pubkey else None

                details.append({
                    'pubkey': sender_pubkey,
                    'name': profile.get('name') if profile else None,
                    'display_name': profile.get('display_name') if profile else None,
                    'picture': profile.get('picture') if profile else None,
                    'amount': amount,
                    'comment': comment,
                    'created_at': e.get('created_at', 0)
                })

            # Sort by amount descending
            details.sort(key=lambda x: x['amount'], reverse=True)
            result['details'] = details

        return result

    def _get_d_tag(self, event: Dict[str, Any]) -> str:
        """
        Extract 'd' tag from addressable event.

        Args:
            event: Event dictionary

        Returns:
            d-tag value or 'unknown' if not found
        """
        for tag in event.get('tags', []):
            if tag and tag[0] == 'd' and len(tag) > 1:
                return tag[1]
        return 'unknown'

    def _build_address(self, event: Dict[str, Any]) -> str:
        """
        Build address string for addressable events (kind 30000+).

        Format: kind:pubkey:d-tag

        Args:
            event: Event dictionary

        Returns:
            Address string (e.g., "30040:abc123...:my-publication")
        """
        kind = event.get('kind')
        pubkey = event.get('pubkey', '')
        d_tag = self._get_d_tag(event)
        return f"{kind}:{pubkey}:{d_tag}"

    def _build_tree(
        self,
        events: List[Dict[str, Any]],
        dep_spec: Dict[str, Any],
        depth: int,
        max_depth: int
    ) -> List[Dict[str, Any]]:
        """
        Build hierarchical tree from events.

        Used for publications where 30040 can contain:
        - Other 30040s (sub-publications)
        - 30041s (sections)
        - 30818s (wiki pages)
        - 30023s (articles)

        Args:
            events: List of child events
            dep_spec: Dependency specification (used for recursive enrichment)
            depth: Current depth
            max_depth: Maximum depth to traverse

        Returns:
            List of enriched events with nested children
        """
        if depth >= max_depth:
            return []

        tree = []
        for event in events:
            # Recursively enrich each child with full dependency tree
            enriched_child = self.enrich_event(event, dep_spec, depth + 1)
            tree.append(enriched_child)

        return tree

    def enrich_feed(
        self,
        events: List[Dict[str, Any]],
        feed_config: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Enrich multiple events from a feed.

        Args:
            events: List of raw events
            feed_config: Feed configuration with root deps

        Returns:
            List of enriched events

        Examples:
            >>> events = get_events(conn, limit=50)
            >>> enriched = enricher.enrich_feed(events, feed_config)
            >>> len(enriched)
            50
        """
        # Prefetch all author profiles
        pubkeys = [e.get('pubkey') for e in events if e.get('pubkey')]
        self.profile_cache.prefetch(pubkeys)

        # Enrich each event
        root_config = feed_config.get('root', {})
        enriched_events = []

        for event in events:
            try:
                enriched = self.enrich_event(event, root_config)
                enriched_events.append(enriched)
            except Exception as e:
                # Log error but continue with other events
                print(f"Error enriching event {event.get('id', 'unknown')}: {e}")
                continue

        return enriched_events


def create_enricher(
    feed_db_path: str,
    profiles_db_path: str,
    max_cache_size: int = 10_000
) -> Enricher:
    """
    Factory function to create enricher with connections.

    Args:
        feed_db_path: Path to feed database (contains content + engagement)
        profiles_db_path: Path to profiles database
        max_cache_size: Max profiles in memory cache

    Returns:
        Configured Enricher instance

    Examples:
        >>> enricher = create_enricher(
        ...     '~/.local/share/nostr-feeds/timeline.db',
        ...     '~/.local/share/nostr-feeds/profiles.db'
        ... )
    """
    import os

    feed_db_path = os.path.expanduser(feed_db_path)
    profiles_db_path = os.path.expanduser(profiles_db_path)

    # Create connections
    feed_db = sqlite3.connect(feed_db_path, check_same_thread=False)
    feed_db.row_factory = sqlite3.Row

    profiles_db = sqlite3.connect(profiles_db_path, check_same_thread=False)
    profiles_db.row_factory = sqlite3.Row

    # Create profile cache
    profile_cache = ProfileCache(profiles_db, max_memory_size=max_cache_size)

    return Enricher(feed_db, profiles_db, profile_cache)
