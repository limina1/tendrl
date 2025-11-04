# Tendrl - Config-Driven Nostr Engine

**Tendrl is a modular Nostr event engine that separates fetching, storage, enrichment, and rendering into composable layers.**

Define feeds in 10 lines of TOML. Tendrl handles the rest.

---

## The Vision

**Before Tendrl**: Every feed type requires custom code. Want zaps? Write fetching logic. Want highlights? More code. Want custom filtering? Change the codebase.

**With Tendrl**: Write 10 lines of TOML. The system fetches, stores, enriches, and renders automatically. No code changes needed.

**Philosophy**: Configuration is code. `tendrl.toml` is the program. The Rust/Python stack is just the interpreter.

---

## Architecture

```
tendrl.toml (Configuration - Single Source of Truth)
    ↓
┌─────────────────────────────────────────┐
│   RUST LAYER (Fast Infrastructure)      │
│  ├─ tendrl_daemon: Event fetcher        │
│  ├─ tendrl_query: Database queries      │
│  ├─ tendrl_profile_fetcher: Profiles    │
│  └─ tendrl_ingest: Event ingestion      │
│                                          │
│  Storage: nostrdb (LMDB)                │
│  Speed: Sub-millisecond queries         │
└─────────────────────────────────────────┘
    ↓ (JSONL pipe)
┌─────────────────────────────────────────┐
│   PYTHON LAYER (Flexible Enrichment)    │
│  ├─ tendrl_enricher: Enrich events      │
│  │   • Profile resolution               │
│  │   • Stats aggregation                │
│  │   • Dependency tree traversal        │
│  │                                       │
│  └─ Integration Scripts:                │
│      • tendrl_render.py                 │
│      • tendrl_render_daemon.py          │
└─────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────┐
│   OUTPUT LAYER (Multiple Formats)       │
│  ├─ Static HTML (Jinja2 templates)     │
│  ├─ JSON API (for web apps)            │
│  ├─ Plain text (for terminals/editors) │
│  └─ Custom (your choice)                │
└─────────────────────────────────────────┘
```

**Key Principle**: Each layer does what it does best. Rust = speed. Python = flexibility. Config = control.

---

## What Makes Tendrl Different

### 🎯 Config-Driven
Define feeds in TOML, not code. Add a new feed type without touching Rust or Python.

```toml
[feed.my_custom_feed]
display_kinds = [1337, 9999]

[feed.my_custom_feed.pattern]
local_queries = [{ kinds = [1337, 9999], limit = 500 }]
remote_filters = [{ kinds = [1337, 9999], limit = 250 }]
```

That's it. Tendrl now fetches, stores, and enriches those event kinds.

### ⚡ Hybrid Filtering
Split queries between local database (instant backfill) and remote relays (live streaming).

- **Local**: Query nostrdb for 500 recent events (~1ms)
- **Remote**: Subscribe to relays for new events (streaming)
- **Result**: Instant startup + continuous updates

### 🔧 Composable
Every component is independent:
- Rust daemon fetches → nostrdb stores
- Python enricher reads → processes → outputs
- Templates render → any format you want

### 🚀 Fast
- **nostrdb**: LMDB (memory-mapped) = ~0.5-2ms queries
- **RelayPool**: Notedeck battle-tested patterns
- **Profile cache**: Two-tier (memory + SQLite) = 94%+ hit rate

### 📦 Complete Stack
Not just a fetcher. Complete pipeline from relay to rendered output:
- Event fetching (Rust daemon)
- Storage (nostrdb LMDB)
- Profile management (background daemon)
- Per-event enrichment (dependency trees)
- Multiple output formats (HTML, JSON, text)

---

## Quick Start

### 1. Build Tendrl

```bash
cargo build --release
```

Produces binaries in `target/release/`:
- `tendrl_daemon` - Event fetcher
- `tendrl_query` - Database query tool
- `tendrl_profile_fetcher` - Profile auto-fetcher
- `tendrl_ingest` - Event ingestion tool

### 2. Configure Your Feeds

```bash
cp examples/tendrl_enrichment_test.toml my_config.toml
```

Edit `my_config.toml` to set your npub/pubkey and customize feeds.

### 3. Start the System

**Option A: Automatic (Recommended)**

```bash
python3 scripts/tendrl_render_daemon.py --config my_config.toml --output-dir public/
```

This automatically:
- Starts `tendrl_daemon` (Rust event fetcher)
- Starts profile auto-fetcher
- Polls for new events
- Regenerates HTML when events arrive
- Serves everything in `public/` directory

Open `public/index.html` in your browser.

**Option B: Manual Control**

```bash
# Terminal 1: Start event fetcher
./target/release/tendrl_daemon --config my_config.toml

# Terminal 2: Start profile fetcher
./target/release/tendrl_profile_fetcher \
  --db-path ~/.local/share/tendrl/nostrdb \
  --poll-interval 120

# Terminal 3: Query and render
./target/release/tendrl_query \
  --feed notes_enriched \
  --config my_config.toml \
  --limit 100 > enriched.jsonl

# Terminal 4: Render HTML
python3 scripts/tendrl_render.py \
  --feed notes_enriched \
  --output public/index.html
```

---

## Components

### Rust Core (Fast Infrastructure)

#### `tendrl_daemon`
**Purpose**: Continuous event fetching from Nostr relays

**Features**:
- Connects to multiple relays via RelayPool
- Hybrid filtering (local backfill + remote streaming)
- Follow list injection (mode = "follows")
- Stores everything in nostrdb LMDB
- Battle-tested Notedeck patterns

**Usage**:
```bash
tendrl_daemon --config tendrl.toml
```

**Output**: Events stored in `~/.local/share/tendrl/nostrdb/`

---

#### `tendrl_query`
**Purpose**: Query nostrdb and output JSONL (with optional enrichment)

**Two Modes**:

1. **Stream Mode** (default):
   ```bash
   tendrl_query --db ~/.local/share/tendrl/nostrdb --kinds 9735 --limit 50
   ```
   Fast JSONL output. No enrichment. Pure speed.

2. **Enrichment Mode**:
   ```bash
   tendrl_query --feed notes_enriched --config tendrl.toml --limit 100
   ```
   Per-event enrichment with profiles, stats, and dependencies.

**Output Format** (enrichment mode):
```json
{
  "id": "abc123...",
  "kind": 1,
  "content": "Hello Nostr!",
  "_enriched": {
    "stats": {
      "reactions": {"count": 42, "by_content": {"❤️": 30, "🔥": 12}},
      "zaps": {"count": 5, "total_sats": 21000, "by_user": [...]}
    },
    "single": {
      "profiles": {"pubkey1": {...}, "pubkey2": {...}}
    }
  }
}
```

---

#### `tendrl_profile_fetcher`
**Purpose**: Background daemon that auto-fetches missing kind-0 profiles

**How It Works**:
1. Scans nostrdb for authors (from notes, zaps, etc.)
2. Identifies missing profiles (no kind-0 event)
3. Fetches from specialized profile indexer relays
4. Stores profiles back in nostrdb
5. Polls every N seconds (configurable)

**Usage**:
```bash
tendrl_profile_fetcher \
  --db-path ~/.local/share/tendrl/nostrdb \
  --relays wss://relay.nostr.band,wss://purplepag.es \
  --poll-interval 120 \
  --batch-size 20
```

**Why This Matters**: Events reference authors by pubkey. To show names/avatars, you need their profiles. This daemon ensures profiles are always available.

---

#### `tendrl_ingest`
**Purpose**: Ingest JSONL events from stdin into nostrdb

**Usage**:
```bash
cat external_events.jsonl | tendrl_ingest --db ~/.local/share/tendrl/nostrdb
```

**Use Case**: Integration with external event sources or bulk imports.

---

### Python Layer (Flexible Enrichment)

#### `tendrl_enricher/`
**Purpose**: Python module for enriching events with profiles, stats, and relationships

**Features**:
- Profile resolution (kind-0 events)
- Stats aggregation (reactions, zaps, reposts)
- Dependency tree traversal (quoted events, threads)
- Two-tier profile cache (memory + SQLite)
- Config-driven enrichment rules

**Capabilities**:
- `count` - Simple counts
- `by_content` - Group by content (e.g., reaction emojis)
- `total_sats` - Sum zap amounts
- `by_user` - Per-user breakdowns
- `users` - Extract user lists

---

#### Integration Scripts

Located in `scripts/`:

**`tendrl_render.py`** - Template-based rendering
- Jinja2 templates → HTML/text
- Custom filters (timestamp_to_relative, format_sats, linkify)
- Static file generation or Flask web server

**`tendrl_render_daemon.py`** - Supervised rendering daemon
- Starts `tendrl_daemon` (Rust)
- Starts `tendrl_profile_fetcher`
- Polls for new events
- Auto-regenerates HTML
- Production-ready deployment

**`tendrl_follows.py`** - Follow list integration
- Fetch kind-3 contact lists
- Filter feeds by follow list
- npub ↔ hex conversion

**`fetch_visible_profiles.py`** - Synchronous profile fetcher
- Parallel profile fetching (10 concurrent)
- Ensures profiles exist before rendering
- Avoids "Unknown Author" placeholders

---

### Templates & Static Assets

#### `templates/` - Jinja2 Templates
- `short-note-card.j2` - Kind-1 notes
- `zap-card.j2` - Kind-9735 zaps
- `highlight-card.j2` - Kind-9802 highlights
- `article-full.j2` - Kind-30023 long-form
- `profile-view.j2` - Profile pages
- `feed.html.j2` / `feed_primal.html.j2` - Feed layouts

**Custom Filters**:
- `timestamp_to_relative` - "3h ago"
- `format_sats` - "21,000 sats" → "21k"
- `linkify` - URLs → clickable links
- `nl2br` - Newlines → `<br>` tags

#### `static/` - CSS & JavaScript
- `static/css/feed.css` - Primal-inspired dark theme
- `static/css/primal.css` - Additional Primal UI styles
- `static/js/feed.js` - Client-side interactions

---

## Configuration

All feeds defined in `tendrl.toml`:

```toml
[user]
npub = "npub1..."
pubkey = "hex..."

[feed.notes_enriched]
name = "Timeline"
mode = "follows"  # Only show notes from follows
display_kinds = [1]

[feed.notes_enriched.pattern]
local_queries = [
  { kinds = [1], limit = 500 }  # Backfill 500 notes
]
remote_filters = [
  { kinds = [1], limit = 250 }  # Stream new notes
]

# Enrichment configuration
[feed.notes_enriched.root]
kind = 1

[feed.notes_enriched.root.deps.author]
kind = 0
relation = "author"
required = false

[feed.notes_enriched.root.deps.reactions]
kind = 7
relation = "e_tag"
mode = "aggregate"
stats = ["count", "by_content"]

[feed.notes_enriched.root.deps.zaps]
kind = 9735
relation = "e_tag"
mode = "aggregate"
stats = ["count", "total_sats", "by_user"]
```

**That's it.** No code changes. Tendrl reads this config and:
1. Fetches kind-1 notes from your follows
2. Backfills 500 from local DB
3. Streams 250 from relays
4. Enriches each note with author profile
5. Aggregates reactions and zaps
6. Outputs enriched JSONL

See `examples/` for more feed configurations.

---

## Feed Modes

### Global Mode
Fetch everything matching the filter (no author restrictions)

```toml
[feed.global_notes]
mode = "global"
```

### Follows Mode
Automatically inject your follow list as authors filter

```toml
[feed.my_timeline]
mode = "follows"  # Only shows events from people you follow
```

Tendrl:
1. Fetches your kind-3 contact list
2. Extracts followed pubkeys
3. Injects them into all filters for this feed

### Author Mode
Filter by specific author

```toml
[feed.alice_posts]
mode = "author"
author = "npub1alice..."
```

---

## Data Flow Examples

### Example 1: Zap Feed

**Config**:
```toml
[feed.zaps]
display_kinds = [9735]

[feed.zaps.root]
kind = 9735
deps.sender = { kind = 0, relation = "zap_sender" }
deps.recipient = { kind = 0, relation = "zap_recipient" }
```

**What Happens**:
1. `tendrl_daemon` fetches kind-9735 events from relays
2. Stores in nostrdb
3. `tendrl_query --feed zaps` enriches each zap with sender/recipient profiles
4. Extracts sats amount from bolt11 invoice
5. Outputs enriched JSONL
6. `tendrl_render.py` renders using `zap-card.j2` template
7. Result: Beautiful zap feed with avatars, names, and amounts

### Example 2: Highlights Feed

**Config**:
```toml
[feed.highlights]
display_kinds = [9802]

[feed.highlights.root]
kind = 9802
deps.author = { kind = 0, relation = "author" }
deps.highlighted_event = { relation = "highlight_target" }
```

**What Happens**:
1. Fetches kind-9802 (NIP-84 highlights)
2. Enriches with highlighter profile
3. Fetches original highlighted event/text
4. Renders with context and attribution
5. Shows "Who highlighted what"

---

## Performance

**Rust Layer**:
- **Query time**: 0.5-2ms (typical)
- **Event insertion**: 0.1-1ms
- **Memory usage**: 50-100MB (daemon)
- **Storage**: ~1KB per event

**Python Layer**:
- **Enrichment**: ~10-15ms per event (with profile cache warm)
- **Profile cache hit rate**: 94%+ (after initial load)
- **Rendering**: ~5ms per event (Jinja2)

**Overall**:
- 100 events enriched + rendered: ~1-2 seconds
- 1,000 events: ~10-15 seconds

**nostrdb is FAST**. Memory-mapped LMDB = disk is RAM.

---

## Comparison to Alternatives

| Tool | Approach | Strength |
|------|----------|----------|
| **Tendrl** | Config + Rust + Python | Config-driven, fast, flexible output |
| **nak** | CLI tool + SQLite | Simple, good for scripting |
| **nostr-feeds** | Python + SQLite | Rich enrichment, proven templates |
| **Full clients** | Monolithic apps | Ready-to-use UI, but not composable |
| **Custom relay** | Server-side | Centralized, requires maintenance |

**When to use Tendrl**:
- You want custom feeds without writing code
- You need fast local queries (LMDB)
- You want to pipe events to various outputs
- You prefer composable tools over monolithic apps

**When NOT to use Tendrl**:
- You want a ready-to-use client (use Damus, Amethyst, etc.)
- You only need one-off queries (use nak)
- You don't want to run a daemon

---

## Documentation

- **[docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)** - Detailed setup guide
- **[docs/ROADMAP.md](docs/ROADMAP.md)** - Project roadmap and status
- **[docs/CURRENT_STATE.md](docs/CURRENT_STATE.md)** - What works now
- **[docs/ENRICHMENT_DESIGN.md](docs/ENRICHMENT_DESIGN.md)** - Enrichment system details
- **[CLAUDE.md](CLAUDE.md)** - AI development guide (architecture deep dive)
- **[docs/archive/](docs/archive/)** - Historical docs and comparisons

---

## Contributing

Tendrl is part of a broader vision: **make Nostr feeds programmable**.

**Ways to contribute**:
- Report bugs via GitHub issues
- Share example feed configs
- Create new Jinja2 templates
- Write enrichment extensions
- Improve documentation

**Philosophy**: Keep it modular. Rust does infrastructure. Python does enrichment. Config does control. Each layer stays focused.

---

## Project Status

**Phase 1: Complete** ✅
- Follow-filtered feeds working
- 533 follows loaded, 1,803 notes fetched
- Per-event enrichment with stats
- Profile auto-fetching
- HTML rendering with Primal UI
- 9 feed types configured

**Phase 2: In Progress** 🚧
- Feed switcher UI
- Real-time updates (polling/SSE)
- Pagination & infinite scroll

**Phase 3: Planned** 📋
- Thread expansion
- Quoted event handling
- Advanced repost rendering

See [docs/ROADMAP.md](docs/ROADMAP.md) for details.

---

## License

MIT OR Apache-2.0 (your choice)

---

## Links

- **nostrdb**: https://github.com/damus-io/nostrdb
- **Notedeck**: https://github.com/damus-io/notedeck
- **Nostr Protocol**: https://github.com/nostr-protocol/nips

---

**Built with**: Rust, nostrdb, enostr, Python, Jinja2, Notedeck patterns
**Philosophy**: Configuration is code. Unix composability. Speed + flexibility.
**Status**: Phase 1 complete, actively developed

*Define feeds in TOML. Tendrl handles the rest.* 🌿
