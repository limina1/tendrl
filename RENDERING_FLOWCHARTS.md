# Tendrl Rendering System - Visual Flowcharts

## 1. Complete System Flow

```
╔═══════════════════════════════════════════════════════════════════════════╗
║                          NOSTR NETWORK (Relays)                            ║
║  wss://relay.damus.io │ wss://nos.lol │ wss://relay.nostr.band            ║
╚═══════════════════════════════════════════════════════════════════════════╝
                                    ║
                          WebSocket REQ/EVENT protocol
                                    ║
                                    ▼
        ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
        ┃      tendrl_daemon (Rust Background)       ┃
        ┃  ┌─────────────────────────────────────┐  ┃
        ┃  │  enostr::RelayPool                  │  ┃ ← WebSocket client
        ┃  │  - Subscribe to feeds (from config) │  ┃
        ┃  │  - Parse Nostr events               │  ┃
        ┃  └─────────────┬───────────────────────┘  ┃
        ┃                │                           ┃
        ┃                ▼                           ┃
        ┃  ┌─────────────────────────────────────┐  ┃
        ┃  │  nostrdb::process_event()           │  ┃ ← Event storage
        ┃  │  - Validate signature               │  ┃
        ┃  │  - Index by id, pubkey, kind, tags  │  ┃
        ┃  │  - Write to LMDB                    │  ┃
        ┃  └─────────────────────────────────────┘  ┃
        ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
                                    ║
                                    ▼
        ╔═══════════════════════════════════════════╗
        ║        nostrdb (LMDB Database)            ║
        ║  ~/.local/share/tendrl/nostrdb/           ║
        ║  - Memory-mapped for speed                ║
        ║  - Concurrent reads supported             ║
        ║  - ~1KB per event                         ║
        ╚═══════════════════════════════════════════╝
                    ║              ║
          ┌─────────┴────────┐     └─────────────┐
          │                  │                   │
          ▼                  ▼                   ▼
   ╔══════════════╗  ╔═══════════════╗  ╔═══════════════╗
   ║  PATH 1:     ║  ║  PATH 2:      ║  ║  PATH 3:      ║
   ║  tendrl_ws   ║  ║  tendrl_query ║  ║  tendrl_      ║
   ║  (Rust Web)  ║  ║  (CLI Tool)   ║  ║  enricher     ║
   ╚══════════════╝  ╚═══════════════╝  ╚═══════════════╝
```

## 2. Path 1: Rust Web Server (tendrl_ws)

```
┌─────────────────────────────────────────────────────────────────┐
│                      Browser Request                             │
│  GET http://localhost:3000/feed                                  │
│  GET http://localhost:3000/publication/naddr/<naddr>             │
└─────────────────────────────────────────────────────────────────┘
                             │
                             ▼
        ┌────────────────────────────────────────┐
        │   Axum Router (HTTP Server)            │
        │   - Route matching                     │
        │   - Extract params                     │
        └────────────────┬───────────────────────┘
                         │
                         ▼
        ┌────────────────────────────────────────┐
        │   Handler Function                     │
        │   async fn feed_handler(State)         │
        └────────────────┬───────────────────────┘
                         │
                         ▼
        ┌────────────────────────────────────────┐
        │   nostrdb Query (Direct LMDB)          │
        │   ndb.query(filters)                   │  ⚡ < 1ms
        │   - Kind filter                        │
        │   - Limit                              │
        └────────────────┬───────────────────────┘
                         │
                         ▼
        ┌────────────────────────────────────────┐
        │   Tera Template Rendering              │
        │   tera.render("feed.html", context)    │
        │   - Variable interpolation             │
        │   - Loops, conditionals                │
        └────────────────┬───────────────────────┘
                         │
                         ▼
        ┌────────────────────────────────────────┐
        │   HTML Response                        │
        │   <html>...</html>                     │
        │   Status: 200 OK                       │
        └────────────────────────────────────────┘
                         │
                         ▼
                   Browser renders
```

**Characteristics:**
- 🚀 Latency: 5-15ms total
- 🔒 Type-safe (Rust)
- 💾 Memory: ~50MB
- 🎯 Use case: Production browsing

## 3. Path 2: CLI Query Tool (tendrl_query)

```
┌─────────────────────────────────────────────────────────────────┐
│  Command Line                                                    │
│  $ tendrl_query --db ~/nostrdb --kinds 1,9735 --limit 100       │
└─────────────────────────────────────────────────────────────────┘
                             │
                             ▼
        ┌────────────────────────────────────────┐
        │   CLI Argument Parsing (clap)          │
        │   - Parse filters                      │
        │   - Validate args                      │
        └────────────────┬───────────────────────┘
                         │
                         ▼
        ┌────────────────────────────────────────┐
        │   nostrdb::Filter Builder              │
        │   Filter::new()                        │
        │     .kinds([1, 9735])                  │
        │     .limit(100)                        │
        └────────────────┬───────────────────────┘
                         │
                         ▼
        ┌────────────────────────────────────────┐
        │   LMDB Query Execution                 │
        │   ndb.query(&filters)                  │  ⚡ 0.5-2ms
        │   - Index scan                         │
        │   - Memory-mapped read                 │
        └────────────────┬───────────────────────┘
                         │
                         ▼
        ┌────────────────────────────────────────┐
        │   JSONL Output (stdout)                │
        │   {"id":"...","content":"..."}\n       │
        │   {"id":"...","content":"..."}\n       │
        │   {"id":"...","content":"..."}\n       │
        └────────────────┬───────────────────────┘
                         │
                         ▼
              ┌──────────┴──────────┐
              │                     │
              ▼                     ▼
        ┌──────────┐         ┌──────────┐
        │   jq     │         │ Python   │  ← External processors
        │   grep   │         │ enricher │
        │   awk    │         │ script   │
        └──────────┘         └──────────┘
```

**Unix Philosophy:**
- 📤 JSONL output (one event per line)
- 🔗 Pipeable to any tool
- 🎯 Single responsibility
- ⚡ Fast queries

## 4. Path 3: Python Enricher (Full Flow)

```
┌─────────────────────────────────────────────────────────────────┐
│  Step 1: Export from nostrdb → SQLite                           │
└─────────────────────────────────────────────────────────────────┘
                             │
        ┌────────────────────▼───────────────────┐
        │   tendrl_query (LMDB → JSONL)          │
        │   $ tendrl_query --kinds 1 > events.jsonl
        └────────────────────┬───────────────────┘
                             │
                             ▼
        ┌────────────────────────────────────────┐
        │   SQLite Import Script                 │
        │   for event in jsonl:                  │
        │     feed_db.insert(event)              │
        └────────────────────┬───────────────────┘
                             │
                             ▼
        ╔════════════════════════════════════════╗
        ║   Per-Feed SQLite Databases            ║
        ║   - timeline.db (kind 1)               ║
        ║   - zaps.db (kind 9735)                ║
        ║   - highlights.db (kind 9802)          ║
        ║   + profiles.db (kind 0)               ║
        ╚════════════════════════════════════════╝
                             │
┌─────────────────────────────────────────────────────────────────┐
│  Step 2: Enrichment Pipeline                                    │
└─────────────────────────────────────────────────────────────────┘
                             │
                             ▼
        ┌────────────────────────────────────────┐
        │   DatabaseManager                      │
        │   - Open feed_db (timeline.db)         │
        │   - Open profiles_db                   │
        └────────────────┬───────────────────────┘
                         │
                         ▼
        ┌────────────────────────────────────────┐
        │   ProfileCache (Two-Tier)              │
        │   ┌──────────────────────────────────┐ │
        │   │  LRU Memory Cache (256 entries)  │ │  ← Fast lookup
        │   └────────────┬─────────────────────┘ │
        │                │ cache miss            │
        │                ▼                       │
        │   ┌──────────────────────────────────┐ │
        │   │  SQLite Cache (profiles.db)      │ │  ← Persistent
        │   └──────────────────────────────────┘ │
        └────────────────┬───────────────────────┘
                         │
                         ▼
        ┌────────────────────────────────────────┐
        │   Enricher.enrich_event()              │
        │                                        │
        │   ┌──────────────────────────────────┐ │
        │   │ 1. Get base event from feed_db   │ │
        │   └────────────┬─────────────────────┘ │
        │                │                       │
        │   ┌────────────▼─────────────────────┐ │
        │   │ 2. Traverse deps (from config):  │ │
        │   │    - author (kind 0)             │ │
        │   │    - reactions (kind 7)          │ │
        │   │    - replies (kind 1 + e-tag)    │ │
        │   │    - zaps (kind 9735 + e-tag)    │ │
        │   └────────────┬─────────────────────┘ │
        │                │                       │
        │   ┌────────────▼─────────────────────┐ │
        │   │ 3. Compute stats:                │ │
        │   │    - Reaction count by emoji     │ │
        │   │    - Total zap sats              │ │
        │   │    - Reply count                 │ │
        │   └────────────┬─────────────────────┘ │
        │                │                       │
        │   ┌────────────▼─────────────────────┐ │
        │   │ 4. Format metadata:              │ │
        │   │    - "2h ago"                    │ │
        │   │    - "💬 7  ❤️ 42  ⚡ 21k"       │ │
        │   └──────────────────────────────────┘ │
        └────────────────┬───────────────────────┘
                         │
                         ▼
        ┌────────────────────────────────────────┐
        │   Enriched Event Structure             │
        │   {                                    │
        │     "event": {...},                    │
        │     "deps": {                          │
        │       "author": {...},                 │
        │       "reactions": {                   │
        │         "count": 42,                   │
        │         "by_content": {"❤️": 30}       │
        │       },                               │
        │       "zaps": {                        │
        │         "count": 15,                   │
        │         "total_sats": 21000            │
        │       }                                │
        │     },                                 │
        │     "meta": {                          │
        │       "formatted_time": "2h ago",      │
        │       "formatted_stats": "💬 7 ❤️ 42"  │
        │     }                                  │
        │   }                                    │
        └────────────────┬───────────────────────┘
                         │
┌─────────────────────────────────────────────────────────────────┐
│  Step 3: Rendering (Dual Mode)                                  │
└─────────────────────────────────────────────────────────────────┘
                         │
          ┌──────────────┴──────────────┐
          │                             │
          ▼                             ▼
┌─────────────────────┐      ┌─────────────────────┐
│  Plain Text Mode    │      │   HTML Mode         │
│  (Semantic Markers) │      │   (Browser Ready)   │
└─────────────────────┘      └─────────────────────┘
          │                             │
          ▼                             ▼
┌─────────────────────┐      ┌─────────────────────┐
│  Jinja2 Template    │      │  Jinja2 Template    │
│  short-note-card.j2 │      │  html/feed.j2       │
└─────────────────────┘      └─────────────────────┘
          │                             │
          ▼                             ▼
┌─────────────────────────────────────────────────┐
│  Custom Filters Applied:                        │
│  - linkify → <<<URL:...>>>url<<</URL>>>         │
│  - to_npub → npub1...                           │
│  - format_sats → 21k                            │
│  - format_relative_time → 2h ago                │
└──────────────┬──────────────────────────────────┘
               │
               ▼
     ┌─────────┴─────────┐
     │                   │
     ▼                   ▼
┌──────────┐      ┌──────────────┐
│  stdout  │      │  HTTP Server │
│  JSONL   │      │  (server.py) │
└────┬─────┘      └──────┬───────┘
     │                   │
     ▼                   ▼
┌──────────┐      ┌──────────────┐
│  Emacs   │      │   Browser    │
│  org-mode│      │   SPA        │
└──────────┘      └──────────────┘
```

## 5. HTTP API Server Flow (server.py)

```
┌─────────────────────────────────────────────────────────────────┐
│  Browser/SPA Request                                             │
│  GET /api/feed/timeline?limit=50                                 │
└─────────────────────────────────────────────────────────────────┘
                             │
                             ▼
        ┌────────────────────────────────────────┐
        │   NostrFeedsHandler                    │
        │   - Parse URL                          │
        │   - Extract query params               │
        └────────────────┬───────────────────────┘
                         │
                         ▼
        ┌────────────────────────────────────────┐
        │   NostrFeedsAPI.get_feed()             │
        │   - Load feed config from TOML         │
        │   - Get SQLite connections             │
        │   - Create enricher                    │
        └────────────────┬───────────────────────┘
                         │
                         ▼
        ┌────────────────────────────────────────┐
        │   Query SQLite (Feed Database)         │
        │   SELECT * FROM events                 │
        │   WHERE kind IN (1)                    │
        │   ORDER BY created_at DESC             │
        │   LIMIT 50                             │
        └────────────────┬───────────────────────┘
                         │
                         ▼
        ┌────────────────────────────────────────┐
        │   For Each Event:                      │
        │   ┌──────────────────────────────────┐ │
        │   │ enricher.enrich_event(event)     │ │
        │   │   - Get author profile           │ │
        │   │   - Aggregate reactions          │ │
        │   │   - Aggregate replies            │ │
        │   │   - Aggregate zaps               │ │
        │   │   - Format stats                 │ │
        │   └──────────────────────────────────┘ │
        └────────────────┬───────────────────────┘
                         │
                         ▼
        ┌────────────────────────────────────────┐
        │   Transform for API Response           │
        │   {                                    │
        │     "feed": "timeline",                │
        │     "events": [...],                   │
        │     "count": 50,                       │
        │     "limit": 50                        │
        │   }                                    │
        └────────────────┬───────────────────────┘
                         │
                         ▼
        ┌────────────────────────────────────────┐
        │   JSON Serialization                   │
        │   json.dumps(response, indent=2)       │
        └────────────────┬───────────────────────┘
                         │
                         ▼
        ┌────────────────────────────────────────┐
        │   HTTP Response                        │
        │   Status: 200 OK                       │
        │   Content-Type: application/json       │
        │   Access-Control-Allow-Origin: *       │
        └────────────────────────────────────────┘
                         │
                         ▼
                   Browser receives JSON
                   JavaScript renders UI
```

**API Endpoints:**

```
/api/feeds                      → List all configured feeds
/api/feed/{name}                → Get enriched feed events
/api/event/{id}                 → Get single event (deep enrichment)
/api/thread/{id}                → Get full thread (conversation tree)
/api/profile/{pubkey}           → Get profile + available feeds
/api/profile/{pubkey}/feed/{name} → Get user's events in feed
POST /api/feed/{name}/refresh   → Trigger manual relay fetch
```

## 6. Thread Expansion Flow

```
GET /api/thread/abc123
                             │
                             ▼
        ┌────────────────────────────────────────┐
        │   Find Event in Database               │
        │   event = find_event("abc123")         │
        └────────────────┬───────────────────────┘
                         │
                         ▼
        ┌────────────────────────────────────────┐
        │   Traverse to Root                     │
        │   while event.has_e_tags():            │
        │     parent_id = event.e_tags[0][1]     │
        │     event = get_event(parent_id)       │
        └────────────────┬───────────────────────┘
                         │
                         ▼
        ┌────────────────────────────────────────┐
        │   Enrich Root with Recursive Config    │
        │   {                                    │
        │     "deps": {                          │
        │       "author": {...},                 │
        │       "replies": {                     │
        │         "mode": "expanded",            │
        │         "recursive": true,             │
        │         "max_depth": 5,                │
        │         "deps": {                      │
        │           "author": {...},             │
        │           "replies": {                 │
        │             "mode": "expanded",        │
        │             "recursive": true,         │
        │             ...                        │
        │           }                            │
        │         }                              │
        │       }                                │
        │     }                                  │
        │   }                                    │
        └────────────────┬───────────────────────┘
                         │
                         ▼
        ┌────────────────────────────────────────┐
        │   Result: Full Thread Tree             │
        │   {                                    │
        │     "event": root_event,               │
        │     "deps": {                          │
        │       "author": {...},                 │
        │       "replies": [                     │
        │         {                              │
        │           "event": reply1,             │
        │           "deps": {                    │
        │             "author": {...},           │
        │             "replies": [               │
        │               {                        │
        │                 "event": reply1_1,     │
        │                 ...                    │
        │               }                        │
        │             ]                          │
        │           }                            │
        │         },                             │
        │         ...                            │
        │       ]                                │
        │     }                                  │
        │   }                                    │
        └────────────────────────────────────────┘
```

**Max Depth Limiting:**
- Prevents infinite recursion
- Configurable per request
- Default: 5 levels deep

## 7. Template Selection Logic

```
┌─────────────────────────────────────────────────────────────────┐
│  renderer.select_template(event, context='full')                │
└─────────────────────────────────────────────────────────────────┘
                             │
                             ▼
        ┌────────────────────────────────────────┐
        │   Check Config Override                │
        │   if config.template:                  │
        │     return config.template             │
        └────────────────┬───────────────────────┘
                         │ No override
                         ▼
        ┌────────────────────────────────────────┐
        │   Check Event Tags                     │
        │   for tag in event.tags:               │
        │     if tag[0] == 'content-type':       │
        │       if 'markdown': return markdown-  │
        │       if 'org': return org-article.j2  │
        └────────────────┬───────────────────────┘
                         │ No content-type
                         ▼
        ┌────────────────────────────────────────┐
        │   Check Event Kind + Context           │
        │                                        │
        │   kind 30023 + preview → article-preview.j2
        │   kind 30023 + full    → article-full.j2
        │   kind 30818 + preview → wiki-preview.j2
        │   kind 30818 + full    → wiki-page.j2
        │   kind 1               → short-note-card.j2
        │   kind 6               → repost-card.j2
        │   kind 7               → reaction-card.j2
        │   kind 9735            → zap-card.j2
        │   kind 9802            → highlight-card.j2
        │   default              → generic-event-card.j2
        └────────────────────────────────────────┘
```

**Contextual Rendering:**
- `preview` - Compact card for feed view
- `full` - Complete content view
- `focus` - Detailed analysis view

## 8. Performance Comparison

```
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃  Operation               │ Path 1    │ Path 2    │ Path 3     ┃
┃                          │ (Rust)    │ (CLI)     │ (Python)   ┃
┣━━━━━━━━━━━━━━━━━━━━━━━━━━╋━━━━━━━━━━━╋━━━━━━━━━━━╋━━━━━━━━━━━━┫
┃  Database Query          │  < 1ms    │  < 1ms    │  0.5-2ms   ┃
┃  Profile Lookup          │  N/A      │  N/A      │  < 1ms     ┃
┃  Stats Computation       │  N/A      │  N/A      │  5-20ms    ┃
┃  Template Render         │  2-5ms    │  N/A      │  10-30ms   ┃
┃  JSON Serialization      │  N/A      │  10-50ms  │  10-50ms   ┃
┣━━━━━━━━━━━━━━━━━━━━━━━━━━╋━━━━━━━━━━━╋━━━━━━━━━━━╋━━━━━━━━━━━━┫
┃  TOTAL (50 events)       │  5-15ms   │  10-50ms  │  100-500ms ┃
┃  TOTAL (Deep enrichment) │  N/A      │  N/A      │  500-2000ms┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━┻━━━━━━━━━━━┻━━━━━━━━━━━┻━━━━━━━━━━━━┛

Memory Usage:
  Path 1: ~50-100MB   (lightweight)
  Path 2: ~10-30MB    (minimal)
  Path 3: ~100-300MB  (caching)

Throughput:
  Path 1: ~1000 req/sec  (web server)
  Path 2: ~5000 events/sec (CLI processing)
  Path 3: ~50-200 req/sec (enrichment bottleneck)
```

## 9. Decision Tree: Which Path to Use?

```
                    Start: What's your use case?
                                │
                ┌───────────────┴───────────────┐
                │                               │
                ▼                               ▼
        Need rich stats/           Just viewing events
        enrichment?                in browser?
                │                               │
            YES │                           NO  │
                │                               │
                ▼                               ▼
        ┌───────────────┐            ┌──────────────────┐
        │  Path 3        │            │  Path 1          │
        │  (Python)      │            │  (Rust Web)      │
        │                │            │                  │
        │  ✓ Full stats  │            │  ✓ Fast         │
        │  ✓ Profiles    │            │  ✓ Simple       │
        │  ✓ Threads     │            │  ✓ Production   │
        │  ✓ API/SPA     │            │  ✓ Low memory   │
        └───────┬────────┘            └────────┬─────────┘
                │                              │
                ▼                              ▼
        Need JSON API?              Need HTML templating?
                │                              │
            YES │                          YES │
                │                              │
                ▼                              ▼
        Use server.py           Use Tera templates
        (HTTP API)              (axum routes)


        ┌───────────────────────────────────────┐
        │  Or: Just piping to another tool?     │
        │                                       │
        │  Use Path 2 (tendrl_query)            │
        │  $ tendrl_query | jq '.content'       │
        │  $ tendrl_query | python script.py    │
        └───────────────────────────────────────┘
```

## 10. Dependency Graph (Enrichment)

```
┌─────────────────────────────────────────────────────────────────┐
│  Event (Kind 1 - Note)                                           │
│  id: abc123                                                      │
│  pubkey: deadbeef                                                │
│  content: "Check out this article! nostr:nevent1..."            │
└─────────────────────────────────────────────────────────────────┘
                             │
                             │ Enrichment starts
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
        ▼                    ▼                    ▼
┌───────────────┐   ┌────────────────┐   ┌────────────────┐
│  deps.author  │   │ deps.reactions │   │  deps.zaps     │
│  (kind 0)     │   │ (kind 7)       │   │  (kind 9735)   │
│               │   │                │   │                │
│ Query:        │   │ Query:         │   │ Query:         │
│  pubkey =     │   │  SELECT WHERE  │   │  SELECT WHERE  │
│  deadbeef     │   │  e_tag = abc123│   │  e_tag = abc123│
│               │   │                │   │                │
│ Result:       │   │ Mode:          │   │ Mode:          │
│  {            │   │  "aggregate"   │   │  "aggregate"   │
│   name: "bob",│   │                │   │                │
│   picture: ..│   │ Stats:         │   │ Stats:         │
│  }            │   │  count: 42     │   │  count: 15     │
│               │   │  by_content: { │   │  total_sats:   │
│               │   │   "❤️": 30,    │   │    21000       │
│               │   │   "🔥": 8      │   │                │
│               │   │  }             │   │                │
└───────────────┘   └────────────────┘   └────────────────┘
        │                    │                    │
        └────────────────────┼────────────────────┘
                             │
                             ▼
        ┌────────────────────────────────────────┐
        │  deps.replies (kind 1 + e_tag)         │
        │                                        │
        │  Mode: "expanded" (get full events)    │
        │  Recursive: true                       │
        │  Max depth: 5                          │
        │                                        │
        │  Query:                                │
        │    SELECT * WHERE                      │
        │      kind = 1 AND                      │
        │      e_tag = abc123                    │
        │                                        │
        │  For each reply:                       │
        │    ┌─────────────────────────────────┐ │
        │    │  Recursively enrich (depth + 1) │ │
        │    │  - Get author                   │ │
        │    │  - Get reactions                │ │
        │    │  - Get sub-replies              │ │
        │    └─────────────────────────────────┘ │
        └────────────────────────────────────────┘
                             │
                             ▼
        ┌────────────────────────────────────────┐
        │  deps.quoted_event                     │
        │  (parsed from content)                 │
        │                                        │
        │  Extract nostr:nevent1... from content │
        │  Decode nevent → event_id              │
        │  Query database for event              │
        │  Enrich quoted event (depth + 1)       │
        └────────────────────────────────────────┘
                             │
                             ▼
        ┌────────────────────────────────────────┐
        │  deps.mentioned_profiles               │
        │  (parsed from content)                 │
        │                                        │
        │  Extract nostr:nprofile1... from content│
        │  Decode nprofile → pubkey              │
        │  Get profile from cache                │
        │  If missing: fetch from relays         │
        └────────────────────────────────────────┘
                             │
                             ▼
        ┌────────────────────────────────────────┐
        │  meta (computed metadata)              │
        │                                        │
        │  - depth: 0                            │
        │  - formatted_time: "2h ago"            │
        │  - formatted_stats: "💬 7  ❤️ 42  ⚡ 21k"│
        └────────────────────────────────────────┘
```

**Result: Fully Enriched Event**

```json
{
  "event": {...},
  "deps": {
    "author": {...},
    "reactions": {"count": 42, "by_content": {"❤️": 30}},
    "replies": [
      {
        "event": {...},
        "deps": {
          "author": {...},
          "replies": [...]  // Recursive
        }
      }
    ],
    "zaps": {"count": 15, "total_sats": 21000},
    "quoted_event": {...},
    "mentioned_profiles": [...]
  },
  "meta": {...}
}
```

---

## Summary

The `feature/nostr-feeds-integration` branch provides **three complementary paths**:

1. **Path 1 (Rust Web)**: Fast browser rendering with Tera templates
2. **Path 2 (CLI Tool)**: JSONL output for Unix pipelines
3. **Path 3 (Python Enricher)**: Deep enrichment with stats, profiles, threads

All paths share:
- ✅ Same nostrdb foundation (LMDB)
- ✅ Same tendrl.toml configuration
- ✅ Same event data

Choose based on your needs:
- Speed → Path 1
- Piping → Path 2
- Richness → Path 3
