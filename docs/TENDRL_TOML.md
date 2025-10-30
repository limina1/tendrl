# Tendrl Configuration Format

Comprehensive documentation for the `tendrl.toml` configuration file.

## Overview

`tendrl.toml` defines custom Nostr feeds that `tendrl_daemon` will fetch and store in nostrdb. Each feed specification tells the daemon:

- **What** to fetch (event kinds)
- **Where** to fetch from (relays)
- **How** to fetch (filters and queries)
- **What dependencies** to resolve (related events)

The configuration uses a declarative, hierarchical structure based on TOML format.

## File Structure

```toml
# Top-level sections
[user]                    # Your Nostr identity and preferences
[host]                    # Optional: host client metadata
[injection]               # Optional: injection preferences

# Feed definitions
[feed.feed_name]          # Define a custom feed
[feed.feed_name.pattern]  # Fetching instructions
[feed.feed_name.root]     # Root event configuration
[feed.feed_name.root.deps.*]  # Dependency definitions
```

## Top-Level Sections

### `[user]` - User Configuration

Defines your Nostr identity and default relay preferences.

```toml
[user]
npub = "npub1abc..."      # Your npub (optional)
name = "Your Name"        # Display name (optional)

relays = [
    "wss://relay.damus.io",
    "wss://nos.lol",
    "wss://relay.nostr.band"
]
```

**Fields**:
- `npub` (string, optional): Your Nostr public key in npub format
- `name` (string, optional): Your display name
- `relays` (array of strings): Default relays for feeds that don't specify custom relays

### `[user.cache]` - Cache Behavior

```toml
[user.cache]
profile_ttl = 0           # Profile cache TTL in seconds (0 = never expire)
event_refresh = 300       # Refetch event stats every N seconds
```

### `[host]` - Host Client Metadata (Optional)

If you're adapting an existing client, document its architecture here. This is informational only.

```toml
[host]
name = "notedeck"
framework = "egui"
architecture = "immediate mode GUI"
version = "0.1.0"
```

## Feed Definitions

Each feed is defined in a `[feed.<name>]` section.

### Basic Feed Structure

```toml
[feed.feed_name]
name = "Display Name"
description = "What this feed shows"
display_kinds = [1, 6, 7]  # Event kinds to display as root items

# Relay configuration
relay_mode = "general"     # "general" | "custom" | "inbox"
relays = [...]             # Optional: override user.relays

# Fetch behavior
fetch_strategy = "stream"  # "stream" | "manual" | "polling"
filter_by_follows = false  # Only show events from follows?
refetch_engagement = true  # Refetch stats on each query?
```

**Fields**:

- `name` (string, required): Human-readable feed name
- `description` (string, required): What the feed displays
- `display_kinds` (array of integers, required): Event kinds to show as root timeline items
- `relay_mode` (string, default "general"):
  - `"general"`: Use `user.relays`
  - `"custom"`: Use `relays` array defined in this feed
  - `"inbox"`: Use user's inbox relays (NIP-65)
- `relays` (array of strings, optional): Custom relay list if `relay_mode = "custom"`
- `fetch_strategy` (string, default "stream"):
  - `"stream"`: Continuous polling for new events
  - `"manual"`: Fetch only on explicit request
  - `"polling"`: Periodic refresh
- `filter_by_follows` (boolean, default false): Only fetch events from followed pubkeys
- `refetch_engagement` (boolean, default true): Always fetch fresh stats (zaps, replies, etc.)

### `[feed.<name>.pattern]` - Fetching Instructions

Defines HOW to fetch events using a hybrid approach (local + remote queries).

```toml
[feed.feed_name.pattern]
filter_type = "hybrid"

# Local queries (to nostrdb)
local_queries = [
    { kinds = [9735], limit = 500 },
    { kinds = [0], limit = 500 },
]

# Remote queries (to relays)
remote_filters = [
    { kinds = [9735, 0], limit = 250 }
]
```

**Fields**:

- `filter_type` (string, required): Always `"hybrid"` (local + remote)
- `local_queries` (array of objects, required): Queries sent to nostrdb
- `remote_filters` (array of objects, required): Filters sent to relays

**Local Query Object**:
```toml
{ kinds = [1, 6, 7], limit = 500, note = "Optional comment" }
```

- `kinds` (array of integers, required): Event kinds to query
- `limit` (integer, required): Maximum results to return
- `note` (string, optional): Human-readable comment

**Remote Filter Object**:
```toml
{ kinds = [1, 6, 7], limit = 250, authors = ["hex-pubkey"], since = 1234567890 }
```

- `kinds` (array of integers, required): Event kinds to subscribe to
- `limit` (integer, optional): Max results per relay
- `authors` (array of strings, optional): Filter by hex pubkeys
- `since` (integer, optional): Unix timestamp - events after this time
- `until` (integer, optional): Unix timestamp - events before this time

**Why both local AND remote?**

- **Local queries**: Fast access to already-cached events
- **Remote filters**: Fetch new events from relays

The daemon sends remote filters to relays, stores results in nostrdb, then local queries become fast lookups.

### `[feed.<name>.root]` - Root Event Configuration

Defines the primary event type displayed in the feed.

```toml
[feed.feed_name.root]
kind = 9735                # Event kind for root items
template = "zap-card"      # UI template name (for reference)
```

**Fields**:

- `kind` (integer, required): Event kind of root timeline items
- `template` (string, optional): UI template identifier (informational)

### `[feed.<name>.root.deps.*]` - Dependency Definitions

Dependencies are **related events** that enrich root events. Each dependency is defined in a separate subsection.

**Structure**:
```toml
[feed.feed_name.root.deps.dependency_name]
kind = 0
relation = "author"
mode = "single"
required = true
```

**Common Fields**:

- `kind` (integer, conditional): Event kind of dependency (not needed for special relations)
- `relation` (string, required): How dependency relates to root event
- `mode` (string, default "single"): Fetch mode - `"single"` | `"tree"` | `"aggregate"` | `"expanded"`
- `required` (boolean, default false): Must this dependency be fetched?
- `multiple` (boolean, default false): Can there be multiple instances?

**Relation Types**:

| Relation | Meaning | Example |
|----------|---------|---------|
| `"author"` | Event's author profile | Profile of note author |
| `"e_tag"` | Referenced event (e-tag) | Reply parent, quoted note |
| `"p_tag"` | Mentioned profile (p-tag) | Tagged users |
| `"a_tag"` | Addressable event (a-tag) | 30023 article reference |
| `"sender"` | Custom: event sender | Zap sender (from description) |
| `"recipient"` | Custom: event recipient | Zap recipient (from p-tag) |
| `"highlighted_author"` | Custom: author of highlighted content | Highlight source author |
| `"a_tag_children"` | Custom: events listed as children | Publication sections |

**Fetch Modes**:

| Mode | Description | Use Case |
|------|-------------|----------|
| `"single"` | Fetch one related event | Author profile |
| `"tree"` | Build hierarchical tree | Nested publications, threads |
| `"aggregate"` | Fetch and compute stats | Reaction counts, zap totals |
| `"expanded"` | Fetch and inline | Comments, nested content |

**Tree Mode Options**:
```toml
[feed.publications.root.deps.sub_publications]
kind = 30040
relation = "a_tag"
mode = "tree"
max_depth = 5             # Prevent infinite recursion
```

**Aggregate Mode Options**:
```toml
[feed.zaps.root.deps.reactions]
kind = 7
relation = "e_tag"
mode = "aggregate"
stats = ["count", "by_content"]  # Compute these statistics
```

- `stats` (array of strings): Statistics to compute
  - `"count"`: Total number
  - `"by_content"`: Breakdown by content field
  - `"total_sats"`: Sum of sats (for zaps)

## Complete Examples

### Example 1: Zaps Feed

```toml
[feed.zaps]
name = "Zaps"
description = "Lightning payment activity across the network"
display_kinds = [9735]

relay_mode = "general"
fetch_strategy = "stream"
filter_by_follows = false

[feed.zaps.pattern]
filter_type = "hybrid"

local_queries = [
    { kinds = [9735], limit = 500, note = "Zap receipts" },
    { kinds = [0], limit = 500, note = "Profiles" },
]

remote_filters = [
    { kinds = [9735, 0], limit = 250 }
]

[feed.zaps.root]
kind = 9735
template = "zap-card"

# Sender profile (from zap request description)
[feed.zaps.root.deps.sender]
kind = 0
relation = "sender"
required = true

# Recipient profile (from p-tag)
[feed.zaps.root.deps.recipient]
kind = 0
relation = "author"
required = true

# Event being zapped (optional)
[feed.zaps.root.deps.target_event]
relation = "e_tag"
required = false
fetch_any_kind = true
```

**What this does**:

1. Fetches kind 9735 (zap receipts) and kind 0 (profiles)
2. For each zap, fetches:
   - Sender profile (extracted from description field)
   - Recipient profile (from p-tag)
   - Optionally: the event that was zapped (from e-tag)

### Example 2: Highlights Feed

```toml
[feed.highlights]
name = "Highlights"
description = "Annotated text excerpts from across the network"
display_kinds = [9802]

relay_mode = "general"
fetch_strategy = "stream"
filter_by_follows = false

[feed.highlights.pattern]
filter_type = "hybrid"

local_queries = [
    { kinds = [9802], limit = 500, note = "Highlight events" },
    { kinds = [0], limit = 500, note = "Author profiles" },
]

remote_filters = [
    { kinds = [9802, 0], limit = 250 }
]

[feed.highlights.root]
kind = 9802
template = "highlight-card"

# Author of the highlight
[feed.highlights.root.deps.author]
kind = 0
relation = "author"
required = true

# The event being highlighted
[feed.highlights.root.deps.highlighted_event]
relation = "e_tag"
required = false
fetch_any_kind = true

# Author of highlighted content
[feed.highlights.root.deps.highlighted_author]
kind = 0
relation = "highlighted_author"
required = false

# Engagement stats
[feed.highlights.root.deps.reactions]
kind = 7
relation = "e_tag"
mode = "aggregate"
stats = ["count", "by_content"]

[feed.highlights.root.deps.replies]
kind = 1
relation = "e_tag"
mode = "aggregate"
stats = ["count"]

[feed.highlights.root.deps.zaps]
kind = 9735
relation = "e_tag"
mode = "aggregate"
stats = ["count", "total_sats"]
```

**What this does**:

1. Fetches kind 9802 (highlights) and author profiles
2. For each highlight, fetches:
   - Highlight author's profile
   - The original event being highlighted (if e-tag present)
   - Author of the highlighted content
3. Computes aggregate stats:
   - Reactions (count + breakdown by emoji)
   - Reply count
   - Zap count + total sats

### Example 3: Long-Form Articles

```toml
[feed.articles]
name = "Articles"
description = "Long-form content from across the network"
display_kinds = [30023]

relay_mode = "custom"
relays = [
    "wss://theforest.nostr1.com",
    "wss://thecitadel.nostr1.com"
]

fetch_strategy = "manual"
filter_by_follows = false

[feed.articles.pattern]
filter_type = "hybrid"

local_queries = [
    { kinds = [30023], limit = 500, note = "Long-form articles" },
    { kinds = [0], limit = 500, note = "Author profiles" },
]

remote_filters = [
    { kinds = [30023, 0], limit = 250 }
]

[feed.articles.root]
kind = 30023
template = "article-card"

[feed.articles.root.deps.author]
kind = 0
relation = "author"
required = true

[feed.articles.root.deps.reactions]
kind = 7
relation = "a_tag"
mode = "aggregate"
stats = ["count", "by_content"]

[feed.articles.root.deps.zaps]
kind = 9735
relation = "a_tag"
mode = "aggregate"
stats = ["count", "total_sats"]

[feed.articles.root.deps.replies]
kind = 1
relation = "a_tag"
mode = "aggregate"
stats = ["count"]
```

**What this does**:

1. Uses custom relays optimized for long-form content
2. Fetches kind 30023 (long-form articles) manually (not streaming)
3. For each article, fetches:
   - Author profile
4. Computes aggregate stats using **a-tags** (addressable events):
   - Reactions
   - Zaps
   - Replies

**Note**: Use `relation = "a_tag"` for addressable events (kinds 30000-39999), not `"e_tag"`.

### Example 4: Publications (Hierarchical)

```toml
[feed.publications]
name = "Publications"
description = "Hierarchical long-form content with nested sections"
display_kinds = [30040]

relay_mode = "custom"
relays = [
    "wss://theforest.nostr1.com",
    "wss://thecitadel.nostr1.com",
    "wss://medschlr.nostr1.com"
]

fetch_strategy = "manual"
filter_by_follows = false

[feed.publications.pattern]
filter_type = "hybrid"

local_queries = [
    { kinds = [30040], limit = 500, note = "Publication containers" },
    { kinds = [30041], limit = 500, note = "Sections" },
    { kinds = [30818], limit = 500, note = "Wiki pages" },
    { kinds = [30023], limit = 500, note = "Articles" },
    { kinds = [0], limit = 500, note = "Profiles" },
]

remote_filters = [
    { kinds = [30040, 30041, 30818, 30023, 0], limit = 250 }
]

[feed.publications.root]
kind = 30040
template = "publication-card"

[feed.publications.root.deps.author]
kind = 0
relation = "author"
required = true

# Nested sub-publications (tree structure)
[feed.publications.root.deps.sub_publications]
kind = 30040
relation = "a_tag"
mode = "tree"
max_depth = 5
required = false

# Content: Sections
[feed.publications.root.deps.sections]
kind = 30041
relation = "a_tag_children"
mode = "expanded"
required = false

# Content: Wiki pages
[feed.publications.root.deps.wiki_pages]
kind = 30818
relation = "a_tag_children"
mode = "expanded"
required = false

# Content: Articles
[feed.publications.root.deps.articles]
kind = 30023
relation = "a_tag_children"
mode = "expanded"
required = false

# Engagement on publications
[feed.publications.root.deps.reactions]
kind = 7
relation = "a_tag"
mode = "aggregate"
stats = ["count", "by_content"]

[feed.publications.root.deps.zaps]
kind = 9735
relation = "a_tag"
mode = "aggregate"
stats = ["count", "total_sats"]
```

**What this does**:

1. Fetches multiple content types (publications, sections, wikis, articles)
2. Builds hierarchical tree structure:
   - Publications can contain sub-publications (nested)
   - Each can contain sections, wiki pages, and articles
3. Limits tree depth to prevent infinite recursion
4. Computes engagement stats at each level

### Example 5: Notes with Filters

```toml
[feed.notes]
name = "Notes"
description = "Short-form text notes (kind 1)"
display_kinds = [1]

relay_mode = "general"
fetch_strategy = "stream"
filter_by_follows = true  # Only from follows

[feed.notes.pattern]
filter_type = "hybrid"

local_queries = [
    { kinds = [1], limit = 1000, note = "Text notes" },
    { kinds = [0], limit = 500, note = "Profiles" },
]

remote_filters = [
    # Fetch notes from last 24 hours
    {
        kinds = [1, 0],
        limit = 500,
        since = 1234567890  # Replace with current time - 86400
    }
]

[feed.notes.root]
kind = 1
template = "note-card"

[feed.notes.root.deps.author]
kind = 0
relation = "author"
required = true

# Quoted notes (from nostr:nevent URIs in content)
[feed.notes.root.deps.quoted_event]
relation = "e_tag"
required = false
fetch_any_kind = true

# Mentioned profiles
[feed.notes.root.deps.mentioned_profiles]
kind = 0
relation = "p_tag"
multiple = true
required = false

# Reactions to note
[feed.notes.root.deps.reactions]
kind = 7
relation = "e_tag"
mode = "aggregate"
stats = ["count", "by_content"]

# Replies to note
[feed.notes.root.deps.replies]
kind = 1
relation = "e_tag"
mode = "aggregate"
stats = ["count"]

# Zaps to note
[feed.notes.root.deps.zaps]
kind = 9735
relation = "e_tag"
mode = "aggregate"
stats = ["count", "total_sats"]

# Reposts
[feed.notes.root.deps.reposts]
kind = 6
relation = "e_tag"
mode = "aggregate"
stats = ["count"]
```

**What this does**:

1. Fetches kind 1 notes from the last 24 hours
2. Only shows notes from followed pubkeys (`filter_by_follows = true`)
3. For each note, fetches:
   - Author profile
   - Quoted events (if any)
   - Mentioned profiles (p-tags)
4. Computes full engagement stats (reactions, replies, zaps, reposts)

## Advanced Patterns

### Time-Based Filtering

```toml
remote_filters = [
    {
        kinds = [1],
        since = 1704067200,  # Jan 1, 2024
        until = 1735689600,  # Jan 1, 2025
        limit = 1000
    }
]
```

### Author Filtering

```toml
remote_filters = [
    {
        kinds = [30023],
        authors = [
            "hex-pubkey-1",
            "hex-pubkey-2"
        ],
        limit = 500
    }
]
```

### Multiple Filter Groups

```toml
remote_filters = [
    # Recent notes from anyone
    { kinds = [1], since = 1704067200, limit = 500 },

    # All articles from specific authors
    { kinds = [30023], authors = ["hex-pubkey"], limit = 100 },

    # Zaps from last week
    { kinds = [9735], since = 1734566400, limit = 1000 }
]
```

## Validation

### Required Fields

Every feed MUST have:
- `[feed.<name>]` with `name`, `description`, `display_kinds`
- `[feed.<name>.pattern]` with `filter_type`, `local_queries`, `remote_filters`
- `[feed.<name>.root]` with `kind`

### Common Mistakes

**Missing local_queries**:
```toml
# ❌ Wrong - no local queries
[feed.zaps.pattern]
filter_type = "hybrid"
remote_filters = [{ kinds = [9735], limit = 250 }]
```

```toml
# ✅ Correct - both local and remote
[feed.zaps.pattern]
filter_type = "hybrid"
local_queries = [{ kinds = [9735], limit = 500 }]
remote_filters = [{ kinds = [9735], limit = 250 }]
```

**Wrong relation for addressable events**:
```toml
# ❌ Wrong - kind 30023 needs a-tag relation
[feed.articles.root.deps.zaps]
kind = 9735
relation = "e_tag"  # Should be "a_tag"
```

```toml
# ✅ Correct - use a_tag for kinds 30000-39999
[feed.articles.root.deps.zaps]
kind = 9735
relation = "a_tag"
```

**Missing dependency kinds**:
```toml
# ❌ Wrong - fetching profiles but not querying them
local_queries = [
    { kinds = [9735], limit = 500 }
    # Missing: { kinds = [0], limit = 500 }
]
```

```toml
# ✅ Correct - include all dependency kinds
local_queries = [
    { kinds = [9735], limit = 500 },
    { kinds = [0], limit = 500 }  # Profiles for sender/recipient
]
```

## Best Practices

### 1. Always fetch profiles

```toml
local_queries = [
    { kinds = [9802], limit = 500 },
    { kinds = [0], limit = 500 },  # Always include profiles
]
```

### 2. Use appropriate limits

- Local queries: 500-1000 (nostrdb is fast)
- Remote filters: 100-250 (relay limits)

### 3. Match local and remote kinds

```toml
local_queries = [
    { kinds = [9735, 0], limit = 500 }
]
remote_filters = [
    { kinds = [9735, 0], limit = 250 }  # Same kinds
]
```

### 4. Use custom relays for specialized content

```toml
[feed.articles]
relay_mode = "custom"
relays = [
    "wss://theforest.nostr1.com",  # Long-form specialist
    "wss://thecitadel.nostr1.com"
]
```

### 5. Aggregate stats for performance

```toml
# ✅ Good - compute stats once
[feed.zaps.root.deps.reactions]
mode = "aggregate"
stats = ["count"]

# ❌ Bad - fetching all reactions individually
[feed.zaps.root.deps.reactions]
mode = "expanded"  # Don't do this for stats
```

## Troubleshooting

### Feed not appearing

- Check `display_kinds` matches `[feed.<name>.root].kind`
- Verify `local_queries` includes the display kinds
- Ensure relays actually serve those event kinds

### Missing related events

- Add dependency `kind` to `local_queries`
- Check `relation` type is correct (e_tag vs a_tag)
- Verify `required = false` for optional dependencies

### Slow queries

- Reduce `limit` in `local_queries`
- Use `aggregate` mode for stats instead of `expanded`
- Query fewer dependency kinds

### No events from relays

- Check relay URLs are valid WebSocket endpoints
- Some relays don't support all event kinds
- Try adding more relays to increase coverage

## Reference

### Event Kind Ranges

| Range | Purpose | Example |
|-------|---------|---------|
| 0-9999 | Regular events | 0 (profile), 1 (note), 7 (reaction) |
| 10000-19999 | Replaceable events | 10002 (relay list) |
| 20000-29999 | Ephemeral events | 20001 (notification) |
| 30000-39999 | Addressable events | 30023 (article), 30040 (publication) |

### Common Event Kinds

| Kind | Name | Relation Type |
|------|------|---------------|
| 0 | Profile metadata | author |
| 1 | Short note | e_tag |
| 6 | Repost | e_tag |
| 7 | Reaction | e_tag |
| 9735 | Zap receipt | e_tag or a_tag |
| 9802 | Highlight | e_tag |
| 30023 | Long-form article | a_tag |
| 30040 | Publication | a_tag |
| 30041 | Section | a_tag |
| 30818 | Wiki page | a_tag |

---

**Next**: See [USAGE.md](USAGE.md) for detailed usage instructions.
