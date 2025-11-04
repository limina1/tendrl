#!/usr/bin/env python3
"""
Tendrl Render - Jinja2 Template Rendering for Enriched Nostr Feeds

This script fetches enriched events from tendrl_query and renders them
using Jinja2 templates adapted from Primal's UI design.

Usage:
    # Render to HTML file
    python tendrl_render.py --feed notes_enriched --output feed.html

    # Run web server
    python tendrl_render.py --serve --port 5000

    # Stream mode (real-time updates)
    python tendrl_render.py --serve --stream

Dependencies:
    pip install jinja2 flask python-dateutil
"""

import argparse
import json
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

# Optional dependencies
try:
    from flask import Flask, render_template, jsonify, Response
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False

try:
    from dateutil.relativedelta import relativedelta
    DATEUTIL_AVAILABLE = True
except ImportError:
    DATEUTIL_AVAILABLE = False


class TendrlRenderer:
    """Renders Tendrl enriched events using Jinja2 templates"""

    def __init__(self, templates_dir: str = "templates"):
        """Initialize Jinja2 environment with custom filters"""
        self.env = Environment(
            loader=FileSystemLoader(templates_dir),
            autoescape=select_autoescape(['html', 'xml'])
        )

        # Register custom filters
        self.env.filters['timestamp_to_relative'] = self.timestamp_to_relative
        self.env.filters['format_sats'] = self.format_sats
        self.env.filters['nl2br'] = self.nl2br
        self.env.filters['linkify'] = self.linkify
        self.env.filters['slice'] = lambda items, count: list(items)[:count]

    @staticmethod
    def timestamp_to_relative(timestamp: int) -> str:
        """Convert Unix timestamp to relative time (e.g., '2 hours ago')"""
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = now - dt

        if delta.days > 365:
            years = delta.days // 365
            return f"{years} year{'s' if years != 1 else ''} ago"
        elif delta.days > 30:
            months = delta.days // 30
            return f"{months} month{'s' if months != 1 else ''} ago"
        elif delta.days > 0:
            return f"{delta.days} day{'s' if delta.days != 1 else ''} ago"
        elif delta.seconds > 3600:
            hours = delta.seconds // 3600
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        elif delta.seconds > 60:
            minutes = delta.seconds // 60
            return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        else:
            return "just now"

    @staticmethod
    def format_sats(sats: int) -> str:
        """Format satoshi amount (e.g., 21000 -> '21k')"""
        if sats >= 1_000_000:
            return f"{sats / 1_000_000:.1f}M"
        elif sats >= 1_000:
            return f"{sats / 1_000:.1f}k"
        else:
            return str(sats)

    @staticmethod
    def nl2br(text: str) -> str:
        """Convert newlines to <br> tags"""
        return text.replace('\n', '<br>\n')

    @staticmethod
    def linkify(text: str) -> str:
        """Convert URLs to clickable links"""
        import re
        url_pattern = r'(https?://[^\s]+)'
        return re.sub(
            url_pattern,
            r'<a href="\1" target="_blank" rel="noopener">\1</a>',
            text
        )

    def fetch_enriched_feed(
        self,
        feed_name: str = "notes_enriched",
        config_path: str = "./tendrl_enrichment_test.toml",
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Fetch enriched events from tendrl_query"""
        cmd = [
            "./target/release/tendrl_query",
            "--feed", feed_name,
            "--config", config_path,
            "--limit", str(limit)
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )

            # Parse JSONL output
            events = []
            for line in result.stdout.strip().split('\n'):
                if line:
                    events.append(json.loads(line))

            return events

        except subprocess.CalledProcessError as e:
            print(f"Error running tendrl_query: {e.stderr}")
            return []
        except json.JSONDecodeError as e:
            print(f"Error parsing JSONL: {e}")
            return []

    def render_feed(
        self,
        events: List[Dict[str, Any]],
        feed_name: str = "Feed",
        feed_description: str = "",
        use_primal_layout: bool = True
    ) -> str:
        """Render feed template with enriched events"""
        template_name = 'feed_primal.html.j2' if use_primal_layout else 'feed.html.j2'
        template = self.env.get_template(template_name)
        return template.render(
            notes=events,
            feed_name=feed_name,
            feed_description=feed_description,
            current_page='/'
        )

    def render_to_file(
        self,
        output_path: str,
        feed_name: str = "notes_enriched",
        config_path: str = "./tendrl_enrichment_test.toml",
        limit: int = 50
    ):
        """Fetch and render feed to HTML file"""
        print(f"Fetching {limit} events from feed '{feed_name}'...")
        events = self.fetch_enriched_feed(feed_name, config_path, limit)

        print(f"Found {len(events)} enriched events")
        print("Rendering to HTML...")

        html = self.render_feed(events, feed_name)

        Path(output_path).write_text(html, encoding='utf-8')
        print(f"Rendered feed to: {output_path}")


def create_flask_app(renderer: TendrlRenderer):
    """Create Flask web server for live feed rendering"""
    if not FLASK_AVAILABLE:
        raise ImportError("Flask is required for --serve mode. Install with: pip install flask")
    app = Flask(__name__, static_folder='static', template_folder='templates')

    @app.route('/')
    def index():
        """Main feed page"""
        events = renderer.fetch_enriched_feed()
        return render_template(
            'feed.html.j2',
            notes=events,
            feed_name="Tendrl Feed",
            feed_description="Enriched Nostr events with reactions, zaps, and profiles"
        )

    @app.route('/feed/<feed_name>')
    def feed(feed_name: str):
        """Render specific feed by name"""
        limit = int(request.args.get('limit', 50))
        config = request.args.get('config', './tendrl_enrichment_test.toml')

        events = renderer.fetch_enriched_feed(feed_name, config, limit)
        return render_template(
            'feed.html.j2',
            notes=events,
            feed_name=feed_name.replace('_', ' ').title(),
            feed_description=f"{len(events)} enriched events"
        )

    @app.route('/api/feed/<feed_name>')
    def api_feed(feed_name: str):
        """JSON API for enriched feed"""
        limit = int(request.args.get('limit', 50))
        config = request.args.get('config', './tendrl_enrichment_test.toml')

        events = renderer.fetch_enriched_feed(feed_name, config, limit)
        return jsonify({
            'feed': feed_name,
            'count': len(events),
            'events': events
        })

    @app.route('/stream/<feed_name>')
    def stream_feed(feed_name: str):
        """Server-Sent Events stream for real-time updates"""
        def generate():
            # Initial batch
            events = renderer.fetch_enriched_feed(feed_name, limit=10)
            for event in events:
                yield f"data: {json.dumps(event)}\n\n"

            # TODO: Watch for new events from daemon
            # This would require integrating with tendrl_daemon's event stream

        return Response(generate(), mimetype='text/event-stream')

    return app


def main():
    parser = argparse.ArgumentParser(
        description="Render Tendrl enriched feeds with Jinja2 templates"
    )
    parser.add_argument(
        '--feed',
        default='notes_enriched',
        help='Feed name from tendrl.toml (default: notes_enriched)'
    )
    parser.add_argument(
        '--config',
        default='./tendrl_enrichment_test.toml',
        help='Path to tendrl config file'
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=50,
        help='Number of events to fetch (default: 50)'
    )
    parser.add_argument(
        '--output',
        help='Output HTML file path (if not serving)'
    )
    parser.add_argument(
        '--serve',
        action='store_true',
        help='Run web server instead of rendering to file'
    )
    parser.add_argument(
        '--port',
        type=int,
        default=5000,
        help='Web server port (default: 5000)'
    )
    parser.add_argument(
        '--templates',
        default='templates',
        help='Templates directory (default: templates)'
    )

    args = parser.parse_args()

    # Initialize renderer
    renderer = TendrlRenderer(templates_dir=args.templates)

    if args.serve:
        # Run web server
        print(f"Starting Tendrl web server on http://localhost:{args.port}")
        print(f"Feed: {args.feed}")
        print("Press Ctrl+C to stop\n")

        app = create_flask_app(renderer)
        app.run(host='0.0.0.0', port=args.port, debug=True)

    else:
        # Render to file
        output_path = args.output or f"{args.feed}.html"
        renderer.render_to_file(
            output_path=output_path,
            feed_name=args.feed,
            config_path=args.config,
            limit=args.limit
        )


if __name__ == '__main__':
    main()


"""
Example usage:

1. Render feed to HTML file:
   python tendrl_render.py --feed notes_enriched --limit 100 --output feed.html

2. Run web server:
   python tendrl_render.py --serve --port 5000
   # Visit http://localhost:5000

3. Render specific feed:
   python tendrl_render.py --feed zaps_enriched --output zaps.html

4. Custom config:
   python tendrl_render.py --config ~/my_tendrl.toml --serve

5. Static site generation (for deployment):
   python tendrl_render.py --feed notes_enriched --output public/index.html
   python tendrl_render.py --feed zaps_enriched --output public/zaps.html
   # Deploy public/ folder to any static host
"""
