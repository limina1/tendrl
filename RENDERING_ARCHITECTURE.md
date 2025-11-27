# Tendrl Rendering Architecture

**Branch**: `feature/nostr-feeds-integration`

## Overview

This branch implements a **dual-path rendering system** for Nostr events, combining Rust's performance with Python's flexibility.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        NOSTR RELAYS                              │
│  wss://relay.damus.io, wss://nos.lol, wss://relay.nostr.band   │
└────────────────────┬────────────────────────────────────────────┘
                     │
                     ▼
         ┌────────────────────────┐
         │   tendrl_daemon (Rust) │  ← Fetches events via enostr
         │   - WebSocket client   │
         │   - Stores in nostrdb  │
         └───────────┬────────────┘
                     │
                     ▼
              ┌──────────────┐
              │   nostrdb    │  ← LMDB database (memory-mapped)
              │   (LMDB)     │
              └──────┬───────┘
                     │
        ┌────────────┴────────────┐
        │                         │
        ▼                         ▼
┌──────────────────┐    ┌──────────────────────┐
│  PATH 1: RUST    │    │  PATH 2: PYTHON      │
│  tendrl_ws       │    │  tendrl_enricher     │
└──────────────────┘    └──────────────────────┘
```

## Path 1: Rust Web Server (tendrl_ws)

**Fast, direct rendering for browsers**

```
┌─────────────┐
│  Browser    │
└──────┬──────┘
       │ HTTP GET /feed
       │ HTTP GET /publication/:id
       ▼
┌─────────────────────────┐
│   tendrl_ws (Axum)      │
│   - Queries nostrdb     │  ← Direct LMDB access
│   - Renders via Tera    │  ← Jinja-style templates
│   - Serves HTML         │
└──────┬──────────────────┘
       │
       ▼
┌─────────────────────────┐
│  Tera Templates         │
│  - feed.html            │
│  - publication.html     │
│  - visualize.html       │
└─────────────────────────┘
```

**Features:**
- ⚡ **Fast**: Direct LMDB queries (< 1ms)
- 🦀 **Rust**: Type-safe, compiled performance
- 🎨 **Tera**: Jinja2-compatible templates
- 🌐 **Routes**: `/feed`, `/publication/:type/:id`, `/visualize`

**Use Cases:**
- Quick publication browsing
- Low-latency rendering
- Production-ready server

## Path 2: Python Enricher + HTTP API (tendrl_enricher)

**Rich enrichment with dependency traversal**

```
┌─────────────┐
│  nostrdb    │  ← Source events
└──────┬──────┘
       │ Export to SQLite
       ▼
┌─────────────────────────┐
│  tendrl_enricher        │
│  - SQLite per-feed DBs  │  ← timeline.db, zaps.db, etc.
│  - profiles.db          │  ← Centralized profile cache
│  - Dependency traversal │  ← Author, reactions, replies, zaps
│  - Stat computation     │  ← Aggregate counts, totals
└──────┬──────────────────┘
       │
       ▼
┌─────────────────────────┐
│   Enriched Events       │
│   {event, deps, meta}   │  ← JSONL format
└──────┬──────────────────┘
       │
       ├──────────────────┐
       │                  │
       ▼                  ▼
┌──────────────┐   ┌──────────────────┐
│ renderer.py  │   │   server.py      │
│ - Jinja2     │   │   - HTTP API     │
│ - Templates  │   │   - JSON         │
│ - HTML/Text  │   │   - SPA          │
└──────────────┘   └─────┬────────────┘
                         │
                         ▼
                  ┌──────────────────┐
                  │  Browser SPA     │
                  │  - JavaScript    │
                  │  - Fetch API     │
                  │  - Dynamic UI    │
                  └──────────────────┘
```

**Features:**
- 🔗 **Dependency Traversal**: Profiles, reactions, replies, zaps
- 📊 **Stats**: Aggregate counts, totals, breakdowns
- 🎨 **Dual Rendering**:
  - Plain text with semantic markers (Emacs/editor integration)
  - HTML with full styling
- 🌐 **HTTP API**: JSON endpoints for SPAs
- 💾 **Two-tier Caching**: In-memory LRU + SQLite

**Use Cases:**
- Rich feed rendering with stats
- Thread expansion
- Profile enrichment
- SPA/mobile app backend

## Data Flow: Event Lifecycle

### 1. Event Fetching (Rust)

```rust
// tendrl_daemon/src/main.rs
RelayPool::connect(relays)
    .subscribe(filters)  // From tendrl.toml
    .on_event(|event| {
        ndb.process_event(event)  // Store in LMDB
    })
```

### 2. Export to SQLite (Python)

```python
# tendrl_enricher/db.py
events = query_nostrdb(kinds=[1, 6, 9735])
for event in events:
    feed_db.execute(
        "INSERT INTO events (id, pubkey, created_at, kind, content, tags) VALUES (?, ?, ?, ?, ?, ?)",
        (event['id'], event['pubkey'], event['created_at'], event['kind'], event['content'], json.dumps(event['tags']))
    )
```

### 3. Enrichment (Python)

```python
# tendrl_enricher/enricher.py
enriched = {
    'event': event,
    'deps': {
        'author': get_profile(event['pubkey']),           # Kind 0
        'reactions': aggregate_reactions(event['id']),    # Kind 7
        'replies': aggregate_replies(event['id']),        # Kind 1 with e-tag
        'zaps': aggregate_zaps(event['id']),              # Kind 9735
    },
    'meta': {
        'formatted_time': '2h ago',
        'formatted_stats': '💬 7  ❤️ 42  ⚡ 21k'
    }
}
```

### 4. Rendering (Jinja2)

**Template Selection:**

| Event Kind | Context | Template |
|------------|---------|----------|
| 1 (Note) | preview | `short-note-card.j2` |
| 1 (Note) | full | `note.html.j2` |
| 6 (Repost) | any | `repost-card.j2` |
| 9735 (Zap) | any | `zap-card.j2` |
| 30023 (Article) | preview | `article-preview.j2` |
| 30023 (Article) | full | `article-full.j2` |

**Example Template:**

```jinja2
{# templates/short-note-card.j2 #}
<<<EVENT:{{ event.id }}>>>
{{ event.content | linkify | render_nprofiles }}

By: {{ deps.author.display_name or deps.author.name }}
{{ meta.formatted_time }}

Stats: {{ meta.formatted_stats }}
<<</EVENT>>>
```

**Output Formats:**
- **Plain Text**: Semantic markers (`<<<EVENT:id>>>`, `<<<PROFILE:npub>>>`)
- **HTML**: Full styling with CSS classes

### 5. HTTP API (JSON)

**Endpoints:**

```
GET  /api/feeds                     → List all feeds
GET  /api/feed/{name}?limit=50      → Feed events (enriched)
GET  /api/event/{id}?depth=5        → Single event (expanded)
GET  /api/thread/{id}                → Thread view (full conversation)
GET  /api/profile/{pubkey}           → Profile info + available feeds
GET  /api/profile/{pubkey}/feed/{name} → User's events in feed
POST /api/feed/{name}/refresh       → Trigger relay fetch
```

**Response Format:**

```json
{
  "feed": "timeline",
  "events": [
    {
      "event": {
        "id": "abc123...",
        "pubkey": "deadbeef...",
        "created_at": 1709876543,
        "kind": 1,
        "content": "Hello Nostr!",
        "tags": [["p", "..."]]
      },
      "deps": {
        "author": {
          "pubkey": "deadbeef...",
          "name": "alice",
          "display_name": "Alice Johnson",
          "picture": "https://..."
        },
        "reactions": {
          "count": 42,
          "by_content": {"❤️": 30, "🔥": 8}
        },
        "replies": {"count": 7},
        "zaps": {"count": 15, "total_sats": 21000}
      },
      "meta": {
        "formatted_time": "2h ago",
        "formatted_stats": "💬 7  ❤️ 42  ⚡ 21k"
      }
    }
  ],
  "count": 50,
  "limit": 50
}
```

## Template System

### Template Organization

```
templates/
├── layout.html.j2           # Base layout (HTML mode)
├── feed.html.j2             # Feed grid view
├── note.html.j2             # Full note view
├── short-note-card.j2       # Note preview (plain text)
├── article-preview.j2       # Article card
├── article-full.j2          # Full article
├── zap-card.j2              # Zap display
├── repost-card.j2           # Repost display
├── highlight-card.j2        # Highlight display
├── reaction-detail.j2       # Reaction expansion
├── reply-detail.j2          # Reply expansion
├── zap-detail.j2            # Zap expansion
├── profile-header.j2        # Profile card
└── thread-view.j2           # Thread conversation
```

### Custom Jinja2 Filters

**NIP-19 Encoding:**
- `to_npub(pubkey)` → `npub1...`
- `to_nevent(event_id, author)` → `nevent1...`
- `to_naddr(event)` → `naddr1...` (addressable events)
- `to_nostr_uri(identifier)` → `nostr:npub1...`

**Formatting:**
- `format_sats(value)` → `21k`, `1.00 BTC`
- `format_relative_time(timestamp)` → `2h ago`, `3 days ago`
- `truncate_content(text, length)` → `Hello wo...`
- `truncate_npub(pubkey)` → `npub1wmr3...g240`

**Content Processing:**
- `linkify(content, html=True)` → Convert URLs, hashtags to links
- `render_nprofiles(content)` → Resolve nprofile mentions to names

**Example Usage:**

```jinja2
Author: {{ event.pubkey | to_npub | to_nostr_uri }}
Zaps: {{ deps.zaps.total_sats | format_sats }}
Time: {{ event.created_at | format_relative_time }}
Content: {{ event.content | linkify | render_nprofiles }}
```

## Configuration (tendrl.toml)

**Feed Definitions:**

```toml
[feed.timeline]
name = "Timeline"
description = "Notes from people you follow"
display_kinds = [1]  # Kind 1: Notes
filter_by_follows = true

[feed.timeline.root]
kind = 1
template = "short-note-card.j2"

[feed.timeline.root.filter]
no_e_tags = true  # Top-level posts only

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
recursive = true

[feed.timeline.root.deps.zaps]
kind = 9735
relation = "p_tag"
mode = "aggregate"
stats = ["count", "total_sats"]
expandable = true
```

**Enrichment Modes:**
- `aggregate` - Just stats (count, total)
- `expanded` - Full event details
- `recursive` - Nested expansion (threads)

## Performance Characteristics

### Path 1: Rust (tendrl_ws)

| Operation | Latency |
|-----------|---------|
| LMDB query | < 1ms |
| Template render | 2-5ms |
| Full page | 5-15ms |

**Memory**: ~50-100MB

### Path 2: Python (tendrl_enricher)

| Operation | Latency |
|-----------|---------|
| SQLite query | 0.5-2ms |
| Enrichment (simple) | 5-20ms |
| Enrichment (deep tree) | 50-200ms |
| JSON serialization | 10-50ms |

**Memory**: ~100-300MB (with caching)

## Database Schema

### nostrdb (LMDB)

**Managed by nostrdb library:**
- Indexes: `id`, `pubkey`, `kind`, `tags`
- Memory-mapped for speed
- Concurrent readers supported

### SQLite (Per-Feed)

```sql
CREATE TABLE events (
    id TEXT PRIMARY KEY,
    pubkey TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    kind INTEGER NOT NULL,
    content TEXT,
    tags TEXT,  -- JSON array
    sig TEXT
);

CREATE INDEX idx_kind ON events(kind);
CREATE INDEX idx_pubkey ON events(pubkey);
CREATE INDEX idx_created_at ON events(created_at DESC);
```

### SQLite (Profiles)

```sql
CREATE TABLE profiles (
    pubkey TEXT PRIMARY KEY,
    name TEXT,
    display_name TEXT,
    about TEXT,
    picture TEXT,
    banner TEXT,
    nip05 TEXT,
    nip05_verified INTEGER DEFAULT 0,
    lightning_address TEXT,
    website TEXT,
    content_json TEXT,  -- Full event JSON
    updated_at INTEGER
);

CREATE INDEX idx_name ON profiles(name);
CREATE INDEX idx_updated_at ON profiles(updated_at);
```

## Special Features

### Parasitic Theme 🦠

**Visual Effects** (seen in recent commits):
- Vine drawing animations
- Ooze dripping effects
- Glitch effects
- Infection state tracking
- Stochastic timing
- Dynamic color variation

**Files:**
- `static/js/parasitic-theme.js`
- `static/css/parasitic-theme.css`

**Purpose**: Unique visual identity for the feed viewer

### Semantic Markers

**For Editor Integration:**

```
<<<EVENT:abc123>>>
<<<PROFILE:nostr:npub1...>>>@alice<<</PROFILE>>> says:
<<<HASHTAG:nostr>>>#nostr<<</HASHTAG>>> is cool!
Check out <<<URL:https://nostr.com>>>https://nostr.com<<</URL>>>
<<</EVENT>>>
```

**Supported Markers:**
- `EVENT:id` - Event boundary
- `PROFILE:uri` - Profile mention
- `HASHTAG:tag` - Hashtag
- `URL:url` - Regular URL
- `IMAGE:url` - Image URL
- `NOSTR_URI:uri` - Nostr protocol URI
- `METADATA:json` - Structured data

**Use Cases:**
- Emacs org-mode integration
- Neovim syntax highlighting
- VSCode extension parsing
- Terminal rendering

## Comparison: Path 1 vs Path 2

| Feature | Path 1 (Rust) | Path 2 (Python) |
|---------|---------------|-----------------|
| **Speed** | ⚡⚡⚡ < 5ms | 🐌 20-200ms |
| **Enrichment** | ❌ Basic | ✅ Full tree traversal |
| **Stats** | ❌ None | ✅ Aggregate + breakdown |
| **Templates** | ✅ Tera (Jinja-like) | ✅ Jinja2 (native) |
| **Caching** | ❌ Minimal | ✅ Two-tier (memory + DB) |
| **API** | ❌ HTML only | ✅ JSON + HTML |
| **SPA Support** | ❌ No | ✅ Yes |
| **Thread Expansion** | ❌ No | ✅ Recursive |
| **Deployment** | ✅ Single binary | ⚙️ Python + deps |

**Recommendation:**
- **Use Path 1**: Production browsing, speed-critical apps
- **Use Path 2**: Rich feeds, analytics, mobile backends, SPAs

## Integration Patterns

### 1. Hybrid (Best of Both)

```
Browser → tendrl_ws (fast initial load)
       → server.py API (rich interactions)
```

### 2. Static Site Generation

```
tendrl_enricher → JSONL → Static generator → HTML files
```

### 3. Emacs Integration

```
tendrl_enricher → Semantic markers → org-mode buffer
```

### 4. Mobile App

```
Mobile → server.py JSON API → Render natively
```

## Future Enhancements

### Planned (from code comments)
- [ ] Thread view template
- [ ] Wiki page templates
- [ ] Channel message templates
- [ ] Contextual rendering (preview/full/focus)
- [ ] Markdown article rendering
- [ ] Org-mode article rendering

### Possible
- [ ] Real-time updates (WebSocket)
- [ ] Search/filtering
- [ ] Export formats (RSS, Atom)
- [ ] Analytics dashboard
- [ ] Moderation tools
- [ ] Custom feed algorithms

## Getting Started

### Path 1: Rust Web Server

```bash
# Build
cargo build --release -p tendrl_ws

# Run
./target/release/tendrl_ws --bind 0.0.0.0:3000

# Open browser
open http://localhost:3000/feed
```

### Path 2: Python Enricher + API

```bash
# Setup
cd tendrl_enricher
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run API server
python server.py --config config.toml --port 8080

# Test API
curl http://localhost:8080/api/feeds
curl http://localhost:8080/api/feed/timeline?limit=10
```

## Architecture Benefits

✅ **Separation of Concerns**: Rust for speed, Python for flexibility
✅ **Config-Driven**: No code changes for new feeds
✅ **Composable**: JSONL pipes to any tool
✅ **Scalable**: LMDB handles millions of events
✅ **Extensible**: Add templates, filters, enrichments
✅ **Editor-Agnostic**: Semantic markers work everywhere

## Summary

The `feature/nostr-feeds-integration` branch provides **two complementary rendering paths**:

1. **Fast Rust path** for low-latency browser rendering
2. **Rich Python path** for deep enrichment and API serving

Both paths share the same **nostrdb foundation** and **tendrl.toml configuration**, ensuring consistency while optimizing for different use cases.

**Key Innovation**: Config-driven feeds with pluggable rendering—users define feeds in TOML, system handles fetching/enrichment/rendering automatically.
