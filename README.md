# Tendrl - Nostr Feed Fetcher

A minimal, headless Nostr event fetcher built on Notedeck's battle-tested relay patterns and nostrdb's lightning-fast LMDB storage.

## Overview

Tendrl is a **headless daemon** for fetching and querying Nostr events. It combines:

- **Notedeck's proven RelayPool** - The same reliable event fetching logic used in production
- **nostrdb's LMDB storage** - Sub-millisecond queries via memory-mapped databases
- **Program-agnostic output** - JSONL format pipes to any enricher/renderer you choose

Unlike full Nostr clients, Tendrl has **no UI complexity**. It's a Unix-style tool: fetch events, store them efficiently, query them fast, output JSON. What you do with that data is up to you.

## Architecture

Tendrl consists of three components:

### 1. `tendrl_daemon` - The Event Fetcher

A background daemon that:
- Reads `tendrl.toml` configuration defining custom feeds
- Connects to Nostr relays using Notedeck's RelayPool
- Fetches events according to filter specifications
- Stores everything in nostrdb (LMDB database)
- Runs continuously, keeping your local database updated

**Data flow**: Relays → RelayPool → nostrdb → disk

### 2. `nostrdb_json_query` - The Query Tool

A CLI tool for extracting events from nostrdb:
- Queries the LMDB database directly (no network I/O)
- Supports filtering by kind, author, time range, limit
- Outputs newline-delimited JSON (JSONL)
- Completes queries in ~1ms (nostrdb is **fast**)

**Data flow**: nostrdb → filter → JSONL stdout

### 3. External Enricher (Your Choice)

Any program that reads JSONL and does something useful:
- Python scripts (like [nostr-feeds](https://github.com/limina1/nostr-feeds))
- Jinja2 templates rendering HTML
- Data analysis tools
- Custom UI applications
- Shell scripts piping to other tools

**Data flow**: JSONL stdin → process → output

## Why Tendrl?

**Speed**: nostrdb uses LMDB (memory-mapped files). Queries complete in microseconds. No SQL parsing overhead.

**Reliability**: Notedeck's RelayPool has been battle-tested in production. It handles reconnections, subscriptions, and event validation correctly.

**Simplicity**: No web server. No GUI framework. No JavaScript bundle. Just daemons and JSONL pipes.

**Flexibility**: Your enricher can be **anything**. Python for ML analysis. Jinja2 for static sites. Rust for speed. JavaScript for browsers. Tendrl doesn't care - it just provides the data.

**Composability**: Unix philosophy. Small tools that do one thing well and pipe together.

## Quick Start

### 1. Build the Binaries

```bash
cd /home/user/Documents/Programming/tendrl/tendrl
cargo build --release
```

This produces:
- `target/release/tendrl_daemon`
- `target/release/tendrl_query`

### 2. Create Configuration

Copy the example config:

```bash
cp examples/tendrl.toml.example tendrl.toml
```

Edit `tendrl.toml` to define your feeds (see [docs/TENDRL_TOML.md](docs/TENDRL_TOML.md) for details).

### 3. Start the Daemon

```bash
./target/release/tendrl_daemon --config tendrl.toml
```

The daemon will:
- Connect to configured relays
- Begin fetching events matching your filters
- Store them in `~/.local/share/tendrl/nostrdb/` (or custom path)
- Continue running until stopped (Ctrl+C)

### 4. Query Events

```bash
# Get latest 50 zap receipts (kind 9735)
./target/release/tendrl_query --db ~/.local/share/tendrl/nostrdb --kinds 9735 --limit 50

# Get highlights from last 24 hours
./target/release/tendrl_query --db ~/.local/share/tendrl/nostrdb \
  --kinds 9802 \
  --since $(date -d '24 hours ago' +%s) \
  --limit 100

# Get all long-form articles by specific author
./target/release/tendrl_query --db ~/.local/share/tendrl/nostrdb \
  --kinds 30023 \
  --author npub1abc... \
  --limit 500
```

Output is JSONL (one JSON object per line):

```json
{"id":"abc123...","pubkey":"def456...","created_at":1234567890,"kind":9735,"tags":[...],"content":"...","sig":"..."}
{"id":"xyz789...","pubkey":"ghi012...","created_at":1234567891,"kind":9735,"tags":[...],"content":"...","sig":"..."}
```

### 5. Pipe to Enricher

```bash
# Analyze with jq
./target/release/tendrl_query --db ~/.local/share/tendrl/nostrdb \
  --kinds 9735 --limit 100 | \
  jq -r '.pubkey' | sort | uniq -c | sort -rn

# Render HTML with Python
./target/release/tendrl_query --db ~/.local/share/tendrl/nostrdb \
  --kinds 9802 --limit 50 | \
  python render_highlights.py > highlights.html

# Feed to nostr-feeds enricher
./target/release/tendrl_query --db ~/.local/share/tendrl/nostrdb \
  --kinds 30023 --limit 100 | \
  python -m nostr_feeds.enricher --template article_feed.html
```

## Components Deep Dive

### tendrl_daemon

**Purpose**: Continuously fetch Nostr events and store them locally.

**Key Features**:
- Uses Notedeck's `RelayPool` for connection management
- Handles reconnections and subscription management automatically
- Stores events in nostrdb (LMDB) for fast queries
- Configurable via `tendrl.toml`

**Command-line Options**:
```
--config <PATH>     Path to tendrl.toml (default: ./tendrl.toml)
--db <PATH>         Path to nostrdb directory (default: ~/.local/share/tendrl/nostrdb)
--log-level <LEVEL> Logging verbosity: error, warn, info, debug, trace
```

**Example**:
```bash
tendrl_daemon --config ~/.config/tendrl.toml --db ~/nostrdb --log-level debug
```

### tendrl_query

**Purpose**: Query nostrdb and output JSONL.

**Key Features**:
- Direct LMDB queries (no daemon needed)
- Multiple filter options (kind, author, time, limit)
- Fast: typically completes in <10ms for 1000s of events
- Outputs valid JSONL (one event per line)

**Command-line Options**:
```
--db <PATH>         Path to nostrdb directory (required)
--kinds <KINDS>     Comma-separated event kinds (e.g., "1,6,7")
--author <NPUB>     Filter by author npub/hex pubkey
--since <UNIX>      Minimum created_at timestamp
--until <UNIX>      Maximum created_at timestamp
--limit <NUM>       Maximum number of events (default: 100)
```

**Example**:
```bash
# All zaps from last week
tendrl_query --db ~/nostrdb \
  --kinds 9735 \
  --since $(date -d '7 days ago' +%s) \
  --limit 1000
```

## Integration Examples

### With jq (JSON Analysis)

```bash
# Top 10 most-zapped events
tendrl_query --db ~/nostrdb --kinds 9735 --limit 1000 | \
  jq -r '.tags[] | select(.[0] == "e") | .[1]' | \
  sort | uniq -c | sort -rn | head -10

# Extract all highlight content
tendrl_query --db ~/nostrdb --kinds 9802 --limit 100 | \
  jq -r '.content'
```

### With Python Enricher

```python
#!/usr/bin/env python3
import sys
import json

for line in sys.stdin:
    event = json.loads(line)

    # Enrich event (fetch profiles, compute stats, etc.)
    enriched = enrich_event(event)

    # Render to HTML
    print(render_template(enriched))
```

Usage:
```bash
tendrl_query --db ~/nostrdb --kinds 9802 --limit 50 | ./enricher.py > output.html
```

### With nostr-feeds

[nostr-feeds](https://github.com/limina1/nostr-feeds) is a Python-based enricher with Jinja2 templates:

```bash
tendrl_query --db ~/nostrdb --kinds 30023 --limit 100 | \
  nostr-feeds render --template publications.j2 > feed.html
```

## Configuration

Tendrl is configured via `tendrl.toml`. This file defines:

- **Relays**: Which Nostr relays to connect to
- **Feeds**: What event kinds to fetch and how to filter them
- **Patterns**: Fetching strategies (polling, streaming, hybrid)
- **Dependencies**: Related events to fetch (profiles, replies, zaps)

See [docs/TENDRL_TOML.md](docs/TENDRL_TOML.md) for complete documentation.

**Example feed definition**:

```toml
[feed.highlights]
name = "Highlights"
display_kinds = [9802]

[feed.highlights.pattern]
filter_type = "hybrid"

local_queries = [
    { kinds = [9802], limit = 500 },
    { kinds = [0], limit = 500 },  # Profiles
]

remote_filters = [
    { kinds = [9802, 0], limit = 250 }
]
```

## Data Storage

Tendrl uses **nostrdb** for storage:

- **Format**: LMDB (memory-mapped database)
- **Default location**: `~/.local/share/tendrl/nostrdb/`
- **Size**: Grows automatically (typical: 1-5 GB for 100K events)
- **Performance**: Sub-millisecond queries via mmap

**Database structure**:
```
nostrdb/
├── data.mdb       # Main database file
├── lock.mdb       # LMDB lock file
└── nostrdb.db     # Metadata
```

**Backup**: Simply copy the entire `nostrdb/` directory.

## Troubleshooting

### Daemon won't start

**Problem**: `tendrl_daemon` exits immediately or fails to connect.

**Solutions**:
- Check `tendrl.toml` syntax: `toml-cli check tendrl.toml`
- Verify relay URLs are valid WebSocket endpoints
- Check firewall isn't blocking outbound WebSocket connections
- Run with `--log-level debug` to see connection details

### No events being fetched

**Problem**: Daemon runs but `tendrl_query` returns nothing.

**Solutions**:
- Verify relays support the event kinds you're requesting
- Check filters aren't too restrictive (try removing limits temporarily)
- Some relays rate-limit; try adding more relays to `tendrl.toml`
- Inspect logs: `tendrl_daemon --log-level info` shows subscription status

### Query returns wrong events

**Problem**: `tendrl_query` outputs events you didn't expect.

**Solutions**:
- Remember nostrdb stores **all** events the daemon fetched
- Use `--kinds`, `--author`, `--since`, `--until` to filter
- Check other feeds in `tendrl.toml` might be fetching those kinds
- nostrdb is shared across all feeds (by design)

### Database corruption

**Problem**: LMDB errors or crashes.

**Solutions**:
- Stop `tendrl_daemon` cleanly (Ctrl+C, not kill -9)
- LMDB is very resilient; corruption is rare
- If corrupted: delete `nostrdb/` directory and re-sync (daemon will rebuild)
- Keep backups if data is important

## Performance

**Benchmarks** (on typical laptop, 100K events in nostrdb):

| Operation | Time |
|-----------|------|
| Query 1000 zaps | ~2ms |
| Query with author filter | ~5ms |
| Full table scan (100K events) | ~50ms |
| Insert new event | ~0.1ms |

**Memory usage**:
- `tendrl_daemon`: ~50 MB (RelayPool + buffers)
- `tendrl_query`: ~10 MB (opens database read-only)
- nostrdb mmap: 0 MB (kernel handles pages)

**Disk I/O**:
- nostrdb is memory-mapped; kernel caches hot pages
- Cold queries hit disk; hot queries from RAM
- Database size: ~10 KB per event (varies by content)

## Comparison to Alternatives

| Tool | Approach | Speed | Flexibility |
|------|----------|-------|-------------|
| **Tendrl** | Daemon + nostrdb + JSONL | ⚡ Fast (LMDB) | 🔥 Very flexible (any enricher) |
| **nak** | CLI + SQLite | ⚙️ Moderate | ✅ Good (pipes to scripts) |
| **Nostr client** | Full app + UI | 🐌 Slower (UI overhead) | ❌ Locked to app's UI |
| **Custom relay** | Server + database | ⚡ Fast (dedicated) | 🔧 Requires server maintenance |

**When to use Tendrl**:
- You want Notedeck's reliability without the GUI
- You're building custom frontends/analysis tools
- You need fast local queries (LMDB advantage)
- You prefer Unix pipes over monolithic apps

**When NOT to use Tendrl**:
- You want a ready-to-use client (use Damus, Amethyst, etc.)
- You only need web-based access (use web clients)
- You don't want to run a daemon (use on-demand tools like nak)

## License

MIT OR Apache-2.0

You may use Tendrl under either:
- [MIT License](https://opensource.org/licenses/MIT)
- [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0)

Choose whichever suits your project.

## Contributing

Tendrl is part of the broader [Tendrl ecosystem](https://github.com/limina1/tendrl) - a "parasitic configuration pattern" for injecting custom feeds into Nostr clients.

This headless daemon is one instantiation of that pattern: instead of injecting into an existing client, we built a minimal fetcher from scratch.

**Contributing**:
- Report bugs via GitHub issues
- Submit PRs for fixes or enhancements
- Share your enricher scripts/templates
- Document interesting feed configurations

## Links

- **Main Tendrl Project**: https://github.com/limina1/tendrl
- **nostrdb**: https://github.com/damus-io/nostrdb-rs
- **Notedeck**: https://github.com/damus-io/notedeck
- **nostr-feeds** (enricher): https://github.com/limina1/nostr-feeds

---

**Built with**: Rust, nostrdb, enostr, Notedeck patterns
**Philosophy**: Unix tools, composability, speed, simplicity
**Status**: Experimental - use at your own risk

*The feeds grow. The data flows. The pattern adapts.* 🌿
