# Tendrl Enrichment System Design

## Problem Statement

We need two distinct feed modes:

1. **Independent Stream**: All kinds are peers, shown chronologically
2. **Per-Event Enrichment**: One primary kind + dependent events attached per root event

## Mode Detection

The presence of `[feed.X.root]` section determines the mode:

```toml
# Mode 1: Independent (no root section)
[feed.activity]
display_kinds = [1, 6, 7, 9735]
[feed.activity.pattern]
# ... pattern only, no root

# Mode 2: Per-Event Enrichment (has root section)
[feed.notes]
display_kinds = [1]
[feed.notes.pattern]
# ... pattern with ALL kinds (primary + deps)
[feed.notes.root]
kind = 1
[feed.notes.root.deps.reactions]
# ... dependencies
```

## Data Flow

### Phase 1: Fetching (Daemon)

Both modes fetch the same way:

```rust
// Pattern defines what to fetch
let pattern = feed.pattern;

// Fetch ALL kinds mentioned (primary + dependencies)
for local_query in pattern.local_queries {
    let filter = Filter::new().kinds(local_query.kinds).limit(local_query.limit);
    let events = ndb.query(&filter);
    // Store in timeline
}

for remote_filter in pattern.remote_filters {
    let filter = Filter::new().kinds(remote_filter.kinds).limit(remote_filter.limit);
    relay_pool.subscribe(filter);
    // Events arrive via RelayEvent, stored automatically
}
```

**Key point**: Daemon fetches everything upfront. Enrichment happens at query time.

### Phase 2: Querying (Query Tool or External Enricher)

This is where modes diverge:

#### Mode 1: Independent Stream

```bash
tendrl_query --db $DB --kinds 1,6,7,9735 --limit 100
```

Output: Flat list of all events, chronologically sorted:
```jsonl
{"id":"abc","kind":1,"content":"Hello"}
{"id":"def","kind":7,"content":"🔥"}
{"id":"ghi","kind":6,"content":"..."}
{"id":"jkl","kind":9735,"tags":[...]}
```

#### Mode 2: Per-Event Enrichment

```bash
tendrl_query --db $DB --feed notes --limit 100
```

The query tool reads `feed.notes.root` config and:

1. **Find root events**: Query only `display_kinds = [1]`
2. **For each root event**, find dependencies:
   - Reactions: `SELECT * FROM events WHERE kind=7 AND has_e_tag(id, root_event.id)`
   - Zaps: `SELECT * FROM events WHERE kind=9735 AND has_e_tag(id, root_event.id)`
   - Reposts: `SELECT * FROM events WHERE kind=6 AND has_e_tag(id, root_event.id)`
   - Author: `SELECT * FROM events WHERE kind=0 AND pubkey=root_event.pubkey`
3. **Aggregate stats** per mode:
   - `mode="aggregate"` → compute counts, totals
   - `mode="expanded"` → include full events
4. **Attach to root event**

Output: Only kind 1 events, each with enriched data:
```jsonl
{
  "id": "abc123",
  "kind": 1,
  "pubkey": "...",
  "content": "Hello Nostr!",
  "tags": [...],
  "_enriched": {
    "author": {
      "id": "xyz",
      "kind": 0,
      "content": "{\"name\":\"Alice\",\"picture\":\"...\"}"
    },
    "reactions": {
      "count": 42,
      "by_content": {
        "🔥": 20,
        "👍": 15,
        "❤️": 7
      },
      "events": [...]  // if mode="expanded"
    },
    "zaps": {
      "count": 5,
      "total_sats": 21000,
      "events": [...]
    },
    "reposts": {
      "count": 3,
      "events": [...]
    }
  }
}
```

## Implementation Phases

### Phase 1: Config Detection ✅ (Already Exists)

`tendrl_core/src/config.rs` already has:
```rust
pub struct FeedDefinition {
    pub display_kinds: Vec<u64>,
    pub pattern: FeedPattern,
    pub root: Option<RootConfig>,  // ✅ Optional → mode detection
}

pub struct RootConfig {
    pub kind: u64,
    pub template: String,
    pub deps: HashMap<String, DepConfig>,  // ✅ Dependency specs
}
```

Detection logic:
```rust
fn get_feed_mode(feed: &FeedDefinition) -> FeedMode {
    match &feed.root {
        None => FeedMode::IndependentStream,
        Some(_) => FeedMode::PerEventEnrichment,
    }
}
```

### Phase 2: Daemon Enhancement (Minimal Changes)

Daemon already fetches all kinds from `pattern`. No changes needed for fetching.

Only enhancement: Track which feeds have enrichment mode for logging.

### Phase 3: Query Tool Enhancement (Major Work)

Add `--feed <name>` flag to trigger enrichment mode:

```bash
# Independent mode (existing behavior)
tendrl_query --kinds 1,6,7,9735

# Per-event enrichment mode (new behavior)
tendrl_query --feed notes  # Reads notes.root.deps from config
```

New query logic:
```rust
if let Some(feed_name) = args.feed {
    let config = TendrlConfig::load()?;
    let feed = config.get_feed(&feed_name)?;

    if let Some(root_config) = &feed.root {
        // Per-event enrichment mode
        query_with_enrichment(ndb, feed, root_config)?;
    } else {
        // Fallback to independent mode
        query_independent(ndb, &feed.display_kinds)?;
    }
} else {
    // Default independent mode
    query_independent(ndb, &args.kinds)?;
}
```

### Phase 4: Enrichment Engine (New Module)

Create `tendrl_core/src/enrichment.rs`:

```rust
pub struct EnrichmentEngine<'a> {
    ndb: &'a Ndb,
    root_config: &'a RootConfig,
}

impl<'a> EnrichmentEngine<'a> {
    pub fn enrich_event(&self, root_event: &Note) -> EnrichedEvent {
        let mut enriched = EnrichedEvent::from_note(root_event);

        for (dep_name, dep_config) in &self.root_config.deps {
            let dep_events = self.fetch_dependency(root_event, dep_config);

            match dep_config.mode.as_str() {
                "aggregate" => {
                    enriched.add_stats(dep_name, aggregate(dep_events, &dep_config.stats));
                }
                "expanded" => {
                    enriched.add_events(dep_name, dep_events);
                }
                "tree" => {
                    enriched.add_tree(dep_name, build_tree(dep_events, dep_config.max_depth));
                }
                _ => {}
            }
        }

        enriched
    }

    fn fetch_dependency(&self, root: &Note, dep: &DepConfig) -> Vec<Note> {
        match dep.relation.as_str() {
            "e_tag" => {
                // Find events with e-tag referencing root
                let filter = Filter::new()
                    .kinds(vec![dep.kind.unwrap()])
                    .event(root.id())
                    .build();
                self.ndb.query(&filter).unwrap()
            }
            "author" => {
                // Find author profile
                let filter = Filter::new()
                    .kinds(vec![0])
                    .authors(vec![root.pubkey()])
                    .build();
                self.ndb.query(&filter).unwrap()
            }
            "p_tag" => {
                // Find events for mentioned pubkeys
                let p_tags = extract_p_tags(root);
                let filter = Filter::new()
                    .kinds(vec![dep.kind.unwrap()])
                    .authors(p_tags)
                    .build();
                self.ndb.query(&filter).unwrap()
            }
            _ => vec![]
        }
    }
}
```

### Phase 5: Thread Reconstruction (Advanced)

For notes, separate top-level from replies:

```toml
[feed.notes_toplevel]
display_kinds = [1]

[feed.notes_toplevel.root]
kind = 1
exclude_tags = ["e"]  # NEW: No e-tags = top-level posts

[feed.notes_replies]
display_kinds = [1]

[feed.notes_replies.root]
kind = 1
require_tags = ["e"]  # NEW: Has e-tags = replies

[feed.notes_replies.root.deps.thread_root]
kind = 1
relation = "e_tag_root"  # NEW: Find root event
mode = "tree"
max_depth = 50
```

Implementation:
```rust
fn is_top_level(note: &Note) -> bool {
    !note.tags().iter().any(|t| t.get(0) == Some("e"))
}

fn get_thread_root(note: &Note, ndb: &Ndb) -> Option<Note> {
    // Parse e-tags for "root" marker or first e-tag
    let root_id = extract_root_event_id(note)?;
    ndb.get_note_by_id(root_id).ok()
}

fn build_thread_tree(root: &Note, ndb: &Ndb, max_depth: usize) -> ThreadTree {
    let mut tree = ThreadTree::new(root);

    // Find all replies to root
    let filter = Filter::new()
        .kinds(vec![1])
        .event(root.id())
        .build();
    let replies = ndb.query(&filter).unwrap();

    // Recursively build tree
    for reply in replies {
        if tree.depth() < max_depth {
            let subtree = build_thread_tree(&reply, ndb, max_depth);
            tree.add_child(subtree);
        }
    }

    tree
}
```

## Config Examples

### Example 1: Notes with Full Engagement

```toml
[feed.notes]
name = "Notes with Engagement"
display_kinds = [1]

[feed.notes.pattern]
filter_type = "hybrid"
local_queries = [
    { kinds = [1], limit = 500 },
    { kinds = [7, 9735, 6, 0], limit = 5000 },
]
remote_filters = [
    { kinds = [1, 7, 9735, 6, 0], limit = 250 }
]

[feed.notes.root]
kind = 1

[feed.notes.root.deps.author]
kind = 0
relation = "author"
required = true

[feed.notes.root.deps.reactions]
kind = 7
relation = "e_tag"
mode = "aggregate"
stats = ["count", "by_content"]

[feed.notes.root.deps.zaps]
kind = 9735
relation = "e_tag"
mode = "aggregate"
stats = ["count", "total_sats"]

[feed.notes.root.deps.reposts]
kind = 6
relation = "e_tag"
mode = "aggregate"
stats = ["count"]
```

### Example 2: Top-Level Posts Only

```toml
[feed.notes_toplevel]
name = "Top-Level Posts"
display_kinds = [1]

[feed.notes_toplevel.pattern]
filter_type = "hybrid"
local_queries = [{ kinds = [1], limit = 500 }]
remote_filters = [{ kinds = [1], limit = 250 }]

[feed.notes_toplevel.root]
kind = 1
exclude_tags = ["e"]  # No replies

[feed.notes_toplevel.root.deps.author]
kind = 0
relation = "author"
```

### Example 3: Threaded Conversations

```toml
[feed.threads]
name = "Threaded Conversations"
display_kinds = [1]

[feed.threads.pattern]
filter_type = "hybrid"
local_queries = [{ kinds = [1], limit = 1000 }]
remote_filters = [{ kinds = [1], limit = 500 }]

[feed.threads.root]
kind = 1

[feed.threads.root.deps.thread]
kind = 1
relation = "e_tag"
mode = "tree"
max_depth = 50

[feed.threads.root.deps.author]
kind = 0
relation = "author"
```

## Migration Path

1. ✅ **Phase 0**: Current state (independent streams work)
2. 🔧 **Phase 1**: Add enrichment detection to query tool
3. 🔧 **Phase 2**: Implement basic enrichment (author lookup)
4. 🔧 **Phase 3**: Add aggregate mode (counts, stats)
5. 🔧 **Phase 4**: Add expanded mode (full events)
6. 🔧 **Phase 5**: Add tree mode (threads)
7. 🔧 **Phase 6**: Add tag-based filtering (exclude_tags, require_tags)

## Performance Considerations

**Enrichment cost per event**:
- Author lookup: 1 query (~0.5ms)
- Reactions aggregate: 1 query + counting (~1-2ms)
- Zaps aggregate: 1 query + sum (~1-2ms)
- Thread tree: N queries for N-depth tree (~N*0.5ms)

**For 100 root events with full enrichment**:
- ~300-500ms total (3-5ms per event)
- Still faster than fetching from relays (~100-500ms per relay)

**Optimization**: Batch queries where possible:
```rust
// Instead of 100 separate author queries
for note in notes {
    query_author(note.pubkey());  // ❌ 100 queries
}

// Batch into one query
let pubkeys: Vec<_> = notes.iter().map(|n| n.pubkey()).collect();
let filter = Filter::new().kinds(vec![0]).authors(pubkeys).build();
let authors = ndb.query(&filter);  // ✅ 1 query
```

## Testing Strategy

1. **Unit tests**: Enrichment engine with mock data
2. **Integration tests**: Full pipeline with test config
3. **Benchmark**: Performance on 1K, 10K, 100K events
4. **Comparison**: Output matches nostr-feeds enricher

## Summary

**Key insight**: Enrichment happens at **query time**, not fetch time.

- **Daemon**: Fetches everything (primary + deps), stores flat
- **Query tool**: Reads config, builds relationships, outputs enriched JSONL
- **External tools**: Receive enriched data, just render

This keeps the daemon simple (just fetch + store) and makes the query tool powerful (read config, enrich dynamically).
