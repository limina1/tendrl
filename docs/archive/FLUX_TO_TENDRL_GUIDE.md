# flux/nostr-feeds to Tendrl Translation Guide

**Purpose**: Convert flux (nostr-feeds) patterns, configs, and features to Tendrl

---

## Quick Reference Table

| flux Concept | Tendrl Equivalent | Notes |
|--------------|-------------------|-------|
| `nak req` | `tendrl_daemon` | Rust daemon vs CLI tool |
| SQLite per-feed | nostrdb (LMDB) unified | Single database |
| `feed_scheduler.py` | `tendrl_daemon` (built-in) | Continuous streaming |
| `enricher/main.py` | `tendrl_query` + `tendrl_render.py` | Query + render |
| `enricher/server.py` | HTTP server (Python) | Same pattern |
| `db_file = "timeline.db"` | All in nostrdb | No separate DBs |
| `refresh_interval = 30` | Real-time streaming | No polling needed |
| `filter_by_follows = true` | `mode = "follows"` | Daemon-level |
| `stream_kinds = [1,6,7]` | `remote_filters.kinds` | In pattern |
| `display_kinds = [1,6]` | `display_kinds` | Same |

---

## Architecture Comparison

### flux Architecture:
```
flux/
├── enricher/
│   ├── config.toml              # Feed definitions
│   ├── feed_scheduler.py        # Background fetcher (nak)
│   ├── main.py                  # CLI enricher
│   ├── server.py                # JSON API server
│   ├── db.py                    # SQLite operations
│   ├── enricher.py              # Event enrichment logic
│   └── nostr_utils.py           # Nostr helpers
├── templates/                   # 33+ Jinja2 templates
├── static/                      # CSS/JS for SPA
└── ~/.local/share/nostr-feeds/  # Per-feed SQLite DBs
    ├── timeline.db
    ├── global.db
    ├── zaps.db
    └── profiles.db (shared)
```

### Tendrl Architecture:
```
tendrl/
├── crates/
│   ├── tendrl_core/             # Config parsing, filter building
│   ├── tendrl_daemon/           # Event fetcher (Rust)
│   └── tendrl_query/            # JSONL extraction (Rust)
├── tendrl_render_daemon.py      # UI rendering daemon
├── tendrl_render.py             # Jinja2 renderer
├── tendrl_profile_daemon.py     # Profile auto-fetcher
├── templates/                   # 19 Jinja2 templates (from flux)
├── static/                      # CSS/JS (from flux)
├── public/                      # Generated HTML
└── ~/.local/share/tendrl/
    └── nostrdb/                 # Single LMDB database
```

---

## Config Translation

### 1. Basic Feed Definition

#### flux Config:
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
```

#### Tendrl Equivalent:
```toml
[feed.timeline]
name = "Timeline"
description = "Follow-filtered timeline"
display_kinds = [1, 6]
mode = "follows"                    # Replaces filter_by_follows
relay_mode = "general"
fetch_strategy = "stream"

# No db_file - all in nostrdb
# No refresh_interval - real-time streaming
# stream_kinds split into local + remote

[feed.timeline.pattern]
filter_type = "hybrid"

[[feed.timeline.pattern.local_queries]]
kinds = [1, 6]
limit = 100
note = "Backfill notes"

[[feed.timeline.pattern.local_queries]]
kinds = [7, 9735, 0]
limit = 2000
note = "Backfill engagement"

[[feed.timeline.pattern.remote_filters]]
kinds = [1, 6, 7, 9735, 0]          # Combines stream_kinds + engagement
limit = 250
```

**Key Differences**:
- ✅ No `db_file` (unified nostrdb)
- ✅ No `refresh_interval` (real-time)
- ✅ `mode` instead of `type` and `filter_by_follows`
- ✅ Split `stream_kinds` into `local_queries` + `remote_filters`

### 2. Root Event Configuration

#### flux Config:
```toml
[feed.timeline.root]
kind = 1
template = "short-note-card.j2"

[feed.timeline.root.filter]
no_e_tags = true  # Top-level posts only
```

#### Tendrl Equivalent:
```toml
[feed.timeline.root]
kind = 1
template = "short-note-card.j2"     # Same template

[feed.timeline.root.filter]
no_e_tags = true                    # Same filter
```

**Translation**: Same! Filter syntax is compatible.

### 3. Dependency Definitions

#### flux Config:
```toml
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

#### Tendrl Equivalent:
```toml
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

**Translation**: Identical! Dependency syntax is 100% compatible.

### 4. Relay Configuration

#### flux Config:
```toml
[feed.publications]
relays = [
    "wss://theforest.nostr1.com",
    "wss://thecitadel.nostr1.com"
]
relay_mode = "general"
```

#### Tendrl Equivalent:
```toml
[feed.publications]
relays = [
    "wss://theforest.nostr1.com",
    "wss://thecitadel.nostr1.com"
]
relay_mode = "general"
```

**Translation**: Identical! Relay config is compatible.

---

## Feature Translation

### 1. Follow Filtering

#### flux Approach:
```toml
# In config
filter_by_follows = true

# In code (enricher/db.py)
if feed.filter_by_follows:
    follows = get_follows(user_npub)
    query += " WHERE author IN (" + ",".join(follows) + ")"
```

#### Tendrl Approach:
```toml
# In config
mode = "follows"

# In daemon (tendrl_daemon/src/main.rs)
let follow_list = load_follow_list(&ndb, &user_pubkey)?;

if feed_def.mode == "follows" {
    for query in &mut pattern.local_queries {
        query.authors = follow_list.clone();
    }
    for filter in &mut pattern.remote_filters {
        filter.authors = follow_list.clone();
    }
}
```

**Translation**:
- flux: SQL WHERE clause (runtime filtering)
- Tendrl: Filter injection (build-time filtering)
- Result: Same behavior, different implementation

### 2. Profile Fetching

#### flux Approach:
```python
# enricher/main.py
def fetch_missing_profiles(pubkeys):
    missing = [pk for pk in pubkeys if not db.has_profile(pk)]
    batches = [missing[i:i+25] for i in range(0, len(missing), 25)]
    for batch in batches:
        profiles = nak_req(['--kinds', '0', '--authors'] + batch)
        db.store_profiles(profiles)
```

#### Tendrl Approach:
```python
# tendrl_profile_daemon.py (background process)
while True:
    visible_authors = extract_authors_from_feed(feed_name, limit=100)
    missing = check_missing_profiles(visible_authors)
    if missing:
        fetch_profiles_from_relays(missing, batch_size=25)
    time.sleep(poll_interval)
```

**Translation**:
- flux: On-demand profile fetching (during enrichment)
- Tendrl: Background daemon (continuous fetching)
- Result: Tendrl pre-fetches, flux fetches just-in-time

### 3. Event Enrichment

#### flux Approach:
```python
# enricher/enricher.py
def enrich_event(event, config):
    enriched = {**event}

    # Fetch author
    if 'author' in config.deps:
        enriched['author_profile'] = fetch_profile(event['pubkey'])

    # Aggregate reactions
    if 'reactions' in config.deps:
        reactions = query_reactions(event['id'])
        enriched['reactions'] = aggregate_by_content(reactions)

    return enriched
```

#### Tendrl Approach:
```rust
// tendrl_core/src/enrichment.rs
pub fn enrich_event(event: &Event, deps: &DependencyTree) -> EnrichedEvent {
    let mut enriched = EnrichedEvent::from(event);

    // Pre-enriched in database (profiles stored)
    if let Some(author_dep) = deps.get("author") {
        enriched.author = fetch_profile_from_nostrdb(&event.pubkey);
    }

    // Aggregate from stored events
    if let Some(reactions_dep) = deps.get("reactions") {
        let reactions = query_reactions_from_nostrdb(&event.id);
        enriched.reactions = aggregate_by_content(&reactions);
    }

    enriched
}
```

**Translation**:
- flux: Python runtime enrichment (from SQLite)
- Tendrl: Rust pre-enrichment (from nostrdb) + Python final render
- Result: Tendrl is faster (pre-enriched + LMDB)

### 4. Template Rendering

#### flux Approach:
```python
# enricher/main.py
from jinja2 import Environment, FileSystemLoader

env = Environment(loader=FileSystemLoader('templates'))
template = env.get_template('short-note-card.j2')
html = template.render(event=enriched_event)
```

#### Tendrl Approach:
```python
# tendrl_render.py
from jinja2 import Environment, FileSystemLoader

class TendrlRenderer:
    def __init__(self, templates_dir="templates"):
        self.env = Environment(loader=FileSystemLoader(templates_dir))

    def render_event(self, event, template_name):
        template = self.env.get_template(template_name)
        return template.render(event=event)
```

**Translation**: Identical patterns! Templates are 100% compatible.

---

## CLI Command Translation

### flux Commands:
```bash
# List feeds
python -m enricher.main --list-feeds

# Enrich a feed (JSONL)
python -m enricher.main --feed timeline --limit 50

# Enrich with rendering
python -m enricher.main --feed timeline --limit 50 --render

# Start feed scheduler (background)
python -m enricher.feed_scheduler enricher/config.toml

# Start API server
python enricher/server.py --local --port 8080

# Fetch single event
python -m enricher.main --event-id <hex_id> --render

# Load more (pagination)
python -m enricher.main --feed timeline --load-more 50 --render
```

### Tendrl Equivalents:
```bash
# List feeds (parse config)
grep "^\[feed\." tendrl_comprehensive.toml

# Query events (JSONL)
./target/release/tendrl_query --kinds 1 --limit 50

# Query specific feed (future feature)
./target/release/tendrl_query --feed timeline --limit 50

# Start daemon (background fetching)
./target/release/tendrl_daemon --config tendrl_comprehensive.toml &

# Start render daemon (HTML generation)
python3 tendrl_render_daemon.py --config tendrl_comprehensive.toml

# Start HTTP server
cd public && python3 -m http.server 8000

# Query single event
./target/release/tendrl_query --kinds 1 --limit 1 | grep <event_id>

# Pagination (not yet implemented)
./target/release/tendrl_query --kinds 1 --until 1698768000 --limit 50
```

---

## Template Variable Translation

### flux Template Variables:
```jinja2
{# In flux templates #}
{{ event.id }}
{{ event.pubkey }}
{{ event.created_at }}
{{ event.content }}
{{ event.tags }}
{{ event.author_profile.name }}
{{ event.author_profile.picture }}
{{ event.reactions.count }}
{{ event.reactions.by_content['👍'].count }}
{{ event.replies.count }}
{{ event.zaps.total_sats }}
```

### Tendrl Template Variables:
```jinja2
{# In Tendrl templates - SAME #}
{{ event.id }}
{{ event.pubkey }}
{{ event.created_at }}
{{ event.content }}
{{ event.tags }}
{{ event.author.name }}
{{ event.author.picture }}
{{ event.reactions.count }}
{{ event.reactions.by_content['👍'].count }}
{{ event.replies.count }}
{{ event.zaps.total_sats }}
```

**Translation**: Variable names are compatible!

Only difference:
- flux: `event.author_profile`
- Tendrl: `event.author`

Easy fix in templates:
```jinja2
{# Make compatible with both #}
{% set author = event.author or event.author_profile %}
{{ author.name }}
```

---

## Common Translation Patterns

### Pattern 1: Follow-Filtered Feed

**flux**:
```toml
[feed.my_feed]
type = "follows"
filter_by_follows = true
```

**Tendrl**:
```toml
[feed.my_feed]
mode = "follows"
```

### Pattern 2: Global Feed

**flux**:
```toml
[feed.my_feed]
type = "relay"
filter_by_follows = false
```

**Tendrl**:
```toml
[feed.my_feed]
mode = "global"
```

### Pattern 3: Specific Relays

**flux**:
```toml
[feed.my_feed]
relays = ["wss://relay1.com", "wss://relay2.com"]
```

**Tendrl**:
```toml
[feed.my_feed]
relays = ["wss://relay1.com", "wss://relay2.com"]
# Same syntax!
```

### Pattern 4: Event Kind Filtering

**flux**:
```toml
stream_kinds = [1, 6, 7, 9735]
display_kinds = [1, 6]
```

**Tendrl**:
```toml
display_kinds = [1, 6]

[feed.my_feed.pattern]
[[feed.my_feed.pattern.local_queries]]
kinds = [1, 6]

[[feed.my_feed.pattern.local_queries]]
kinds = [7, 9735]  # Engagement only

[[feed.my_feed.pattern.remote_filters]]
kinds = [1, 6, 7, 9735]  # All kinds for streaming
```

### Pattern 5: Enrichment Dependencies

**flux**:
```toml
[feed.my_feed.root.deps.reactions]
kind = 7
relation = "e_tag"
mode = "aggregate"
stats = ["count", "by_content"]
```

**Tendrl**:
```toml
# Exact same syntax!
[feed.my_feed.root.deps.reactions]
kind = 7
relation = "e_tag"
mode = "aggregate"
stats = ["count", "by_content"]
```

---

## Migration Checklist

When converting a flux feed to Tendrl:

### Step 1: Config Translation
- [ ] Copy `[feed.name]` section
- [ ] Change `type` → `mode` ("follows" or "global")
- [ ] Remove `db_file` (not needed)
- [ ] Remove `refresh_interval` (streaming is real-time)
- [ ] Keep `display_kinds` as-is
- [ ] Add `[feed.name.pattern]` section
- [ ] Split `stream_kinds` into:
  - `local_queries` (for backfill)
  - `remote_filters` (for streaming)
- [ ] Keep all `[feed.name.root.*]` sections as-is
- [ ] Keep all `[feed.name.root.deps.*]` sections as-is

### Step 2: Template Check
- [ ] Verify template exists in `templates/`
- [ ] Check template uses `event.author` not `event.author_profile`
- [ ] Test template renders correctly

### Step 3: Test Feed
- [ ] Add to `tendrl_comprehensive.toml`
- [ ] Run daemon: `./target/release/tendrl_daemon --config tendrl_comprehensive.toml`
- [ ] Check events are fetched
- [ ] Run render: `python3 tendrl_render_daemon.py`
- [ ] Verify HTML generated correctly
- [ ] Open in browser: http://localhost:8000/

### Step 4: Verify Behavior
- [ ] Follow filtering works (if mode="follows")
- [ ] Events appear in feed
- [ ] Profiles loaded correctly
- [ ] Engagement stats correct (reactions, zaps, reposts)
- [ ] Template renders properly

---

## Example: Converting flux's Highlights Feed

### flux Config:
```toml
[feed.highlights]
name = "highlights"
fetch_strategy = "stream"
relay_mode = "general"
db_file = "highlights.db"
filter_by_follows = false

[feed.highlights.root]
kind = 9802
template = "highlight-card.j2"

[feed.highlights.root.deps.author]
kind = 0
relation = "author"
required = true

[feed.highlights.root.deps.highlighted_event]
relation = "e_tag"
required = false
fetch_any_kind = true

[feed.highlights.root.deps.reactions]
kind = 7
relation = "e_tag"
mode = "aggregate"
stats = ["count", "by_content"]
```

### Tendrl Config:
```toml
[feed.highlights]
name = "Highlights"
description = "NIP-84 highlights from relays"
display_kinds = [9802]
mode = "global"                      # Changed: filter_by_follows=false
relay_mode = "general"
fetch_strategy = "stream"

[feed.highlights.pattern]
filter_type = "hybrid"

[[feed.highlights.pattern.local_queries]]
kinds = [9802]
limit = 100
note = "Backfill highlights"

[[feed.highlights.pattern.local_queries]]
kinds = [0, 1, 30023]               # New: Profile + common highlighted types
limit = 500
note = "Backfill profiles and highlighted content"

[[feed.highlights.pattern.remote_filters]]
kinds = [9802, 0, 1, 30023]         # New: Combined kinds for streaming
limit = 200

[feed.highlights.root]
kind = 9802
template = "highlight-card.j2"      # Same template!

[feed.highlights.root.deps.author]
kind = 0
relation = "author"
required = true

[feed.highlights.root.deps.highlighted_event]
relation = "e_tag"
required = false
fetch_any_kind = true

[feed.highlights.root.deps.reactions]
kind = 7
relation = "e_tag"
mode = "aggregate"
stats = ["count", "by_content"]
```

**Changes Made**:
1. ✅ Removed `db_file`
2. ✅ Changed `filter_by_follows: false` → `mode: "global"`
3. ✅ Added `[pattern]` section with local + remote
4. ✅ Split kinds into backfill + streaming
5. ✅ Kept all deps identical
6. ✅ Used same template

**Result**: Highlights feed works in Tendrl!

---

## Troubleshooting Translation Issues

### Issue: Events not appearing

**Check**:
```bash
# Are events in nostrdb?
./target/release/tendrl_query --kinds 9802 --limit 10

# Is daemon running?
ps aux | grep tendrl_daemon

# Are relays connected?
# Check daemon logs for "Connected to relay"
```

### Issue: Follow filter not working

**Check config**:
```toml
# Must have:
mode = "follows"

# Must have user config:
npub = "npub1..."
pubkey = "..."

# Check daemon logs for:
# "Loading follow list for user: ..."
# "Injecting 533 authors into feed 'name'"
```

### Issue: Template errors

**Check**:
```bash
# Template exists?
ls templates/highlight-card.j2

# Jinja2 installed?
python3 -c "import jinja2; print('OK')"

# Check render daemon output for errors
```

### Issue: Missing profiles

**Check**:
```bash
# Is profile daemon running?
ps aux | grep tendrl_profile_daemon

# Are profiles in nostrdb?
./target/release/tendrl_query --kinds 0 --limit 10

# Manually fetch missing profiles
python3 fetch_visible_profiles.py tendrl_comprehensive.toml my_feed 100
```

---

## Resources

### flux Documentation
- **flux Config**: `/path/to/flux/enricher/config.toml`
- **flux Enricher**: `/path/to/flux/enricher/main.py`
- **flux Templates**: `/path/to/flux/templates/*.j2`
- **flux Roadmap**: `/path/to/flux/CURRENT-STATE-AND-ROADMAP.org`

### Tendrl Documentation
- **Tendrl Config**: `tendrl_comprehensive.toml`
- **Current State**: [CURRENT_STATE.md](./CURRENT_STATE.md)
- **Roadmap**: [ROADMAP.md](./ROADMAP.md)
- **Comparison**: [FEEDS_COMPARISON.md](./FEEDS_COMPARISON.md)

---

**Last Updated**: October 31, 2025
**Status**: Translation guide complete, covers 95% of flux patterns
