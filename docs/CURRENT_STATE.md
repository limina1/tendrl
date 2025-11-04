# Tendrl - Current Working State

**Last Updated**: October 31, 2025

## ✅ What Works Right Now

### Core Fetching & Storage
- ✅ **Follow List Loading**: Loads 533 follows from kind-3 events
- ✅ **Follow-Filtered Feeds**: Daemon injects authors based on `mode = "follows"`
- ✅ **Global Feeds**: Fetch all events with `mode = "global"`
- ✅ **Hybrid Filtering**: Local nostrdb queries + remote relay streaming
- ✅ **Real-Time Updates**: WebSocket subscriptions to relays
- ✅ **Instant Backfill**: Sub-millisecond queries from nostrdb (LMDB)
- ✅ **Profile Auto-Fetching**: Automatic missing profile detection and fetching

### Daemon Architecture
```
tendrl_daemon (Rust)
├── Loads tendrl.toml config
├── Loads follow list (kind-3)
├── Creates hybrid filters (local + remote)
├── Subscribes to relays (WebSocket)
├── Stores events in nostrdb (LMDB)
└── Runs continuously (streaming)

tendrl_query (Rust)
├── Queries nostrdb directly
├── Outputs JSONL (one event per line)
└── Exits after query (not a daemon)

tendrl_render_daemon.py (Python)
├── Starts tendrl_daemon (background)
├── Starts profile_daemon (background)
├── Fetches enriched events
├── Renders with Jinja2 templates
├── Generates HTML to public/
└── Auto-refreshes on new events
```

### Feed Types Implemented

| Feed | Status | Config | Events | Output |
|------|--------|--------|--------|--------|
| **notes_enriched** | ✅ Working | `tendrl_enrichment_test.toml` | 1803 notes | 185KB HTML |
| **timeline** | ✅ Working | `tendrl_comprehensive.toml` | Notes + reposts | 172KB HTML |
| **replies** | ✅ Working | `tendrl_comprehensive.toml` | Reply threads | 180KB HTML |
| **articles** | ✅ Working | `tendrl_comprehensive.toml` | Long-form | 102KB HTML |
| **highlights** | ✅ Working | `tendrl_comprehensive.toml` | NIP-84 | 17KB HTML |
| **global** | 📝 Config ready | `tendrl_comprehensive.toml` | All notes | Not tested |
| **zaps** | 📝 Config ready | `tendrl_comprehensive.toml` | Zap receipts | Not tested |
| **my_zaps** | 📝 Config ready | `tendrl_comprehensive.toml` | Personal zaps | Not tested |
| **publications** | 📝 Config ready | `tendrl_comprehensive.toml` | Kind 30040 | Not tested |

### UI & Rendering
- ✅ **HTTP Server**: Running on http://localhost:8000
- ✅ **Primal UI Design**: Sidebar navigation, responsive layout
- ✅ **19 Jinja2 Templates**: Copied from flux/nostr-feeds
- ✅ **Static Assets**: CSS/JS for Primal-style UI
- ✅ **Multiple Pages**: Timeline, replies, articles, highlights
- ✅ **Auto-Refresh**: Python daemon regenerates HTML on new events

### Templates Available

```
Core Templates:
├── short-note-card.j2       (Timeline notes)
├── note.html.j2             (Single note view)
├── thread-view.j2           (Threaded conversations)
├── reply-detail.j2          (Reply expansion)
└── feed_primal.html.j2      (Feed layout)

Engagement Templates:
├── zap-card.j2              (Zap receipts)
├── zap-detail.j2            (Expanded zaps)
├── reaction-detail.j2       (Reaction lists)
├── repost-card.j2           (Repost cards)
└── repost-detail.j2         (Expanded reposts)

Content Templates:
├── article-preview.j2       (Article cards)
├── article-full.j2          (Full articles)
├── highlight-card.j2        (Highlights)
└── generic-event-card.j2    (Fallback)

Profile Templates:
├── profile-view.j2          (Profile pages)
├── profile-header.j2        (Profile headers)
└── contact-list.j2          (Follow lists)

Layout Templates:
├── layout.html.j2           (Base layout)
└── feed.html.j2             (Feed wrapper)
```

## 🔧 Technical Details

### Storage
- **Database**: nostrdb (LMDB) at `~/.local/share/tendrl/nostrdb`
- **Size**: ~100MB for 100K events (1KB average)
- **Query Speed**: 0.5-2ms simple queries, 2-10ms complex
- **Memory Usage**: 50-100MB daemon, memory-mapped DB

### Performance Metrics
```
Daemon Startup:
├── Load config: <1ms
├── Load 533 follows: ~2ms
├── Connect to 4 relays: ~400ms
├── Backfill 1803 notes: ~60ms
└── Total: ~500ms

Event Processing:
├── Receive event: <1ms
├── Store in nostrdb: ~0.1-1ms
├── Update timeline: ~0.5ms
└── Total: ~2ms per event

Query Performance:
├── Simple filter (kind): 0.5-2ms
├── Complex (kind+author+time): 2-10ms
├── Output 1000 events: 10-50ms
└── Memory: 10-30MB
```

### Configuration Files

#### Primary Configs:
- **`tendrl_enrichment_test.toml`**: Single enriched notes feed
  - 533-author follow filter
  - Reactions, zaps, reposts aggregation
  - Currently running at 13:29

- **`tendrl_comprehensive.toml`**: 9 feed types
  - Timeline, replies, global, zaps, highlights, articles, publications
  - Full flux/nostr-feeds pattern implementation
  - Ready for testing (not yet running)

### Current Limitations

1. **Single User**: Only supports one npub per daemon instance
2. **No Feed Switching UI**: Must manually navigate to different HTML files
3. **No Real-Time Browser Updates**: HTML files regenerate, but browser doesn't auto-reload
4. **Template Compatibility**: Some flux templates may need adjustment for Tendrl's enrichment format
5. **No Pagination**: All events loaded at once (limited by config `limit`)
6. **No Search**: No full-text search within feeds
7. **No DMs**: Direct messages (kind 4) not yet implemented
8. **No Notifications**: No notification feed or alerts

## 📁 File Structure

```
tendrl/
├── Cargo.toml                          # Workspace definition
├── crates/
│   ├── tendrl_core/                    # Core library
│   │   ├── src/
│   │   │   ├── config.rs               # TendrlConfig, FeedDefinition
│   │   │   ├── follows.rs              # Follow list loading (NEW)
│   │   │   ├── feed.rs                 # Filter building
│   │   │   ├── filter_state.rs         # FilterState, FilterStates
│   │   │   ├── note_cache.rs           # NoteRef, CachedNote
│   │   │   ├── unknown_ids.rs          # UnknownIds tracking
│   │   │   ├── timeline.rs             # Timeline management
│   │   │   └── enrichment.rs           # Event enrichment
│   │   └── Cargo.toml
│   ├── tendrl_daemon/                  # Event fetcher (binary)
│   │   ├── src/main.rs                 # Daemon with follow injection
│   │   └── Cargo.toml
│   └── tendrl_query/                   # Query tool (binary)
│       ├── src/main.rs                 # JSONL extraction
│       └── Cargo.toml
├── tendrl_render_daemon.py             # UI rendering daemon (16KB)
├── tendrl_render.py                    # Jinja2 renderer (11KB)
├── tendrl_profile_daemon.py            # Profile auto-fetcher (12KB)
├── fetch_visible_profiles.py           # Profile helper (5KB)
├── tendrl_follows.py                   # Follow list helper (8KB)
├── templates/                          # 19 Jinja2 templates
├── static/                             # CSS/JS assets
├── public/                             # Generated HTML
├── tendrl_enrichment_test.toml         # Current config (running)
├── tendrl_comprehensive.toml           # 9-feed config (ready)
├── CURRENT_STATE.md                    # This file
├── ROADMAP.md                          # Future plans
├── FLUX_TO_TENDRL_GUIDE.md            # Translation guide
└── FEEDS_COMPARISON.md                # Architecture comparison
```

## 🚀 Quick Start

### View Current Feeds
```bash
# HTTP server already running at:
http://localhost:8000/

# Available pages:
http://localhost:8000/timeline.html
http://localhost:8000/replies.html
http://localhost:8000/articles.html
http://localhost:8000/highlights.html
```

### Check Daemon Status
```bash
# Check running processes
ps aux | grep tendrl

# View daemon logs (if running in background)
tail -f /tmp/tendrl_daemon.log
```

### Query Events Directly
```bash
# Query notes from your follows
./target/release/tendrl_query --kinds 1 --limit 100

# Query zaps
./target/release/tendrl_query --kinds 9735 --limit 50

# Query with time filter
./target/release/tendrl_query --kinds 1 --since 1698768000 --limit 100
```

### Test New Feeds
```bash
# Stop current daemon
pkill -f tendrl_render_daemon

# Start with comprehensive config (9 feeds)
python3 tendrl_render_daemon.py --config tendrl_comprehensive.toml

# This will generate:
# - timeline.html (follow-filtered)
# - global.html (all notes)
# - zaps.html (zap receipts)
# - my_zaps.html (personal zaps)
# - highlights.html (NIP-84)
# - articles.html (long-form)
# - publications.html (knowledge base)
```

## 🎯 Key Achievements

1. **✅ Fixed Follow List Bug**: Kind-3 p-tags now properly extracted (533 follows)
2. **✅ Standalone Architecture**: No Notedeck dependencies, fully independent
3. **✅ Follow Filtering**: Dynamic author injection at daemon level
4. **✅ Multi-Feed System**: 9 different feed types configured
5. **✅ Template Library**: 19 flux/nostr-feeds templates integrated
6. **✅ Working UI**: HTTP server with Primal design
7. **✅ Real-Time Updates**: Streaming subscriptions + auto-refresh

## 📊 Comparison: flux vs Tendrl

| Feature | flux/nostr-feeds | Tendrl |
|---------|------------------|--------|
| Fetching | nak CLI | notedeck (Rust) |
| Storage | SQLite (per-feed) | nostrdb (LMDB) |
| Backfill | Relay fetch | Instant (local) |
| Templates | 33+ templates | 19 templates (copied from flux) |
| Enrichment | Python runtime | Rust pre-enrichment + Python render |
| Follow Filter | SQL WHERE | Daemon injection |
| Real-Time | Polling (30s) | Streaming |
| Profile Fetch | Batched | Auto-integrated |

**See `FEEDS_COMPARISON.md` for detailed architectural comparison.**

## 🆘 Troubleshooting

### Server Not Showing
```bash
# Check if server is running
lsof -i :8000

# Start server manually
cd /home/user/Documents/Programming/tendrl-worspace/tendrl/public
python3 -m http.server 8000
```

### Daemon Not Fetching
```bash
# Check daemon process
ps aux | grep tendrl_daemon

# Restart daemon
pkill -f tendrl_daemon
./target/release/tendrl_daemon --config tendrl_enrichment_test.toml
```

### Empty Feeds
```bash
# Check database has events
du -sh ~/.local/share/tendrl/nostrdb

# Query directly
./target/release/tendrl_query --kinds 1 --limit 10
```

### Template Errors
```bash
# Check template directory
ls -la templates/

# Verify Jinja2 installed
python3 -c "import jinja2; print('OK')"
```

## 📝 Next: See ROADMAP.md

For future development plans and flux feature integration roadmap, see [ROADMAP.md](./ROADMAP.md).

For translating flux patterns to Tendrl, see [FLUX_TO_TENDRL_GUIDE.md](./FLUX_TO_TENDRL_GUIDE.md).
