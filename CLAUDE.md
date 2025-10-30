# Tendrl - AI Development Guide

## The Core Idea

**Tendrl is config-driven event fetching for Nostr.**

One file (`tendrl.toml`) defines everything. Users write feeds in declarative TOML. The system responds:

- **Fetcher** reads config → subscribes to relays → stores events
- **Database** organizes events according to feed patterns
- **Query tool** filters events based on feed specifications
- **Templates** (external) render events following config hints

**No code changes needed to add feeds.** Users edit TOML, restart daemon, done.

This is the Unix philosophy applied to Nostr: **configuration as interface**.

---

## The Problem Tendrl Solves

**Before Tendrl:**
- Want zap feed? Write fetching code.
- Want highlights feed? Write more fetching code.
- Want long-form articles? Write even more code.
- Each feed = custom implementation, hardcoded filters, UI changes.

**With Tendrl:**
- Want any feed? Write 10 lines of TOML.
- Daemon fetches it automatically.
- Query tool extracts it.
- Your templates render it.

**Result**: Users become developers. Define feeds declaratively, no programming required.

---

## Architecture: Config Flows Downward

```
tendrl.toml (SINGLE SOURCE OF TRUTH)
    ↓
    ├─→ tendrl_core reads config
    │   └─→ Validates structure
    │   └─→ Builds HybridFilters (local + remote)
    │
    ├─→ tendrl_daemon consumes filters
    │   └─→ Creates RelayPool subscriptions
    │   └─→ Stores events in nostrdb
    │   └─→ Organizes by feed pattern
    │
    ├─→ tendrl_query respects feed specs
    │   └─→ Queries nostrdb by kind
    │   └─→ Outputs JSONL matching config
    │
    └─→ External enrichers (optional)
        └─→ Read display_kinds from config
        └─→ Fetch deps according to root.deps.*
        └─→ Render using templates
```

**Key principle**: Code never hardcodes feeds. Code **reads config and responds**.

---

## The tendrl.toml Contract

### What Users Define

```toml
[feed.my_custom_feed]
name = "My Custom Feed"
description = "Whatever I want to track"
display_kinds = [1337, 9999]  # Custom event kinds

# HOW to fetch (hybrid = local DB + remote relays)
[feed.my_custom_feed.pattern]
filter_type = "hybrid"

# Query local nostrdb for existing events
[[feed.my_custom_feed.pattern.local_queries]]
kinds = [1337, 9999]
limit = 500

# Subscribe to relays for new events
[[feed.my_custom_feed.pattern.remote_filters]]
kinds = [1337, 9999]
limit = 250
```

### What the System Does

1. **tendrl_core** parses this into:
   - `FeedDefinition` struct
   - `FeedPattern` with local + remote filters
   - `HybridFilter` with nostrdb queries + relay subscriptions

2. **tendrl_daemon** executes:
   - Queries nostrdb: `SELECT * FROM events WHERE kind IN (1337, 9999) LIMIT 500`
   - Subscribes to relays: `{"kinds": [1337, 9999], "limit": 250}`
   - Stores incoming events automatically

3. **tendrl_query** allows:
   - `tendrl_query --kinds 1337,9999` → extracts these events as JSONL
   - No hardcoded kind lists, respects config

4. **Templates** (external) can:
   - Read `display_kinds = [1337, 9999]` from config
   - Choose appropriate template for each kind
   - Render appropriately

**Users never touch Rust code.** They edit TOML, system adapts.

---

## Component Responsibilities

### tendrl_core (Library)

**Role**: Configuration parser and filter builder.

**What it does**:
- Parse `tendrl.toml` into Rust structs
- Validate feed definitions
- Build `HybridFilter` from `FeedPattern`
- Convert `LocalQuery` → nostrdb `Filter`
- Convert `RemoteFilter` → enostr `Filter`

**What it doesn't do**:
- Fetch events (that's daemon's job)
- Store events (that's nostrdb's job)
- Render events (that's templates' job)
- UI anything (it's a library)

**Depends on**: `nostrdb`, `serde`, `toml`

---

### tendrl_daemon (Binary)

**Role**: Event fetcher and storage manager.

**What it does**:
1. Load `tendrl.toml` via `tendrl_core`
2. For each feed in config:
   - Build `HybridFilter` from pattern
   - Query nostrdb for backfill (local_queries)
   - Subscribe to relays (remote_filters)
3. Main loop:
   - Poll RelayPool for new events
   - Store in nostrdb via `ndb.process_event()`
   - Update timeline tracking
4. Runs forever (until Ctrl+C)

**What it doesn't do**:
- Query events (that's query tool's job)
- Render events (that's templates' job)
- Validate event content (nostrdb does that)
- UI anything (it's headless)

**Depends on**: `tendrl_core`, `nostrdb`, `enostr`, `tokio`

**Storage location**: `~/.local/share/tendrl/nostrdb/` (XDG-compliant)

---

### tendrl_query (Binary)

**Role**: JSONL extraction from nostrdb.

**What it does**:
- Accept CLI filters: `--kinds`, `--author`, `--since`, `--until`, `--limit`
- Query nostrdb LMDB directly
- Output JSONL to stdout (one event per line)
- Exit (not a daemon, just a query tool)

**What it doesn't do**:
- Fetch from relays (that's daemon's job)
- Enrich events (that's external tools' job)
- Parse JSONL (that's receiver's job)
- Store anything (read-only)

**Depends on**: `nostrdb`, `serde_json`, `clap`

**Usage pattern**:
```bash
# Query → pipe → process
tendrl_query --db ~/nostrdb --kinds 9735 | python enricher.py
tendrl_query --db ~/nostrdb --kinds 1 --limit 100 | jq -r '.content'
```

---

### External Enrichers (Not Our Code)

**Role**: Read JSONL, enrich, render.

**Examples**:
- **nostr-feeds** (Python): Jinja2 templates, profile enrichment, HTML output
- **jq** (CLI): JSON processing, filtering, analysis
- **Custom scripts**: Any language, any format, any purpose

**What they do**:
- Read JSONL from stdin
- Fetch additional data (profiles, reactions, etc.)
- Apply templates
- Output final format (HTML, text, JSON, etc.)

**What Tendrl provides them**:
- Raw event JSON (matching Nostr spec exactly)
- Fast queries (nostrdb is sub-millisecond)
- Reliable fetching (events are already local)

**Data flow**:
```bash
tendrl_query → JSONL → enricher → output
```

---

## How Feeds Work: The Hybrid Pattern

Tendrl uses **hybrid filtering**: split between local database queries and remote relay subscriptions.

### Why Split?

**Local queries** (nostrdb):
- Fast backfill (existing events)
- Optimized for LMDB structure
- No network I/O

**Remote filters** (relays):
- Streaming new events
- Standard Nostr subscriptions
- Real-time updates

### Example: Zaps Feed

```toml
[feed.zaps]
name = "Zaps"
display_kinds = [9735]

[feed.zaps.pattern]
filter_type = "hybrid"

# Backfill: Query 500 most recent zaps from local DB
[[feed.zaps.pattern.local_queries]]
kinds = [9735]
limit = 500

# Stream: Subscribe to zaps on relays (250 per relay)
[[feed.zaps.pattern.remote_filters]]
kinds = [9735]
limit = 250
```

**What happens**:

1. **Daemon startup**:
   - Queries nostrdb: "Give me 500 most recent kind-9735 events"
   - Results appear instantly (LMDB query ~1ms)
   - User sees zaps immediately

2. **Relay subscription**:
   - Sends REQ to all relays: `{"kinds": [9735], "limit": 250}`
   - New zaps stream in continuously
   - Stored in nostrdb automatically

3. **Query anytime**:
   - `tendrl_query --kinds 9735` → all zaps (backfilled + streaming)
   - No network delay, reads from local LMDB

**Benefits**:
- Instant backfill (no waiting for relay responses)
- Continuous updates (streaming keeps DB fresh)
- Fast queries (always local, never blocking)

---

## Development Guidelines for AI Assistants

### When Adding Features

**DON'T**:
- ❌ Hardcode event kinds or feeds
- ❌ Add feed-specific logic to core code
- ❌ Create feed-specific structs or enums
- ❌ Bypass tendrl.toml for "quick hacks"
- ❌ Add UI frameworks or rendering logic

**DO**:
- ✅ Make code respond to config
- ✅ Add fields to tendrl.toml spec
- ✅ Keep daemon generic (reads any feed)
- ✅ Let external tools handle rendering
- ✅ Preserve Unix composability

### Example: Adding a New Feed Type

**Wrong approach** (hardcoded):
```rust
// ❌ DON'T DO THIS
enum FeedType {
    Timeline,
    Notifications,
    Zaps,  // hardcoded!
}

match feed_type {
    FeedType::Zaps => fetch_zaps(), // feed-specific logic!
}
```

**Right approach** (config-driven):
```toml
# ✅ DO THIS - users define feeds in TOML
[feed.zaps]
display_kinds = [9735]

[feed.zaps.pattern]
local_queries = [{ kinds = [9735], limit = 500 }]
remote_filters = [{ kinds = [9735], limit = 250 }]
```

```rust
// ✅ Code is generic, responds to any config
for (feed_id, feed_def) in config.feeds {
    let filter = build_hybrid_filter(&feed_def.pattern);
    subscribe_to_feed(filter);  // works for ANY feed
}
```

### Testing Changes

1. **Modify tendrl.toml** - Add/change feed definitions
2. **Restart daemon** - Load new config
3. **Query results** - Verify events are fetched
4. **Check logs** - Debug with `RUST_LOG=debug`

**No code changes needed** for new feeds. That's the point.

---

## Common Patterns

### Multiple Kinds in One Feed

```toml
[feed.engagement]
display_kinds = [1, 6, 7, 9735]  # notes, reposts, reactions, zaps

[[feed.engagement.pattern.local_queries]]
kinds = [1, 6, 7, 9735]
limit = 1000
```

**Daemon behavior**: Fetches all four kinds, stores together.

---

### Feed-Specific Relays

```toml
[feed.articles]
relay_mode = "custom"
relays = [
    "wss://relay.nostr.band",  # Good for long-form
    "wss://nos.lol"
]

[[feed.articles.pattern.remote_filters]]
kinds = [30023]
limit = 100
relays = ["wss://relay.nostr.band"]  # Only this relay for this filter
```

**Daemon behavior**: Connects only to specified relays for this feed.

---

### Time-Based Filtering

```toml
[[feed.recent.pattern.remote_filters]]
kinds = [1]
since = 1704067200  # Unix timestamp
limit = 500
```

**Daemon behavior**: Only fetches events after this timestamp.

---

## File Structure

```
tendrl/
├── Cargo.toml              # Workspace definition
├── CLAUDE.md               # This file (AI guide)
├── README.md               # User-facing overview
├── LICENSE                 # MIT OR Apache-2.0
│
├── crates/
│   ├── tendrl_core/        # Config parser + filter builder
│   │   ├── Cargo.toml
│   │   └── src/
│   │       ├── lib.rs      # Public exports
│   │       ├── config.rs   # TendrlConfig, FeedDefinition, etc.
│   │       ├── filter.rs   # HybridFilter, NdbQueryPackage, etc.
│   │       └── feed.rs     # build_hybrid_filter()
│   │
│   ├── tendrl_daemon/      # Event fetcher (binary)
│   │   ├── Cargo.toml
│   │   └── src/
│   │       └── main.rs     # Daemon event loop
│   │
│   └── tendrl_query/       # Query tool (binary)
│       ├── Cargo.toml
│       └── src/
│           └── main.rs     # JSONL extraction
│
├── docs/
│   ├── TENDRL_TOML.md      # Complete config reference
│   └── USAGE.md            # Usage guide with examples
│
└── examples/
    └── tendrl.toml.example # Sample config with 4+ feeds
```

---

## Dependencies Philosophy

**Keep it minimal:**
- `nostrdb` - Database (required, core functionality)
- `serde`/`toml` - Config parsing (required)
- `tokio` - Async runtime (daemon only)
- `clap` - CLI args (query tool only)

**Avoid:**
- UI frameworks (egui, iced, tauri, etc.)
- Web frameworks (axum, actix, rocket, etc.)
- Heavy serialization (protobuf, capnp, etc.)
- Anything not essential to config → fetch → query

**Why**: Tendrl is infrastructure. Heavy dependencies hurt compile times, complicate maintenance, and reduce flexibility.

---

## Integration with nostr-feeds

Tendrl is designed to feed **nostr-feeds** (Python enricher/renderer):

```bash
# Tendrl fetches → nostr-feeds enriches → templates render
tendrl_query --kinds 9735 | python -m enricher.main --stdin --render
```

**nostr-feeds responsibilities**:
- Profile enrichment (fetch kind-0 for authors)
- Stat computation (count reactions, sum zaps)
- Template rendering (Jinja2 → HTML/text)
- Relationship building (threads, quotes, etc.)

**Tendrl responsibilities**:
- Fetch events from relays
- Store in fast database
- Query efficiently
- Output JSONL

**Division of labor**: Tendrl does fetching/storage (Rust speed), nostr-feeds does enrichment/rendering (Python flexibility).

---

## Performance Expectations

**Daemon**:
- Connects to relays: ~100-500ms per relay
- Backfill 500 events: ~1-5ms (nostrdb query)
- Process incoming event: ~0.1-1ms (LMDB write)
- Memory usage: ~50-100MB typical

**Query tool**:
- Simple query (kind filter): ~0.5-2ms
- Complex query (kind + author + time): ~2-10ms
- Output 1000 events: ~10-50ms (JSON serialization)
- Memory usage: ~10-30MB

**Storage**:
- ~1KB per event average
- 100K events ≈ 100MB database
- LMDB memory-mapped (disk = RAM speed)

---

## Debugging

### Enable Detailed Logging

```bash
RUST_LOG=debug ./target/release/tendrl_daemon --config tendrl.toml
```

**What to look for**:
- `Loading tendrl.toml` - Config parsed successfully?
- `Building HybridFilter` - Filters created correctly?
- `Connected to relay` - Relay connections working?
- `Stored event: kind=X` - Events being saved?

### Check Database

```bash
# List database files
ls -lh ~/.local/share/tendrl/nostrdb/

# Check database size
du -sh ~/.local/share/tendrl/nostrdb/

# Query directly
./target/release/tendrl_query --db ~/.local/share/tendrl/nostrdb --kinds 1 | wc -l
```

### Validate Config

```bash
# Parse config without running daemon
cargo test -p tendrl_core
```

**Tests verify**:
- TOML syntax is valid
- Required fields are present
- Filter specs are correctly structured

---

## The Vision

**Tendrl makes Nostr feeds programmable.**

Not "program feeds in Rust", but **program feeds in TOML**. Users shouldn't need to understand async Rust, LMDB internals, or relay protocols. They should edit a text file and get a working feed.

**Analogy**:
- **Before Tendrl**: Building feeds is like writing SQL by hand. You need to understand databases, indexes, query optimization.
- **With Tendrl**: Building feeds is like using an ORM. Declare what you want, the system handles the rest.

**Goal**: Lower the barrier to custom Nostr feeds from "write Rust" to "edit TOML". Make feed creation accessible to **writers, designers, analysts** - anyone who can edit a config file.

**Principle**: **Configuration is code.** tendrl.toml is the program. The Rust binaries are just the interpreter.

---

## What Makes Tendrl Different

**vs. Full Nostr Clients (Damus, Amethyst, Snort)**:
- ✅ No UI (headless)
- ✅ No platform lock-in (JSONL output)
- ✅ Config-driven feeds (no code changes)
- ✅ Unix philosophy (pipes, not platforms)

**vs. nak CLI**:
- ✅ Persistent storage (LMDB, not streaming-only)
- ✅ Hybrid queries (local backfill + remote stream)
- ✅ Background daemon (continuous fetching)
- ✅ Sub-millisecond queries (memory-mapped DB)

**vs. Relay Implementations (strfry, nostr-rs-relay)**:
- ✅ Client-side filtering (no relay needed)
- ✅ Custom feed logic (not just REQ/EVENT)
- ✅ Local storage (offline queries)
- ✅ Notedeck patterns (battle-tested)

**Tendrl is infrastructure.** Like nginx for web servers or postgres for databases - you build on top of it, you don't use it directly.

---

## When to Use Tendrl

**Good fit**:
- Building custom Nostr feeds
- Offline-first applications
- Data analysis workflows
- Static site generators
- Personal archiving
- Research projects
- Custom clients with unique feed requirements

**Not a fit**:
- General-purpose Nostr client (use Damus, Amethyst, etc.)
- One-off event fetching (use nak)
- Running a public relay (use strfry)
- Real-time streaming only (use nak --stream)

**Sweet spot**: You need **custom feeds** + **fast local queries** + **program-agnostic output**.

---

## Contributing

When adding features to Tendrl, ask:

1. **Can this be configured?** (Add to tendrl.toml, not Rust code)
2. **Does this preserve composability?** (Can it pipe to other tools?)
3. **Is this minimal?** (Does it add essential functionality or bloat?)
4. **Does it respect the config contract?** (Code reads config, doesn't hardcode)

If yes to all four: proceed.
If no to any: reconsider the design.

**Remember**: Tendrl's power comes from **what it doesn't do**. No UI, no web server, no bundled renderer. Just config → fetch → query → JSONL.

Keep it simple. Keep it composable. Keep it config-driven.

---

## Resources

- **README.md**: User-facing overview and quick start
- **docs/TENDRL_TOML.md**: Complete config reference with examples
- **docs/USAGE.md**: Detailed usage guide and integration patterns
- **examples/tendrl.toml.example**: Real-world config with 4+ feeds

**External**:
- [nostrdb](https://github.com/damus-io/nostrdb): LMDB-based Nostr database
- [nostr-feeds](https://github.com/limina1/nostr-feeds): Python enricher/renderer
- [Notedeck](https://github.com/damus-io/notedeck): Source of RelayPool patterns
- [NIPs](https://github.com/nostr-protocol/nips): Nostr protocol specifications

---

**Tendrl: Config-driven Nostr feeds. Define in TOML, fetch with Rust, render with anything.**
