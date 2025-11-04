#!/usr/bin/env python3
"""
HTTP API server for nostr-feeds - ALL feed types.

Provides JSON endpoints for browser SPAs, mobile apps, and custom tools.
Adopts FastAPI-style patterns from publications_server.py as reference.

Supports all feed types from config.toml:
- Kind 1: Notes (with threads)
- Kind 6: Reposts
- Kind 9735: Zaps
- Kind 9802: Highlights (NIP-84)
- Kind 30023: Long-form articles
- Kind 30040: Publications (with sections)

Now uses the proven architecture pattern!
"""

import sys
import json
import argparse
from pathlib import Path
from typing import Dict, List, Optional, Any
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from enricher.config import Config
from enricher.db import DatabaseManager, get_events, get_addressable_event
from enricher.enricher import Enricher
from enricher.profiles import ProfileCache
from enricher.follows import FollowsCache
from enricher.event_finder import find_event
from enricher.nip19 import normalize_event_identifier
from enricher.feed_fetcher import FeedFetcher


class NostrFeedsAPI:
    """
    API for fetching enriched events from any feed type using generic enricher.
    """

    def __init__(self, config_path: str):
        """
        Initialize API with config.

        Args:
            config_path: Path to config.toml
        """
        self.config = Config.load(config_path)
        self.db_manager = DatabaseManager(self.config.get_db_dir())

    def list_feeds(self) -> List[Dict[str, Any]]:
        """
        List all configured feeds.

        Returns:
            List of feed metadata
        """
        feeds = []
        for feed_name in self.config.list_feeds():
            feed_config = self.config.get_feed(feed_name)
            root_config = self.config.get_feed_root_config(feed_name)

            feeds.append({
                'name': feed_name,
                'type': feed_config.get('type', 'relay'),
                'kinds': feed_config.get('display_kinds', [root_config.get('kind', 1)]),
                'filter_by_follows': feed_config.get('filter_by_follows', False),
                'refresh_interval': feed_config.get('refresh_interval', 0)
            })

        return feeds

    def get_feed(self, feed_name: str, limit: int = 50, since: Optional[int] = None,
                 until: Optional[int] = None, user_npub: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get enriched feed events.

        Args:
            feed_name: Feed name from config
            limit: Maximum number of events
            since: Unix timestamp (only events after this)
            until: Unix timestamp (only events before this)
            user_npub: User npub for follow filtering

        Returns:
            List of enriched events
        """
        feed_config = self.config.get_feed(feed_name)
        if not feed_config:
            raise ValueError(f"Feed '{feed_name}' not found")

        # Get databases
        feed_db_file = feed_config.get('db_file')
        feed_db = self.db_manager.get_connection(feed_db_file)

        profiles_db_file = self.config.get_profiles_db()
        profiles_db = self.db_manager.get_connection(profiles_db_file)

        # Create caches and enricher
        profile_cache = ProfileCache(profiles_db, cache_ttl=self.config.get_profile_cache_ttl())
        follows_cache = FollowsCache(feed_db)
        enricher = Enricher(feed_db, profiles_db, profile_cache)

        # Get root config
        root_config = self.config.get_feed_root_config(feed_name)

        # Get display kinds
        display_kinds = feed_config.get('display_kinds')
        if not display_kinds:
            root_kind = root_config.get('kind', 1)
            display_kinds = [root_kind]

        # Check follow filtering
        filter_by_follows = feed_config.get('filter_by_follows', False)
        authors = None

        if filter_by_follows:
            npub = user_npub or self.config.get_default_npub()
            if npub:
                follows = follows_cache.get_follows(npub)
                authors = list(follows)

        # Query events
        events = get_events(feed_db, limit=limit, since=since, until=until, kinds=display_kinds, authors=authors)

        # Apply has_e_tags filter if configured
        root_filter = root_config.get('filter', {})
        if root_filter.get('has_e_tags'):
            # Only include notes that have e-tags (are replies)
            events = [e for e in events if any(t[0] == 'e' for t in e.get('tags', []) if isinstance(t, list) and len(t) > 0)]

        # Enrich events based on feed config
        enriched_events = []
        for event in events:
            try:
                # For feed view, use aggregate mode for engagement
                feed_root_config = self._create_feed_config(root_config, event['kind'])
                enriched = enricher.enrich_event(event, feed_root_config, depth=0)

                # Transform to format expected by SPA
                transformed = self._transform_for_feed_card(enriched, event['kind'])

                # Filter publications with 0 sections
                if event['kind'] == 30040:
                    # Get section count from transformed data
                    section_count = transformed.get('section_count', 0)
                    if section_count == 0:
                        # Skip publications with no sections
                        continue

                enriched_events.append(transformed)
            except Exception as e:
                print(f"Error enriching event {event.get('id')}: {e}", file=sys.stderr)
                continue

        return enriched_events

    def get_event(self, event_id: str, max_depth: int = 5) -> Optional[Dict[str, Any]]:
        """
        Get single enriched event with full expansion.

        Args:
            event_id: Event ID (hex or NIP-19) or address (kind:pubkey:d_tag)
            max_depth: Maximum thread depth

        Returns:
            Enriched event dict with full tree
        """
        # Check if this is an address (kind:pubkey:d_tag)
        if ':' in event_id and event_id.count(':') >= 2:
            parts = event_id.split(':', 2)
            try:
                kind = int(parts[0])
                pubkey = parts[1]
                d_tag = parts[2]

                # For addressable events, search publications db
                feed_db = self.db_manager.get_connection('publications.db')
                event = get_addressable_event(feed_db, kind, pubkey, d_tag)
                if event:
                    event['_source_db'] = 'publications.db'
            except (ValueError, IndexError):
                # Not a valid address, try as event ID
                event_id_hex = event_id
                try:
                    event = find_event(event_id_hex, self.db_manager)
                except Exception:
                    event = None
        else:
            # Normalize event ID
            try:
                event_info = normalize_event_identifier(event_id)
                event_id_hex = event_info['event_id']
            except:
                event_id_hex = event_id

            # Find event in databases (handle EventNotFoundError)
            try:
                event = find_event(event_id_hex, self.db_manager)
            except Exception:
                event = None

        if not event:
            return None

        source_db = event.get('_source_db')

        # Get databases
        feed_db = self.db_manager.get_connection(source_db)
        profiles_db_file = self.config.get_profiles_db()
        profiles_db = self.db_manager.get_connection(profiles_db_file)

        # Create enricher
        profile_cache = ProfileCache(profiles_db, cache_ttl=self.config.get_profile_cache_ttl())
        enricher = Enricher(feed_db, profiles_db, profile_cache)

        # Create dep config based on event kind
        dep_config = self._create_detail_config(event['kind'], max_depth)

        # Enrich with full tree
        enriched = enricher.enrich_event(event, dep_config, depth=0)

        # Transform to format expected by SPA
        transformed = self._transform_for_detail_view(enriched, event['kind'])

        return transformed

    def get_thread(self, event_id: str, max_depth: int = 5) -> Optional[Dict[str, Any]]:
        """
        Get full thread view for an event.

        If the event is a reply, this finds the root of the thread first,
        then returns the full conversation with all replies expanded.

        Args:
            event_id: Event ID (hex or NIP-19)
            max_depth: Maximum reply depth to expand

        Returns:
            Enriched root event with full thread expansion
        """
        from enricher.db import get_event_by_id

        # Normalize and find the event
        try:
            event_info = normalize_event_identifier(event_id)
            event_id_hex = event_info['event_id']
        except:
            event_id_hex = event_id

        # Find event in databases (handle EventNotFoundError)
        try:
            event = find_event(event_id_hex, self.db_manager)
        except Exception:
            return None

        if not event:
            return None

        source_db = event.get('_source_db')
        feed_db = self.db_manager.get_connection(source_db)

        # Handle kind 6 reposts: extract reposted event and build thread from that
        if event.get('kind') == 6:
            # Try to extract reposted event from content (JSON)
            content = event.get('content', '')
            reposted_event = None
            if content:
                try:
                    import json
                    reposted_event = json.loads(content)
                except json.JSONDecodeError:
                    pass

            # Fallback: try to find via e-tag
            if not reposted_event:
                e_tag = next((t for t in event.get('tags', []) if t and t[0] == 'e' and len(t) > 1), None)
                if e_tag:
                    reposted_event = get_event_by_id(feed_db, e_tag[1])

            # Use reposted event as starting point for thread traversal
            if reposted_event:
                event = reposted_event

        # Find root of thread by following e-tags upward
        root_event = event
        visited = set()  # Prevent infinite loops
        is_incomplete_thread = False  # Flag if we couldn't find the actual root

        while True:
            current_id = root_event.get('id')
            if current_id in visited:
                break  # Loop detected
            visited.add(current_id)

            # Get e-tags (parent references)
            e_tags = [t for t in root_event.get('tags', []) if t and t[0] == 'e' and len(t) > 1]

            if not e_tags:
                # No parent, this is the root
                break

            # NIP-10: First e-tag is root (if marked), last is immediate parent
            # Try to find the root e-tag first
            root_tag = next((t for t in e_tags if len(t) > 3 and t[3] == 'root'), None)
            if root_tag:
                parent_id = root_tag[1]
            else:
                # Use first e-tag as root (NIP-10 convention)
                parent_id = e_tags[0][1]

            # Fetch parent event
            parent_event = get_event_by_id(feed_db, parent_id)
            if not parent_event:
                # Can't find parent, use current as root
                # Note: This means the thread is incomplete
                print(f"Warning: Parent event {parent_id} not found in database. Using {current_id} as thread root.", file=sys.stderr)
                break

            # Continue climbing
            root_event = parent_event

        # Debug: Log what we found
        print(f"DEBUG: Thread request for {event_id_hex}", file=sys.stderr)
        print(f"DEBUG: Found root event: {root_event.get('id')}", file=sys.stderr)
        print(f"DEBUG: Root event kind: {root_event.get('kind')}", file=sys.stderr)

        # Now enrich the root with full thread expansion
        profiles_db_file = self.config.get_profiles_db()
        profiles_db = self.db_manager.get_connection(profiles_db_file)

        profile_cache = ProfileCache(profiles_db, cache_ttl=self.config.get_profile_cache_ttl())
        enricher = Enricher(feed_db, profiles_db, profile_cache)

        # Create config with full reply expansion
        thread_config = {
            'deps': {
                'author': {
                    'kind': 0,
                    'relation': 'author',
                    'required': True
                },
                'reactions': {
                    'kind': 7,
                    'relation': 'e_tag',
                    'mode': 'aggregate',
                    'stats': ['count', 'by_content']
                },
                'replies': {
                    'kind': 1,
                    'relation': 'e_tag',
                    'mode': 'expanded',  # Full expansion
                    'stats': ['count'],
                    'expandable': True,
                    'recursive': True,
                    'max_depth': max_depth,
                    # Nested deps: each reply should also have these enriched
                    'deps': {
                        'author': {
                            'kind': 0,
                            'relation': 'author',
                            'required': True
                        },
                        'reactions': {
                            'kind': 7,
                            'relation': 'e_tag',
                            'mode': 'aggregate',
                            'stats': ['count', 'by_content']
                        },
                        'zaps': {
                            'kind': 9735,
                            'relation': 'e_tag',
                            'mode': 'aggregate',
                            'stats': ['count', 'total_sats']
                        },
                        'reposts': {
                            'kind': 6,
                            'relation': 'e_tag',
                            'mode': 'aggregate',
                            'stats': ['count']
                        },
                        # Nested replies (recursive)
                        'replies': {
                            'kind': 1,
                            'relation': 'e_tag',
                            'mode': 'expanded',
                            'recursive': True,
                            'max_depth': max_depth
                        }
                    }
                },
                'zaps': {
                    'kind': 9735,
                    'relation': 'e_tag',
                    'mode': 'aggregate',
                    'stats': ['count', 'total_sats']
                },
                'reposts': {
                    'kind': 6,
                    'relation': 'e_tag',
                    'mode': 'aggregate',
                    'stats': ['count']
                }
            }
        }

        # Enrich root with full thread
        enriched = enricher.enrich_event(root_event, thread_config, depth=0)

        # Transform to format expected by SPA
        transformed = self._transform_for_thread_view(enriched)

        return transformed

    def get_profile(self, pubkey: str) -> Optional[Dict[str, Any]]:
        """
        Get profile information for a user.

        Args:
            pubkey: User's public key (hex or npub format)

        Returns:
            Profile data with metadata
        """
        from enricher.db import get_profile as get_profile_by_pubkey

        # Normalize pubkey (handle npub, nprofile, hex)
        try:
            from enricher.nip19 import normalize_profile_identifier
            pubkey_info = normalize_profile_identifier(pubkey)
            pubkey_hex = pubkey_info['pubkey']
        except:
            # Assume it's already hex
            pubkey_hex = pubkey

        # Get profile from profiles.db
        profiles_db_file = self.config.get_profiles_db()
        profiles_db = self.db_manager.get_connection(profiles_db_file)

        profile = get_profile_by_pubkey(profiles_db, pubkey_hex)

        if not profile:
            return None

        # Convert to expected format
        return {
            'pubkey': pubkey_hex,
            'name': profile.get('name'),
            'display_name': profile.get('display_name'),
            'about': profile.get('about'),
            'picture': profile.get('picture'),
            'banner': profile.get('banner'),
            'nip05': profile.get('nip05'),
            'nip05_verified': profile.get('nip05_verified', 0) == 1,
            'lightning_address': profile.get('lightning_address'),
            'website': profile.get('website'),
            'event_json': profile.get('content_json')  # Return full event JSON for copy function
        }

    def get_profile_feeds(self, pubkey: str) -> List[Dict[str, Any]]:
        """
        Get list of feeds that have events by this author.

        Args:
            pubkey: User's public key (hex or npub)

        Returns:
            List of feed metadata with event counts
        """
        from enricher.db import count_events_by_author

        # Normalize pubkey
        try:
            from enricher.nip19 import normalize_profile_identifier
            pubkey_info = normalize_profile_identifier(pubkey)
            pubkey_hex = pubkey_info['pubkey']
        except:
            pubkey_hex = pubkey

        available_feeds = []

        # Check each feed for events by this author
        for feed_name in self.config.list_feeds():
            try:
                feed_config = self.config.get_feed(feed_name)
                feed_db_file = feed_config.get('db_file')

                # Skip if database doesn't exist
                try:
                    feed_db = self.db_manager.get_connection(feed_db_file)
                except FileNotFoundError:
                    continue

                # Get display kinds for this feed
                root_config = self.config.get_feed_root_config(feed_name)
                display_kinds = feed_config.get('display_kinds')
                if not display_kinds:
                    root_kind = root_config.get('kind', 1)
                    display_kinds = [root_kind]

                # Count events by author for these kinds
                count = count_events_by_author(feed_db, pubkey_hex, kinds=display_kinds)

                if count > 0:
                    available_feeds.append({
                        'name': feed_name,
                        'kinds': display_kinds,
                        'count': count
                    })
            except Exception as e:
                # Skip feeds that fail to load
                print(f"Warning: Skipping feed '{feed_name}': {e}", file=sys.stderr)
                continue

        return available_feeds

    def get_profile_feed(self, pubkey: str, feed_name: str, limit: int = 50,
                        since: Optional[int] = None, until: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get events from a specific feed filtered by author.

        Args:
            pubkey: User's public key (hex or npub)
            feed_name: Feed name from config
            limit: Maximum number of events
            since: Unix timestamp (only events after this)
            until: Unix timestamp (only events before this)

        Returns:
            List of enriched events by this author
        """
        # Normalize pubkey
        try:
            from enricher.nip19 import normalize_profile_identifier
            pubkey_info = normalize_profile_identifier(pubkey)
            pubkey_hex = pubkey_info['pubkey']
        except:
            pubkey_hex = pubkey

        # Get feed config
        feed_config = self.config.get_feed(feed_name)
        if not feed_config:
            raise ValueError(f"Feed '{feed_name}' not found")

        # Get databases
        feed_db_file = feed_config.get('db_file')
        feed_db = self.db_manager.get_connection(feed_db_file)

        profiles_db_file = self.config.get_profiles_db()
        profiles_db = self.db_manager.get_connection(profiles_db_file)

        # Create caches and enricher
        profile_cache = ProfileCache(profiles_db, cache_ttl=self.config.get_profile_cache_ttl())
        enricher = Enricher(feed_db, profiles_db, profile_cache)

        # Get root config
        root_config = self.config.get_feed_root_config(feed_name)

        # Get display kinds
        display_kinds = feed_config.get('display_kinds')
        if not display_kinds:
            root_kind = root_config.get('kind', 1)
            display_kinds = [root_kind]

        # Query events by this author only
        events = get_events(feed_db, limit=limit, since=since, until=until,
                          kinds=display_kinds, authors=[pubkey_hex])

        # Enrich events (same as get_feed)
        enriched_events = []
        for event in events:
            try:
                # Create feed-optimized config
                feed_config_dep = self._create_feed_config(root_config, event['kind'])

                # Enrich the event
                enriched = enricher.enrich_event(event, feed_config_dep, depth=0)

                # Transform based on kind
                transformed = self._transform_for_feed_card(enriched, event['kind'])
                enriched_events.append(transformed)
            except Exception as e:
                print(f"Error enriching event {event.get('id', 'unknown')}: {e}")
                continue

        return enriched_events

    def _create_feed_config(self, root_config: Dict[str, Any], kind: int) -> Dict[str, Any]:
        """
        Create feed-optimized config (aggregate engagement, no expansion).

        Args:
            root_config: Root config from feed
            kind: Event kind

        Returns:
            Modified config for feed view
        """
        feed_config = {
            'deps': {
                'author': root_config['deps']['author']
            }
        }

        # Add parent_note if configured (for replies feed)
        if 'parent_note' in root_config['deps']:
            feed_config['deps']['parent_note'] = root_config['deps']['parent_note']
        if 'parent_author' in root_config['deps']:
            feed_config['deps']['parent_author'] = root_config['deps']['parent_author']

        # For kind 6 reposts, add reposted event/author dependencies
        if kind == 6:
            if 'reposted_event' in root_config['deps']:
                feed_config['deps']['reposted_event'] = root_config['deps']['reposted_event']
            if 'reposted_author' in root_config['deps']:
                feed_config['deps']['reposted_author'] = root_config['deps']['reposted_author']

        # For kind 1 notes, add quoted event/author dependencies
        if kind == 1:
            if 'quoted_event' in root_config['deps']:
                feed_config['deps']['quoted_event'] = root_config['deps']['quoted_event']
            if 'quoted_author' in root_config['deps']:
                feed_config['deps']['quoted_author'] = root_config['deps']['quoted_author']
            # Add mentioned profiles (nprofile/npub mentions)
            if 'mentioned_profiles' in root_config['deps']:
                feed_config['deps']['mentioned_profiles'] = root_config['deps']['mentioned_profiles']

        # Add engagement metrics (aggregate mode)
        if 'reactions' in root_config['deps']:
            feed_config['deps']['reactions'] = {
                **root_config['deps']['reactions'],
                'mode': 'aggregate'
            }
        if 'reposts' in root_config['deps']:
            feed_config['deps']['reposts'] = {
                **root_config['deps']['reposts'],
                'mode': 'aggregate'
            }
        if 'zaps' in root_config['deps']:
            feed_config['deps']['zaps'] = {
                **root_config['deps']['zaps'],
                'mode': 'aggregate'
            }
        if 'replies' in root_config['deps']:
            feed_config['deps']['replies'] = {
                **root_config['deps']['reposts'],
                'mode': 'aggregate'
            }

        # For publications (30040), add section count
        if kind == 30040:
            if 'sections' in root_config['deps']:
                feed_config['deps']['sections'] = {
                    'kind': 30041,
                    'relation': 'a_tag_children',
                    'mode': 'aggregate',
                    'stats': ['count']
                }

        return feed_config

    def _create_detail_config(self, kind: int, max_depth: int) -> Dict[str, Any]:
        """
        Create detail-optimized config (expanded engagement, threads).

        Args:
            kind: Event kind
            max_depth: Maximum tree depth

        Returns:
            Config for detail view
        """
        # Base config with author and engagement
        config = {
            'deps': {
                'author': {'kind': 0, 'relation': 'author', 'required': True}
            }
        }

        # Kind 6 reposts: Extract reposted event and author with full enrichment
        if kind == 6:
            config['deps'].update({
                'reposted_event': {
                    'relation': 'repost_content',
                    'mode': 'expanded',  # Recursively enrich the reposted event
                    'max_depth': 3,
                    'required': False,
                    # Nested deps for the reposted event
                    'deps': {
                        'author': {'kind': 0, 'relation': 'author', 'required': True},
                        'mentioned_profiles': {'relation': 'mentioned_profiles', 'required': False},
                        'quoted_event': {'relation': 'quoted_event', 'mode': 'single', 'required': False},
                        'quoted_author': {'kind': 0, 'relation': 'quoted_author', 'required': False},
                        'reactions': {'kind': 7, 'relation': 'e_tag', 'mode': 'aggregate', 'stats': ['count', 'by_content']},
                        'zaps': {'kind': 9735, 'relation': 'e_tag', 'mode': 'aggregate', 'stats': ['count', 'total_sats']},
                        'reposts': {'kind': 6, 'relation': 'e_tag', 'mode': 'aggregate', 'stats': ['count']},
                        'replies': {'kind': 1, 'relation': 'e_tag', 'mode': 'aggregate', 'stats': ['count']}
                    }
                },
                'reposted_author': {'kind': 0, 'relation': 'reposted_author', 'required': False},
                'reactions': {'kind': 7, 'relation': 'e_tag', 'mode': 'aggregate', 'stats': ['count', 'by_content']},
                'zaps': {'kind': 9735, 'relation': 'e_tag', 'mode': 'aggregate', 'stats': ['count', 'total_sats']},
                'reposts': {'kind': 6, 'relation': 'e_tag', 'mode': 'aggregate', 'stats': ['count']}
            })

        # Regular events (kind 1): Use e_tag for engagement
        elif kind == 1:
            config['deps'].update({
                # Parent thread chain (recursive, goes all the way up)
                'parent_thread': {'relation': 'parent_thread', 'max_depth': max_depth, 'required': False},
                # Quoted events (nevent mentions)
                'quoted_event': {'relation': 'quoted_event', 'mode': 'single', 'required': False},
                'quoted_author': {'kind': 0, 'relation': 'quoted_author', 'required': False},
                # Mentioned profiles (nprofile/npub mentions)
                'mentioned_profiles': {'relation': 'mentioned_profiles', 'required': False},
                # Engagement metrics
                'reactions': {'kind': 7, 'relation': 'e_tag', 'mode': 'aggregate', 'stats': ['count', 'by_content']},
                'zaps': {'kind': 9735, 'relation': 'e_tag', 'mode': 'aggregate', 'stats': ['count', 'total_sats']},
                'reposts': {'kind': 6, 'relation': 'e_tag', 'mode': 'aggregate', 'stats': ['count']},
                # Replies tree (recursive expansion with nested deps)
                'replies': {
                    'kind': 1,
                    'relation': 'e_tag',
                    'mode': 'expanded',
                    'recursive': True,
                    'max_depth': max_depth,
                    # Nested deps: each reply should also have these enriched
                    'deps': {
                        'author': {'kind': 0, 'relation': 'author', 'required': True},
                        'mentioned_profiles': {'relation': 'mentioned_profiles', 'required': False},
                        'reactions': {'kind': 7, 'relation': 'e_tag', 'mode': 'aggregate', 'stats': ['count', 'by_content']},
                        'zaps': {'kind': 9735, 'relation': 'e_tag', 'mode': 'aggregate', 'stats': ['count', 'total_sats']},
                        'reposts': {'kind': 6, 'relation': 'e_tag', 'mode': 'aggregate', 'stats': ['count']},
                        # Recursive replies (will inherit these same deps)
                        'replies': {
                            'kind': 1,
                            'relation': 'e_tag',
                            'mode': 'expanded',
                            'recursive': True,
                            'max_depth': max_depth
                        }
                    }
                }
            })

        # Addressable events (kind 30000+): Use a_tag for engagement
        elif kind >= 30000:
            config['deps'].update({
                'reactions': {'kind': 7, 'relation': 'a_tag', 'mode': 'aggregate', 'stats': ['count', 'by_content']},
                'zaps': {'kind': 9735, 'relation': 'a_tag', 'mode': 'aggregate', 'stats': ['count', 'total_sats']},
                'reposts': {'kind': 6, 'relation': 'a_tag', 'mode': 'aggregate', 'stats': ['count']},
                'replies': {'kind': 1, 'relation': 'a_tag', 'mode': 'aggregate', 'stats': ['count']}
            })

            # Publications (30040): Add sections with full tree
            if kind == 30040:
                config['deps']['sections'] = {
                    'kind': 30041,
                    'relation': 'a_tag_children',
                    'mode': 'expanded',
                    'required': False
                }
                config['deps']['wiki_pages'] = {
                    'kind': 30818,
                    'relation': 'a_tag_children',
                    'mode': 'expanded',
                    'required': False
                }
                config['deps']['articles'] = {
                    'kind': 30023,
                    'relation': 'a_tag_children',
                    'mode': 'expanded',
                    'required': False
                }

        return config

    def _transform_for_feed_card(self, enriched: Dict[str, Any], kind: int) -> Dict[str, Any]:
        """
        Transform enriched event to feed card format.

        Args:
            enriched: Enriched event from generic enricher
            kind: Event kind

        Returns:
            Transformed data for SPA feed view (compatible with app.js expectations)
        """
        # Publications (kind 30040) need special transformation for publications SPA
        if kind == 30040:
            return self._transform_publication_card(enriched)

        # Other kinds: Return enriched format as-is for app.js compatibility
        # app.js expects: {event, deps, meta}
        return enriched

    def _transform_publication_card(self, enriched: Dict[str, Any]) -> Dict[str, Any]:
        """Transform publication to format expected by publications SPA."""
        event = enriched['event']
        deps = enriched['deps']

        # Extract metadata from tags
        title = self._get_tag_value(event, 'title') or 'Untitled'
        summary = self._get_tag_value(event, 'summary') or event.get('content', '')[:200]
        d_tag = self._get_tag_value(event, 'd') or 'unknown'

        # Extract publication author from 'author' tag (content creator)
        publication_author = self._get_tag_value(event, 'author')

        # Build address
        address = f"{event['kind']}:{event['pubkey']}:{d_tag}"

        # Get section count
        sections_dep = deps.get('sections', {})
        section_count = sections_dep.get('count', 0) if isinstance(sections_dep, dict) else len(sections_dep) if isinstance(sections_dep, list) else 0

        # Get index author (kind 0 profile of event pubkey)
        author = deps.get('author', {})

        # Get stats
        reactions = deps.get('reactions', {}).get('count', 0)
        reposts = deps.get('reposts', {}).get('count', 0)
        zaps = deps.get('zaps', {}).get('count', 0)
        total_sats = deps.get('zaps', {}).get('total_sats', 0)
        replies = deps.get('replies', {}).get('count', 0)

        return {
            'address': address,
            'title': title,
            'summary': summary,
            'section_count': section_count,
            'created_at': event.get('created_at', 0),
            'publication_author': publication_author,  # Content author from tag
            'author': {  # Index author (who published to Nostr)
                'pubkey': author.get('pubkey', event['pubkey']),
                'display_name': author.get('display_name') or author.get('name') or event['pubkey'][:8],
                'name': author.get('name'),
                'picture': author.get('picture'),
                'nip05': author.get('nip05')
            },
            'stats': {
                'reactions': reactions,
                'reposts': reposts,
                'zaps': zaps,
                'total_sats': total_sats,
                'replies': replies
            }
        }

    def _transform_for_detail_view(self, enriched: Dict[str, Any], kind: int) -> Dict[str, Any]:
        """
        Transform enriched event to detail view format.

        Args:
            enriched: Enriched event from generic enricher
            kind: Event kind

        Returns:
            Transformed data for SPA detail view (compatible with app.js expectations)
        """
        # Publications (kind 30040) need special transformation for publications SPA
        if kind == 30040:
            return self._transform_publication_full(enriched)

        # Other kinds: Return enriched format as-is for app.js compatibility
        # app.js expects: {event, deps, meta}
        return enriched

    def _transform_for_thread_view(self, enriched: Dict[str, Any]) -> Dict[str, Any]:
        """
        Transform enriched event to thread view format.

        Thread view shows the full conversation tree with all replies expanded.

        Args:
            enriched: Enriched root event with expanded replies

        Returns:
            Transformed data for SPA thread view
        """
        # Return enriched format as-is - it already contains the full thread structure
        # with recursively expanded replies
        return enriched

    def _transform_replies_tree(self, replies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Transform replies tree to simpler format for SPA.

        Args:
            replies: List of enriched reply events

        Returns:
            Simplified replies tree
        """
        result = []
        for reply_enriched in replies:
            reply_event = reply_enriched['event']
            reply_deps = reply_enriched.get('deps', {})
            reply_author = reply_deps.get('author', {})

            simplified = {
                'id': reply_event['id'],
                'content': reply_event.get('content', ''),
                'created_at': reply_event.get('created_at', 0),
                'author': {
                    'pubkey': reply_author.get('pubkey', reply_event['pubkey']),
                    'display_name': reply_author.get('display_name') or reply_author.get('name') or reply_event['pubkey'][:8],
                    'name': reply_author.get('name')
                },
                'depth': reply_enriched.get('meta', {}).get('depth', 0)
            }

            # Recursively transform sub-replies
            if 'replies' in reply_deps and isinstance(reply_deps['replies'], list):
                simplified['replies'] = self._transform_replies_tree(reply_deps['replies'])
            else:
                simplified['replies'] = []

            result.append(simplified)

        return result

    def _flatten_sections(self, sections: List[Dict[str, Any]], result: List[Dict[str, Any]], level: int):
        """Recursively flatten sections into list."""
        for section_enriched in sections:
            section_event = section_enriched['event']
            section_deps = section_enriched.get('deps', {})

            # Extract section metadata
            title = self._get_tag_value(section_event, 'title') or 'Untitled Section'
            d_tag = self._get_tag_value(section_event, 'd') or 'unknown'
            content_format = self._get_tag_value(section_event, 'content-type') or 'text/plain'

            address = f"{section_event['kind']}:{section_event['pubkey']}:{d_tag}"

            # Get stats for this section
            reactions = section_deps.get('reactions', {}).get('count', 0) if 'reactions' in section_deps else 0
            reposts = section_deps.get('reposts', {}).get('count', 0) if 'reposts' in section_deps else 0
            zaps = section_deps.get('zaps', {}).get('count', 0) if 'zaps' in section_deps else 0
            total_sats = section_deps.get('zaps', {}).get('total_sats', 0) if 'zaps' in section_deps else 0

            result.append({
                'address': address,
                'title': title,
                'content': section_event.get('content', ''),
                'content_format': content_format,
                'level': level,
                'kind': section_event.get('kind'),
                'stats': {
                    'reactions': reactions,
                    'reposts': reposts,
                    'zaps': zaps,
                    'total_sats': total_sats
                }
            })

            # Recursively flatten children if they exist
            if 'sections' in section_deps:
                self._flatten_sections(section_deps['sections'], result, level + 1)

    def _generate_toc(self, sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Generate table of contents from sections."""
        toc = []
        for section_enriched in sections:
            section_event = section_enriched['event']

            title = self._get_tag_value(section_event, 'title') or 'Untitled'
            d_tag = self._get_tag_value(section_event, 'd') or 'unknown'
            address = f"{section_event['kind']}:{section_event['pubkey']}:{d_tag}"

            entry = {
                'address': address,
                'title': title,
                'children': []
            }

            # Recursively add children
            section_deps = section_enriched.get('deps', {})
            if 'sections' in section_deps:
                entry['children'] = self._generate_toc(section_deps['sections'])

            toc.append(entry)

        return toc

    def _transform_publication_full(self, enriched: Dict[str, Any]) -> Dict[str, Any]:
        """Transform full publication with sections to format expected by publications SPA."""
        event = enriched['event']
        deps = enriched['deps']

        # Extract metadata
        title = self._get_tag_value(event, 'title') or 'Untitled'
        summary = self._get_tag_value(event, 'summary')
        d_tag = self._get_tag_value(event, 'd') or 'unknown'
        tags = self._get_all_tag_values(event, 't')

        address = f"{event['kind']}:{event['pubkey']}:{d_tag}"

        # Get author
        author = deps.get('author', {})

        # Flatten sections from enriched tree
        sections = []
        if 'sections' in deps:
            self._flatten_sections(deps['sections'], sections, level=0)

        # Generate TOC from sections
        toc = self._generate_toc(deps.get('sections', []))

        # Get stats
        reactions = deps.get('reactions', {}).get('count', 0)
        reposts = deps.get('reposts', {}).get('count', 0)
        zaps = deps.get('zaps', {}).get('count', 0)
        total_sats = deps.get('zaps', {}).get('total_sats', 0)
        replies = deps.get('replies', {}).get('count', 0)

        return {
            'address': address,
            'title': title,
            'summary': summary,
            'tags': tags,
            'created_at': event.get('created_at', 0),
            'author': {
                'pubkey': author.get('pubkey', event['pubkey']),
                'display_name': author.get('display_name') or author.get('name') or event['pubkey'][:8],
                'name': author.get('name'),
                'picture': author.get('picture'),
                'nip05': author.get('nip05')
            },
            'stats': {
                'reactions': reactions,
                'reposts': reposts,
                'zaps': zaps,
                'total_sats': total_sats,
                'replies': replies
            },
            'sections': sections,
            'toc': toc,
            'section_count': len(sections)
        }

    def _get_all_tag_values(self, event: Dict[str, Any], tag_name: str) -> List[str]:
        """Extract all values for a tag."""
        return [tag[1] for tag in event.get('tags', [])
                if tag and tag[0] == tag_name and len(tag) > 1]

    def _get_tag_value(self, event: Dict[str, Any], tag_name: str) -> Optional[str]:
        """Extract single tag value."""
        for tag in event.get('tags', []):
            if tag and tag[0] == tag_name and len(tag) > 1:
                return tag[1]
        return None

    def refresh_feed(self, feed_name: str, limit: int = 50) -> Dict[str, Any]:
        """
        Trigger immediate relay fetch for this feed (on-demand).

        Runs nak fetch in background thread, returns immediately.
        This complements the automatic 15-second daemon loop by allowing
        users to manually trigger a fetch right now.

        Args:
            feed_name: Feed name from config
            limit: Maximum number of events to fetch

        Returns:
            Status information (202 Accepted)
        """
        import threading
        import sys

        feed_config = self.config.get_feed(feed_name)
        if not feed_config:
            raise ValueError(f"Feed '{feed_name}' not found")

        # Capture for thread
        config = self.config
        db_manager = self.db_manager

        # Background fetch function
        def background_fetch():
            try:
                print(f"\n[Manual Refresh] Starting nak fetch for {feed_name}...", file=sys.stderr)
                # Create fetcher inside thread to avoid blocking
                fetcher = FeedFetcher(config, db_manager)
                fetched_count = fetcher.fetch_feed(feed_name, limit=limit)
                print(f"[Manual Refresh] ✓ Fetched {fetched_count} new events for {feed_name}\n", file=sys.stderr)
            except Exception as e:
                import traceback
                print(f"[Manual Refresh] ✗ Failed to fetch {feed_name}: {e}", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)

        # Start thread and return immediately
        thread = threading.Thread(target=background_fetch, daemon=True)
        thread.start()

        return {
            'status': 'accepted',
            'feed': feed_name,
            'message': f"Manual fetch started (running in background)"
        }


# Global API instance
api = None


class NostrFeedsHandler(BaseHTTPRequestHandler):
    """HTTP request handler for nostr-feeds API."""

    def do_POST(self):
        """Handle POST requests."""
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query_params = parse_qs(parsed_url.query)

        if path.startswith('/api/feed/') and path.endswith('/refresh'):
            # Extract feed name from path: /api/feed/<name>/refresh
            parts = path.split('/')
            if len(parts) >= 4:
                feed_name = unquote(parts[3])
                self.serve_feed_refresh(feed_name, query_params)
            else:
                self.send_error(400, 'Invalid refresh path')
        else:
            self.send_error(404, 'Not found')

    def do_GET(self):
        """Handle GET requests."""
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query_params = parse_qs(parsed_url.query)

        if path == '/' or path == '/index.html':
            self.serve_spa(query_params)
        elif path == '/spa.html':
            # Explicit publications SPA route
            self.serve_publications_spa()
        elif path == '/api/feeds':
            self.serve_feeds_list()
        elif path == '/api/publications':
            # Publications SPA compatibility endpoint (maps to /api/feed/publications)
            self.serve_publications_list(query_params)
        elif path.startswith('/api/publication/'):
            # Publications SPA compatibility endpoint (maps to /api/event/<address>)
            address = unquote(path[17:])  # Remove '/api/publication/'
            self.serve_publication_detail(address, query_params)
        elif path.startswith('/api/feed/'):
            feed_name = unquote(path[10:])  # Remove '/api/feed/'
            self.serve_feed(feed_name, query_params)
        elif path.startswith('/api/event/'):
            event_id = unquote(path[11:])  # Remove '/api/event/'
            self.serve_event(event_id, query_params)
        elif path.startswith('/api/thread/'):
            event_id = unquote(path[12:])  # Remove '/api/thread/'
            self.serve_thread(event_id, query_params)
        elif path.startswith('/api/profile/'):
            # Profile routes: /api/profile/<pubkey> or /api/profile/<pubkey>/feed/<feed_name>
            rest_path = unquote(path[13:])  # Remove '/api/profile/'
            if '/feed/' in rest_path:
                # /api/profile/<pubkey>/feed/<feed_name>
                parts = rest_path.split('/feed/')
                pubkey = parts[0]
                feed_name = parts[1] if len(parts) > 1 else ''
                self.serve_profile_feed(pubkey, feed_name, query_params)
            else:
                # /api/profile/<pubkey>
                pubkey = rest_path
                self.serve_profile(pubkey, query_params)
        elif path.endswith('.css') or path.endswith('.js'):
            self.serve_static(path)
        else:
            self.send_error(404, 'Not found')

    def serve_spa(self, query_params):
        """Serve unified SPA with publications-style design for all feeds."""
        try:
            # Always serve spa.html - unified SPA with clean design for all feed types
            template_path = Path(__file__).parent.parent / 'templates' / 'browser' / 'spa.html'

            with open(template_path, 'rb') as f:
                content = f.read()

            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, f'Error serving SPA: {e}')

    def serve_publications_spa(self):
        """Serve the publications SPA HTML file."""
        try:
            template_path = Path(__file__).parent.parent / 'templates' / 'browser' / 'spa.html'

            with open(template_path, 'rb') as f:
                content = f.read()

            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, f'Error serving publications SPA: {e}')

    def serve_static(self, path):
        """Serve static CSS/JS files."""
        try:
            template_dir = Path(__file__).parent.parent / 'templates' / 'browser'
            file_path = template_dir / path.lstrip('/')

            if not file_path.exists():
                self.send_error(404, 'File not found')
                return

            # Determine content type
            if path.endswith('.css'):
                content_type = 'text/css'
            elif path.endswith('.js'):
                content_type = 'application/javascript'
            else:
                content_type = 'text/plain'

            with open(file_path, 'rb') as f:
                content = f.read()

            self.send_response(200)
            self.send_header('Content-type', content_type)
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, f'Error serving static file: {e}')

    def serve_publications_list(self, query_params):
        """Serve publications list API (publications SPA compatibility)."""
        try:
            limit = int(query_params.get('limit', ['50'])[0])
            since = int(query_params.get('since', ['0'])[0]) or None
            user_npub = query_params.get('user', [None])[0]

            # Get publications feed
            events = api.get_feed('publications', limit=limit, since=since, user_npub=user_npub)

            # Publications SPA expects {publications: [...], count: N}
            response = {
                'publications': events,
                'count': len(events)
            }

            self.send_json_response(response)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.send_error(500, f'Error fetching publications: {e}')

    def serve_publication_detail(self, address, query_params):
        """Serve single publication detail (publications SPA compatibility)."""
        try:
            max_depth = int(query_params.get('depth', ['5'])[0])

            # address format: "30040:pubkey:d_tag"
            event = api.get_event(address, max_depth=max_depth)

            if not event:
                self.send_error(404, 'Publication not found')
                return

            # Publications SPA expects direct object, not wrapped
            self.send_json_response(event)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.send_error(500, f'Error fetching publication: {e}')

    def serve_feeds_list(self):
        """Serve feeds list API."""
        try:
            feeds = api.list_feeds()

            response = {
                'feeds': feeds,
                'count': len(feeds)
            }

            self.send_json_response(response)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.send_error(500, f'Error fetching feeds: {e}')

    def serve_feed(self, feed_name, query_params):
        """Serve feed API."""
        try:
            limit = int(query_params.get('limit', ['50'])[0])
            since = int(query_params.get('since', ['0'])[0]) or None
            until = int(query_params.get('until', ['0'])[0]) or None
            user_npub = query_params.get('user', [None])[0]

            # Log backfill requests
            if until:
                import datetime
                print(f"[API] Backfill request: feed={feed_name}, limit={limit}, until={until} ({datetime.datetime.fromtimestamp(until).isoformat()})", file=sys.stderr)

            events = api.get_feed(feed_name, limit=limit, since=since, until=until, user_npub=user_npub)

            if until:
                print(f"[API] Backfill response: {len(events)} events returned", file=sys.stderr)

            response = {
                'feed': feed_name,
                'events': events,
                'count': len(events),
                'limit': limit
            }

            self.send_json_response(response)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.send_error(500, f'Error fetching feed: {e}')

    def serve_event(self, event_id, query_params):
        """Serve single event API."""
        try:
            max_depth = int(query_params.get('depth', ['5'])[0])

            event = api.get_event(event_id, max_depth=max_depth)

            if not event:
                self.send_error(404, 'Event not found')
                return

            self.send_json_response(event)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.send_error(500, f'Error fetching event: {e}')

    def serve_thread(self, event_id, query_params):
        """Serve thread view API - finds root and expands full conversation."""
        try:
            max_depth = int(query_params.get('depth', ['5'])[0])

            thread = api.get_thread(event_id, max_depth=max_depth)

            if not thread:
                self.send_error(404, 'Thread not found')
                return

            self.send_json_response(thread)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.send_error(500, f'Error fetching thread: {e}')

    def serve_profile(self, pubkey, query_params):
        """Serve profile information with available feeds."""
        try:
            # Get profile metadata
            profile = api.get_profile(pubkey)

            if not profile:
                self.send_error(404, 'Profile not found')
                return

            # Get available feeds for this user
            feeds = api.get_profile_feeds(pubkey)

            # Combine profile + feeds
            response = {
                'profile': profile,
                'feeds': feeds,
                'count': len(feeds)
            }

            self.send_json_response(response)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.send_error(500, f'Error fetching profile: {e}')

    def serve_profile_feed(self, pubkey, feed_name, query_params):
        """Serve profile feed (events by author from specific feed)."""
        try:
            limit = int(query_params.get('limit', ['50'])[0])
            since = int(query_params.get('since', ['0'])[0]) or None
            until = int(query_params.get('until', ['0'])[0]) or None

            # Get events by this author from the specified feed
            events = api.get_profile_feed(pubkey, feed_name, limit=limit, since=since, until=until)

            response = {
                'pubkey': pubkey,
                'feed': feed_name,
                'events': events,
                'count': len(events),
                'limit': limit
            }

            self.send_json_response(response)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.send_error(500, f'Error fetching profile feed: {e}')

    def serve_feed_refresh(self, feed_name, query_params):
        """Serve feed refresh API (fetch new events from relays)."""
        try:
            limit = int(query_params.get('limit', ['50'])[0])

            result = api.refresh_feed(feed_name, limit=limit)

            self.send_json_response(result)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.send_error(500, f'Error refreshing feed: {e}')

    def send_json_response(self, data):
        """Send JSON response."""
        json_data = json.dumps(data, indent=2).encode('utf-8')

        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Content-Length', str(len(json_data)))
        self.send_header('Access-Control-Allow-Origin', '*')  # Enable CORS
        self.end_headers()
        self.wfile.write(json_data)

    def log_message(self, format, *args):
        """Log requests to stderr."""
        sys.stderr.write(f"[{self.log_date_time_string()}] {format % args}\n")


def main():
    """Run the server."""
    parser = argparse.ArgumentParser(description="Nostr Feeds API Server (All Feed Types)")
    parser.add_argument('--config', '-c', default='enricher/config.toml',
                       help='Config file path (default: enricher/config.toml)')
    parser.add_argument('--host', default='127.0.0.1',
                       help='Host to bind to (default: 127.0.0.1)')
    parser.add_argument('--port', '-p', type=int, default=8080,
                       help='Port to bind to (default: 8080)')
    parser.add_argument('--local', '--dev', action='store_true',
                       help='Use local directory for databases (dev mode)')

    args = parser.parse_args()

    # Initialize API
    global api
    try:
        api = NostrFeedsAPI(args.config)

        if args.local:
            print(f"Development mode: Using configured db_dir: {api.db_manager.db_dir}", file=sys.stderr)
    except Exception as e:
        print(f"Error initializing API: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print(f"🚀 Nostr Feeds API Server (Config-Driven!)", file=sys.stderr)
    print(f"   Config: {args.config}", file=sys.stderr)
    print(f"   URL: http://{args.host}:{args.port}", file=sys.stderr)
    print(f"\n   API Endpoints:", file=sys.stderr)
    print(f"     GET  /                           - SPA interface", file=sys.stderr)
    print(f"     GET  /api/feeds                  - List all feeds", file=sys.stderr)
    print(f"     GET  /api/feed/<name>?limit=50   - Feed events", file=sys.stderr)
    print(f"     GET  /api/event/<id>?depth=5     - Single event", file=sys.stderr)
    print(f"\n   Supported Feed Types:", file=sys.stderr)
    print(f"     - Kind 1: Notes (with threads)", file=sys.stderr)
    print(f"     - Kind 6: Reposts", file=sys.stderr)
    print(f"     - Kind 9735: Zaps", file=sys.stderr)
    print(f"     - Kind 9802: Highlights (NIP-84)", file=sys.stderr)
    print(f"     - Kind 30023: Long-form articles", file=sys.stderr)
    print(f"     - Kind 30040: Publications (with sections)", file=sys.stderr)
    print(f"\n   Press Ctrl+C to stop\n", file=sys.stderr)

    # Run server
    server = HTTPServer((args.host, args.port), NostrFeedsHandler)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\nShutting down server...", file=sys.stderr)
        server.shutdown()


if __name__ == '__main__':
    main()
