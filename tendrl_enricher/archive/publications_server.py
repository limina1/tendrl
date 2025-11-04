#!/usr/bin/env python3
"""
HTTP API server for publications SPA.

Provides JSON endpoints for:
- Publications feed (list of kind 30040)
- Single publication with full tree (kind 30040 + all 30041 sections)

Now uses generic enricher with config-driven architecture!
"""

import sys
import json
import sqlite3
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


class PublicationAPI:
    """
    API for fetching publications data using generic enricher.
    """

    def __init__(self, config_path: str):
        """
        Initialize API with config.

        Args:
            config_path: Path to config.toml
        """
        self.config = Config.load(config_path)
        self.db_manager = DatabaseManager(self.config.get_db_dir())
        self.feed_config = self.config.get_feed('publications')

        if not self.feed_config:
            raise ValueError("Publications feed not found in config.toml")

    def get_publications_feed(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Get publications feed with basic metadata using generic enricher.

        Args:
            limit: Maximum number of publications

        Returns:
            List of enriched publication dicts
        """
        # Get databases
        feed_db_file = self.feed_config.get('db_file')
        feed_db = self.db_manager.get_connection(feed_db_file)

        profiles_db_file = self.config.get_profiles_db()
        profiles_db = self.db_manager.get_connection(profiles_db_file)

        # Create enricher
        profile_cache = ProfileCache(profiles_db, cache_ttl=self.config.get_profile_cache_ttl())
        enricher = Enricher(feed_db, profiles_db, profile_cache)

        # Get events
        display_kinds = self.feed_config.get('display_kinds', [30040])
        events = get_events(feed_db, kinds=display_kinds, limit=limit)

        # Enrich using config (but without tree expansion for feed view)
        root_config = self.feed_config.get('root', {}).copy()

        # For feed view, use aggregate mode for sections (just count)
        feed_root_config = {
            'deps': {
                'author': root_config['deps']['author'],
                'sections': {
                    'kind': 30041,
                    'relation': 'a_tag_children',  # Read sections FROM publication's a-tags
                    'mode': 'aggregate',
                    'stats': ['count']
                },
                'reactions': root_config['deps']['reactions'],
                'reposts': root_config['deps']['reposts'],
                'zaps': root_config['deps']['zaps'],
                'replies': root_config['deps']['replies']
            }
        }

        # Enrich events
        enriched_events = []
        for event in events:
            try:
                enriched = enricher.enrich_event(event, feed_root_config, depth=0)

                # Transform to format expected by SPA
                transformed = self._transform_for_feed_card(enriched)

                # Filter publications with 0 sections
                section_count = transformed.get('section_count', 0)
                if section_count == 0:
                    # Skip publications with no sections
                    continue

                enriched_events.append(transformed)
            except Exception as e:
                print(f"Error enriching publication {event.get('id')}: {e}", file=sys.stderr)
                continue

        # Sort by section count (most sections first)
        enriched_events.sort(key=lambda p: p.get('section_count', 0), reverse=True)

        return enriched_events

    def get_publication_full(self, address: str, max_depth: int = 10) -> Optional[Dict[str, Any]]:
        """
        Get full publication with all sections and TOC using generic enricher.

        Args:
            address: Publication address (kind:pubkey:d-tag or pubkey:d-tag)
            max_depth: Maximum tree depth

        Returns:
            Enriched publication dict with full tree
        """
        # Parse address
        parts = address.split(':')
        if len(parts) == 2:
            kind = 30040
            pubkey, d_tag = parts
        elif len(parts) == 3:
            kind = int(parts[0])
            pubkey = parts[1]
            d_tag = parts[2]
        else:
            return None

        # Get databases
        feed_db_file = self.feed_config.get('db_file')
        feed_db = self.db_manager.get_connection(feed_db_file)

        profiles_db_file = self.config.get_profiles_db()
        profiles_db = self.db_manager.get_connection(profiles_db_file)

        # Find root event
        root_event = get_addressable_event(feed_db, kind, pubkey, d_tag)
        if not root_event:
            return None

        # Create enricher
        profile_cache = ProfileCache(profiles_db, cache_ttl=self.config.get_profile_cache_ttl())
        enricher = Enricher(feed_db, profiles_db, profile_cache)

        # Get root config and override max_depth
        root_config = self.feed_config.get('root', {}).copy()
        if 'sub_publications' in root_config.get('deps', {}):
            root_config['deps']['sub_publications']['max_depth'] = max_depth

        # Enrich with full tree
        enriched = enricher.enrich_event(root_event, root_config, depth=0)

        # Transform to format expected by SPA
        transformed = self._transform_for_full_view(enriched)

        return transformed

    def _transform_for_feed_card(self, enriched: Dict[str, Any]) -> Dict[str, Any]:
        """Transform enriched event to feed card format expected by SPA."""
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
        section_count = sections_dep.get('count', 0)

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

    def _transform_for_full_view(self, enriched: Dict[str, Any]) -> Dict[str, Any]:
        """Transform enriched event to full publication format expected by SPA."""
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
        self._flatten_sections(deps.get('sections', []), sections, level=0)

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
            # (sections can have sub-sections via a_tag)
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

    def _get_tag_value(self, event: Dict[str, Any], tag_name: str) -> Optional[str]:
        """Extract single tag value."""
        for tag in event.get('tags', []):
            if tag and tag[0] == tag_name and len(tag) > 1:
                return tag[1]
        return None

    def _get_all_tag_values(self, event: Dict[str, Any], tag_name: str) -> List[str]:
        """Extract all values for a tag."""
        return [tag[1] for tag in event.get('tags', [])
                if tag and tag[0] == tag_name and len(tag) > 1]


# Global API instance
api = None


class PublicationHandler(BaseHTTPRequestHandler):
    """HTTP request handler for publications API."""

    def do_GET(self):
        """Handle GET requests."""
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query_params = parse_qs(parsed_url.query)

        if path == '/' or path == '/index.html':
            self.serve_spa()
        elif path == '/api/publications':
            self.serve_publications_feed(query_params)
        elif path.startswith('/api/publication/'):
            address = unquote(path[17:])  # Remove '/api/publication/'
            self.serve_publication(address, query_params)
        elif path.endswith('.css') or path.endswith('.js'):
            self.serve_static(path)
        else:
            self.send_error(404, 'Not found')

    def serve_spa(self):
        """Serve the SPA HTML file."""
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
            self.send_error(500, f'Error serving SPA: {e}')

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

    def serve_publications_feed(self, query_params):
        """Serve publications feed API."""
        try:
            limit = int(query_params.get('limit', ['50'])[0])

            publications = api.get_publications_feed(limit=limit)

            response = {
                'publications': publications,
                'count': len(publications),
                'limit': limit
            }

            self.send_json_response(response)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.send_error(500, f'Error fetching publications: {e}')

    def serve_publication(self, address, query_params):
        """Serve single publication API."""
        try:
            max_depth = int(query_params.get('max_depth', ['10'])[0])

            publication = api.get_publication_full(address, max_depth=max_depth)

            if not publication:
                self.send_error(404, 'Publication not found')
                return

            self.send_json_response(publication)
        except Exception as e:
            self.send_error(500, f'Error fetching publication: {e}')

    def send_json_response(self, data):
        """Send JSON response."""
        json_data = json.dumps(data, indent=2).encode('utf-8')

        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Content-Length', str(len(json_data)))
        self.end_headers()
        self.wfile.write(json_data)

    def log_message(self, format, *args):
        """Log requests to stderr."""
        sys.stderr.write(f"[{self.log_date_time_string()}] {format % args}\n")


def main():
    """Run the server."""
    parser = argparse.ArgumentParser(description="Publications SPA API Server")
    parser.add_argument('--config', '-c', default='enricher/config.toml',
                       help='Config file path (default: enricher/config.toml)')
    parser.add_argument('--host', default='127.0.0.1',
                       help='Host to bind to (default: 127.0.0.1)')
    parser.add_argument('--port', type=int, default=5000,
                       help='Port to bind to (default: 5000)')
    parser.add_argument('--local', '--dev', action='store_true',
                       help='Use local directory for databases (dev mode)')

    args = parser.parse_args()

    # Initialize API
    global api
    try:
        api = PublicationAPI(args.config)

        # Note: --local mode uses the db_dir from config (already initialized)
        if args.local:
            print(f"Development mode: Using configured db_dir: {api.db_manager.db_dir}", file=sys.stderr)
    except Exception as e:
        print(f"Error initializing API: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print(f"🚀 Publications SPA Server (Config-Driven!)", file=sys.stderr)
    print(f"   Config: {args.config}", file=sys.stderr)
    print(f"   URL: http://{args.host}:{args.port}", file=sys.stderr)
    print(f"\n   API Endpoints:", file=sys.stderr)
    print(f"     GET  /                                  - SPA interface", file=sys.stderr)
    print(f"     GET  /api/publications?limit=50         - Publications feed", file=sys.stderr)
    print(f"     GET  /api/publication/<address>         - Single publication", file=sys.stderr)
    print(f"\n   Press Ctrl+C to stop\n", file=sys.stderr)

    # Run server
    server = HTTPServer((args.host, args.port), PublicationHandler)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\nShutting down server...", file=sys.stderr)
        server.shutdown()


if __name__ == '__main__':
    main()
