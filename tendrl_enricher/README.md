# nostr-feeds Enricher

**Program-agnostic Nostr event enrichment engine**

Python implementation of the streaming enrichment architecture. Reads from SQLite databases, traverses dependency trees, computes stats, and outputs enriched JSONL events.

## Features

- ✅ **Dependency tree traversal** - Configurable event relationships (profiles, reactions, replies, zaps)
- ✅ **Two-tier profile caching** - In-memory LRU + SQLite persistence
- ✅ **Aggregate statistics** - Reactions, replies, zap totals computed on-the-fly
- ✅ **SQLite per-feed databases** - Isolation and lifecycle management
- ✅ **TOML configuration** - External feed definitions
- ✅ **JSONL output** - Stream-friendly event format
- ✅ **Follow list filtering** - Timeline from users you follow
- ✅ **Multi-user support** - Configure multiple npubs with separate follow lists

## Architecture

```
Config (TOML) → DatabaseManager → Enricher → JSONL Output
                       ↓
                  ProfileCache (memory + SQLite)
                       ↓
                  Stats Computation
```

## Setup

### Quick Start

```bash
# Create virtual environment and install dependencies
./setup.sh

# Run tests
./run_tests.sh
```

### Manual Setup

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run tests
python tests/create_fixtures.py
python tests/test_integration.py
```

### Auto-activation with direnv

```bash
# Install direnv (if not already installed)
# macOS: brew install direnv
# Linux: apt install direnv

# Allow direnv for this directory
direnv allow

# Now the venv activates automatically when you cd into the directory!
```

## Usage

### Command Line

```bash
# Enrich a feed and output JSONL
python -m enricher.main --feed timeline --limit 50

# Filter by follows (uses default_npub from config)
python -m enricher.main --feed timeline --limit 50

# Filter by follows for specific user
python -m enricher.main --feed timeline --user npub1abc... --limit 50

# Show feed info
python -m enricher.main --feed timeline --info

# List all feeds
python -m enricher.main --list-feeds

# Save output to file
python -m enricher.main --feed timeline --output enriched.jsonl

# Only process events since timestamp
python -m enricher.main --feed timeline --since 1709876543
```

### Python API

```python
from enricher.config import Config
from enricher.enricher import create_enricher
from enricher.db import get_events

# Load configuration
config = Config.load('~/.config/nostr-feeds/config.toml')

# Create enricher
enricher = create_enricher(
    feed_db_path='~/.local/share/nostr-feeds/timeline.db',
    profiles_db_path='~/.local/share/nostr-feeds/profiles.db'
)

# Get events
events = get_events(enricher.feed_db, limit=50)

# Enrich events
feed_config = config.get_feed('timeline')
enriched = enricher.enrich_feed(events, feed_config)

# Process enriched events
for event in enriched:
    print(event['event']['content'])
    print(f"By: {event['deps']['author']['name']}")
    print(f"Stats: {event['meta']['formatted_stats']}")
```

## Configuration

Example `config.toml`:

```toml
[global]
db_dir = "~/.local/share/nostr-feeds"
template_dir = "~/.config/nostr-feeds/templates"
profile_cache_db = "profiles.db"

# Default user for follow-based feeds
default_npub = "npub1abc..."

# Known users (for quick switching)
known_npubs = ["npub1abc...", "npub1def..."]

# User-specific configuration
[users."npub1abc..."]
name = "alice"
relays = ["wss://relay.damus.io", "wss://nos.lol"]

[users."npub1def..."]
name = "bob"
relays = ["wss://relay.damus.io"]

# Timeline feed (filtered by follows)
[feed.timeline]
name = "timeline"
type = "follows"
db_file = "timeline.db"
refresh_interval = 15
filter_by_follows = true  # Only show posts from followed users

[feed.timeline.root]
kind = 1
template = "short-note-card.j2"

[feed.timeline.root.filter]
no_e_tags = true  # Only top-level posts

[feed.timeline.root.deps.author]
kind = 0
relation = "author"
required = true

[feed.timeline.root.deps.reactions]
kind = 7
relation = "e_tag"
mode = "aggregate"
stats = ["count", "by_content"]

[feed.timeline.root.deps.replies]
kind = 1
relation = "e_tag"
mode = "aggregate"
stats = ["count"]
expandable = true

[feed.timeline.root.deps.zaps]
kind = 9735
relation = "p_tag"
mode = "aggregate"
stats = ["count", "total_sats"]
expandable = true

# Global feed (no follow filter)
[feed.global]
name = "global"
type = "relay"
db_file = "global.db"
refresh_interval = 30
filter_by_follows = false  # Show all posts

[feed.global.root]
kind = 1
template = "short-note-card.j2"

[feed.global.root.filter]
no_e_tags = true

[feed.global.root.deps.author]
kind = 0
relation = "author"
required = true
```

## Module Overview

### `enricher/`

- **`config.py`** - TOML configuration loader with user management
- **`db.py`** - SQLite connection management and queries
- **`profiles.py`** - Two-tier profile cache (LRU + SQLite)
- **`follows.py`** - Follow list cache and filtering
- **`stats.py`** - Aggregate statistics computation
- **`utils.py`** - Formatting helpers (time, sats, tags)
- **`enricher.py`** - Core dependency tree traversal
- **`main.py`** - CLI entry point with follow filtering

### Output Format

Enriched events are output as JSONL (one JSON object per line):

```json
{
  "event": {
    "id": "abc123...",
    "pubkey": "deadbeef...",
    "created_at": 1709876543,
    "kind": 1,
    "content": "Hello Nostr!",
    "tags": [["p", "..."]],
    ...
  },
  "deps": {
    "author": {
      "pubkey": "deadbeef...",
      "name": "alice",
      "display_name": "Alice Johnson",
      ...
    },
    "reactions": {
      "count": 42,
      "by_content": {"❤️": 30, "🔥": 8},
      "expandable": false
    },
    "replies": {
      "count": 7,
      "expandable": true
    },
    "zaps": {
      "count": 15,
      "total_sats": 21000,
      "expandable": true
    }
  },
  "meta": {
    "depth": 0,
    "formatted_time": "2h ago",
    "formatted_stats": "💬 7  ❤️ 42  ⚡ 21k"
  }
}
```

## Testing

Tests use sample SQLite databases in `tests/fixtures/`:

```bash
# Create test fixtures
python tests/create_fixtures.py

# Run integration test
python tests/test_integration.py

# Or use the convenience script
./run_tests.sh
```

## Next Steps (Phase 2)

- [ ] Jinja2 renderer with semantic markers
- [ ] Template loader and cache
- [ ] Marker output format (`<<<EVENT:id>>>`)
- [ ] Emacs integration layer

## Design Documents

See project root for comprehensive architecture documentation:

- `STREAMING-ARCHITECTURE.org` - Complete pipeline design
- `ENRICHER-PROTOCOL.org` - JSON-RPC protocol spec
- `DATABASE-DESIGN.org` - SQLite schema and queries
- `CONFIG-FORMAT.org` - TOML configuration format
- `TEMPLATING-COMPARISON.org` - Template system design

## License

Part of the nostr-feeds project.
