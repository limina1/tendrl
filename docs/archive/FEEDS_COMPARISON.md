# Tendrl vs flux/nostr-feeds: Feed Comparison

## Architecture Differences

| Component | flux/nostr-feeds | Tendrl |
|-----------|-----------------|--------|
| **Fetching** | `nak` CLI tool | notedeck/tendrl (Rust) |
| **Storage** | SQLite (per-feed databases) | nostrdb (LMDB, single database) |
| **Templating** | Jinja2 | Jinja2 (same) |
| **Config** | TOML (enricher-centric) | TOML (filter-centric) |
| **Enrichment** | Python runtime enricher | Per-event enrichment in Rust + Python rendering |

## Feed Types Implemented

### ✅ Core Feeds (Both Systems)

1. **Timeline** - Follow-filtered notes and reposts
   - flux: `feed.timeline`
   - Tendrl: `feed.timeline` with `mode = "follows"`

2. **Replies** - Conversation view (notes with e-tags)
   - flux: `feed.replies`
   - Tendrl: `feed.replies` with `has_e_tags = true`

3. **Global** - All notes from relays (no follow filter)
   - flux: `feed.global`
   - Tendrl: `feed.global` with `mode = "global"`

4. **Zaps** - Global zap receipts
   - flux: `feed.zaps`
   - Tendrl: `feed.zaps`

5. **My Zaps** - Personal zaps (inbox mode)
   - flux: `feed.my_zaps` with `relay_mode = "inbox"`
   - Tendrl: `feed.my_zaps` with `relay_mode = "inbox"`

6. **Highlights** - NIP-84 highlights (kind 9802)
   - flux: `feed.highlights`
   - Tendrl: `feed.highlights`

7. **Articles** - Long-form content (kind 30023)
   - flux: Included in publications or separate feed
   - Tendrl: `feed.articles`

8. **Publications** - NIP-XX knowledge base (kind 30040)
   - flux: `feed.publications` with nested content
   - Tendrl: `feed.publications`

## Templates Available

### Copied from flux/nostr-feeds (19 templates total):

| Template | Purpose | Event Kinds |
|----------|---------|-------------|
| `short-note-card.j2` | Timeline notes | 1 |
| `zap-card.j2` | Zap receipts | 9735 |
| `zap-detail.j2` | Expanded zap view | 9735 |
| `highlight-card.j2` | Highlights | 9802 |
| `article-preview.j2` | Article summaries | 30023 |
| `article-full.j2` | Full article view | 30023 |
| `repost-card.j2` | Repost cards | 6 |
| `repost-detail.j2` | Expanded reposts | 6 |
| `reply-detail.j2` | Reply threads | 1 |
| `reaction-detail.j2` | Reaction lists | 7 |
| `profile-view.j2` | Profile pages | 0 |
| `profile-header.j2` | Profile header | 0 |
| `contact-list.j2` | Follow lists | 3 |
| `thread-view.j2` | Threaded conversations | 1 |
| `generic-event-card.j2` | Fallback for any kind | * |
| `feed.html.j2` | Feed layout (basic) | - |
| `feed_primal.html.j2` | Feed layout (Primal UI) | - |
| `layout.html.j2` | Page layout | - |
| `note.html.j2` | Note rendering | 1 |

## Config Structure Comparison

### flux/nostr-feeds Config Pattern:

```toml
[feed.timeline]
name = "timeline"
type = "follows"
fetch_strategy = "stream"
db_file = "timeline.db"
refresh_interval = 30
filter_by_follows = true
stream_kinds = [1, 6, 7, 9735]
display_kinds = [1, 6]

[feed.timeline.root]
kind = 1

[feed.timeline.root.deps.author]
kind = 0
relation = "author"
required = true

[feed.timeline.root.deps.reactions]
kind = 7
relation = "e_tag"
mode = "aggregate"
stats = ["count", "by_content"]
```

### Tendrl Config Pattern:

```toml
[feed.timeline]
name = "Timeline"
description = "Notes and reposts from your follows"
display_kinds = [1, 6]
mode = "follows"  # Global or follows
relay_mode = "general"
fetch_strategy = "stream"

[feed.timeline.pattern]
filter_type = "hybrid"

# Local queries (backfill from nostrdb)
[[feed.timeline.pattern.local_queries]]
kinds = [1, 6]
limit = 100

# Remote filters (streaming from relays)
[[feed.timeline.pattern.remote_filters]]
kinds = [1, 6, 7, 9735, 0]
limit = 250

[feed.timeline.root]
kind = 1
template = "short-note-card.j2"

[feed.timeline.root.deps.author]
kind = 0
relation = "author"
required = true

[feed.timeline.root.deps.reactions]
kind = 7
relation = "e_tag"
mode = "aggregate"
stats = ["count", "by_content"]
```

## Key Differences

### 1. Filter Architecture

**flux**: Single filter spec, `nak` handles fetching
- Direct relay queries via `nak req`
- Per-feed SQLite databases
- Periodic refreshes via scheduler

**Tendrl**: Hybrid local + remote filtering
- Local queries from nostrdb (instant backfill)
- Remote subscriptions to relays (streaming)
- Single unified LMDB database

### 2. Enrichment Strategy

**flux**: Runtime enrichment in Python
- Events fetched → stored in SQLite
- On query: load events → enrich with profiles/stats → render
- Two-tier caching (memory + SQLite)

**Tendrl**: Pre-enrichment in Rust + render-time Python
- Events fetched → enriched → stored in nostrdb
- On query: events already enriched → fast rendering
- LMDB provides sub-millisecond queries

### 3. Feed Switching

**flux**: Multiple SQLite databases
- `timeline.db`, `global.db`, `zaps.db`, etc.
- Each feed is independent
- Lower memory usage per feed

**Tendrl**: Single nostrdb database
- All events in one LMDB database
- Feeds are filtered views of same data
- Higher memory usage but faster queries

## Migration Path

To migrate a flux feed to Tendrl:

1. **Identify feed type** (follows vs global)
2. **Set mode** (`mode = "follows"` or `mode = "global"`)
3. **Define hybrid pattern**:
   - `local_queries` for backfill
   - `remote_filters` for streaming
4. **Copy dependency structure** (usually 1:1)
5. **Set template** to match flux template name

Example:

```toml
# flux
[feed.my_custom]
type = "follows"
fetch_strategy = "stream"

# Tendrl equivalent
[feed.my_custom]
mode = "follows"
fetch_strategy = "stream"
[feed.my_custom.pattern]
filter_type = "hybrid"
[[feed.my_custom.pattern.local_queries]]
kinds = [...]
[[feed.my_custom.pattern.remote_filters]]
kinds = [...]
```

## Performance Comparison

| Metric | flux | Tendrl |
|--------|------|--------|
| Query speed | Fast (SQLite indexed) | Very fast (LMDB memory-mapped) |
| Backfill speed | Moderate (nak fetch) | Instant (local queries) |
| Storage efficiency | High (per-feed DBs) | Moderate (single DB) |
| Memory usage | Low | Higher (LMDB mapped) |
| Concurrent feeds | High | High |
| Profile fetching | Batched (kind 0) | Integrated (nostrdb) |

## Next Steps

1. ✅ Config created: `tendrl_comprehensive.toml`
2. ✅ Templates copied: 19 templates from flux
3. ⏳ Test each feed type with Tendrl daemon
4. ⏳ Verify templates work with Tendrl enrichment
5. ⏳ Add UI navigation for feed switching
6. ⏳ Implement feed-specific views
