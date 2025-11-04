# Tendrl Development Roadmap

**Current Phase**: Phase 1 Complete ✅
**Next Phase**: Phase 2 (UI Enhancement)
**Inspired By**: flux/nostr-feeds architecture & feature set

---

## Phase 1: Core Foundation ✅ COMPLETE

**Status**: All objectives achieved (Oct 31, 2025)

### Completed Features:

- [x] Fix follow list bug (kind-3 p-tag extraction)
- [x] Implement follow-filtered feeds (`mode = "follows"`)
- [x] Add global feed support (`mode = "global"`)
- [x] Standalone architecture (no Notedeck dependencies)
- [x] Hybrid filtering (local backfill + remote streaming)
- [x] Multi-feed configuration system (9 feed types)
- [x] Template integration (19 flux templates)
- [x] UI rendering system (Python daemon)
- [x] HTTP server (Primal UI design)
- [x] Profile auto-fetching

### Metrics:
- **533 follows** loaded from kind-3
- **1803 notes** backfilled instantly
- **1000+ events** processed from relays
- **6 HTML pages** generated (185KB - 17KB)
- **9 feed types** configured
- **19 templates** integrated

---

## Phase 2: UI Enhancement & Feed Navigation 🚧 NEXT

**Goal**: Match flux's interactive SPA experience

### 2.1 Feed Switcher UI
**Priority**: HIGH
**Inspired by**: flux's JSON API + browser SPA

**Tasks**:
- [ ] Add sidebar navigation for all feeds
  - Timeline (follows)
  - Global (all)
  - Replies (conversations)
  - Zaps (all zaps)
  - My Zaps (personal)
  - Highlights (NIP-84)
  - Articles (long-form)
  - Publications (knowledge base)

- [ ] Highlight active feed in sidebar
- [ ] Add feed icons (reuse Primal icon set)
- [ ] Implement client-side routing (pushState)
- [ ] Add feed counters (unread/total)

**Files to Modify**:
```
templates/layout.html.j2          # Add dynamic nav links
templates/feed_primal.html.j2     # Update feed container
static/js/feed.js                 # Add routing logic
tendrl_render.py                  # Generate nav metadata
```

**flux Reference**:
- `flux/enricher/static/js/app.js` - SPA routing
- `flux/enricher/static/js/feed-browser.js` - Feed switching
- `flux/enricher/templates/feed-browser.html` - Feed list UI

### 2.2 Real-Time Updates (Live Reload)
**Priority**: MEDIUM
**Inspired by**: flux's countdown timer + auto-refresh

**Options**:

**Option A**: JavaScript Polling (Simple)
```javascript
// In feed.js
setInterval(async () => {
  const response = await fetch('/api/feed/timeline?since=' + lastTimestamp);
  if (response.ok) {
    const newEvents = await response.json();
    prependEventsToFeed(newEvents);
  }
}, 30000); // 30 seconds
```

**Option B**: Server-Sent Events (Better)
```python
# In tendrl_render.py
@app.route('/api/feed/<feed_name>/stream')
def stream_feed(feed_name):
    def generate():
        while True:
            new_events = check_for_updates(feed_name)
            if new_events:
                yield f"data: {json.dumps(new_events)}\n\n"
            time.sleep(30)
    return Response(generate(), mimetype='text/event-stream')
```

**Option C**: WebSocket (Most Efficient)
```python
# Using Flask-SocketIO
@socketio.on('subscribe_feed')
def handle_subscribe(data):
    feed_name = data['feed']
    join_room(feed_name)

@socketio.on('new_events')
def broadcast_events(feed_name, events):
    emit('events_update', events, room=feed_name)
```

**Recommendation**: Start with Option A (polling), upgrade to B/C later.

**flux Reference**:
- `flux/enricher/static/js/feed-browser.js` - Countdown timer + auto-load

### 2.3 Pagination & Infinite Scroll
**Priority**: MEDIUM
**Inspired by**: flux's `--load-more` and `until` parameter

**Tasks**:
- [ ] Add "Load More" button at feed bottom
- [ ] Implement `until` parameter for older events
- [ ] Add infinite scroll option (lazy loading)
- [ ] Preserve scroll position on refresh
- [ ] Add "Jump to Top" button

**Implementation**:
```python
# In tendrl_render.py
def fetch_enriched_feed(feed_name, limit=50, until=None):
    """Fetch events, optionally before 'until' timestamp"""
    cmd = ['./target/release/tendrl_query', '--feed', feed_name, '--limit', str(limit)]
    if until:
        cmd.extend(['--until', str(until)])
    # ... rest of query logic
```

```javascript
// In feed.js
async function loadMoreEvents() {
  const oldestTimestamp = getOldestEventTimestamp();
  const response = await fetch(`/api/feed/timeline?limit=50&until=${oldestTimestamp}`);
  const olderEvents = await response.json();
  appendEventsToFeed(olderEvents);
}
```

**flux Reference**:
- `flux/enricher/main.py` - `--load-more` flag
- `flux/enricher/db.py` - `until` parameter in queries

---

## Phase 3: Advanced Feed Features 📋 PLANNED

**Goal**: Implement flux's advanced filtering and display modes

### 3.1 Thread Expansion
**Priority**: HIGH
**Inspired by**: flux's recursive reply expansion

**Tasks**:
- [ ] Add "Show Thread" button on notes with replies
- [ ] Implement recursive reply loading (max depth: 5)
- [ ] Add thread visualization (indentation or lines)
- [ ] Fetch parent notes for context
- [ ] Handle circular references (prevent infinite loops)

**Config Addition**:
```toml
[feed.timeline.root.deps.replies]
kind = 1
relation = "e_tag"
mode = "aggregate"
stats = ["count"]
expandable = true         # Already present
max_depth = 5             # NEW
fetch_parents = true      # NEW
```

**Template Addition** (use `thread-view.j2` from flux):
```jinja2
{# Recursive thread rendering #}
{% macro render_thread(note, depth=0) %}
  <div class="thread-item" style="margin-left: {{ depth * 20 }}px">
    {% include 'short-note-card.j2' %}
    {% if note.replies and depth < max_depth %}
      {% for reply in note.replies %}
        {{ render_thread(reply, depth + 1) }}
      {% endfor %}
    {% endif %}
  </div>
{% endmacro %}
```

**flux Reference**:
- `flux/enricher/thread_builder.py` - Recursive thread construction
- `flux/templates/thread-view.j2` - Thread visualization

### 3.2 Quoted Events & Mentions
**Priority**: MEDIUM
**Inspired by**: flux's quoted event extraction and rendering

**Tasks**:
- [ ] Extract `nostr:nevent1...` from note content
- [ ] Fetch quoted events automatically
- [ ] Render quoted events inline (preview card)
- [ ] Extract `nostr:nprofile1...` mentions
- [ ] Fetch mentioned profiles
- [ ] Render profile mentions as chips/tags

**Config Addition**:
```toml
[feed.timeline.root.deps.quoted_event]
relation = "quoted_event"     # Special relation
mode = "single"               # First quote only
required = false

[feed.timeline.root.deps.quoted_author]
kind = 0
relation = "quoted_author"
required = false

[feed.timeline.root.deps.mentioned_profiles]
relation = "mentioned_profiles"
required = false
multiple = true               # Can mention multiple users
```

**flux Reference**:
- `flux/enricher/nostr_utils.py` - nevent/nprofile parsing
- `flux/templates/note.html.j2` - Quoted event rendering

### 3.3 Repost Handling
**Priority**: MEDIUM
**Inspired by**: flux's kind-6 repost expansion

**Tasks**:
- [ ] Parse kind-6 repost content (JSON)
- [ ] Extract embedded event from repost
- [ ] Render original event with "reposted by" header
- [ ] Fetch reposted event's author profile
- [ ] Handle repost chains (repost of repost)

**Config Already Has** (in `tendrl_comprehensive.toml`):
```toml
[feed.timeline.root.deps.reposted_event]
relation = "repost_content"   # Parses JSON from content
mode = "expanded"             # Recursively enrich
max_depth = 3                 # Limit chains

[feed.timeline.root.deps.reposted_author]
kind = 0
relation = "reposted_author"
required = true
```

**Need to Implement**:
- Rust logic in `tendrl_core/src/enrichment.rs`
- Parse repost JSON content
- Extract embedded event
- Enrich recursively

**flux Reference**:
- `flux/enricher/enricher.py` - `_extract_reposted_event()`
- `flux/templates/repost-card.j2` - Repost rendering

---

## Phase 4: Content Types & Specialized Feeds 📚 PLANNED

**Goal**: Support flux's specialized content types

### 4.1 Long-Form Articles (kind 30023)
**Priority**: HIGH
**Status**: Config ready, needs testing

**Tasks**:
- [ ] Test articles feed with comprehensive config
- [ ] Verify `article-preview.j2` template works
- [ ] Add `article-full.j2` for single article view
- [ ] Extract article metadata (title, summary, image)
- [ ] Handle article tags (topics)
- [ ] Add article-specific relay list

**Config Already Present**:
```toml
[feed.articles]
name = "Articles"
display_kinds = [30023]
mode = "follows"
```

**Templates Available**:
- `article-preview.j2` - Cards in feed
- `article-full.j2` - Full article page

**flux Reference**:
- `flux/enricher/config.toml` - Article feed config
- `flux/templates/article-preview.j2` - Preview cards
- `flux/templates/article-full.j2` - Full view

### 4.2 Highlights (NIP-84, kind 9802)
**Priority**: MEDIUM
**Status**: Partially working (17KB generated)

**Tasks**:
- [ ] Test highlights feed more thoroughly
- [ ] Extract highlighted text ranges
- [ ] Fetch original highlighted content
- [ ] Render context around highlight
- [ ] Add highlight colors/markers
- [ ] Support a-tag highlights (addressable events)

**Config Already Present**:
```toml
[feed.highlights]
name = "Highlights"
display_kinds = [9802]
mode = "global"
```

**Template Available**: `highlight-card.j2`

**flux Reference**:
- `flux/enricher/config.toml` - Highlights feed
- `flux/templates/highlight-card.j2` - Highlight rendering

### 4.3 Publications (NIP-XX, kind 30040)
**Priority**: LOW (Complex)
**Status**: Config ready, needs extensive testing

**Tasks**:
- [ ] Test publications feed
- [ ] Handle nested structure (pub → sections → articles)
- [ ] Render table of contents
- [ ] Support wiki pages (kind 30818)
- [ ] Support sections (kind 30041)
- [ ] Build navigation hierarchy
- [ ] Add breadcrumb navigation

**Config Already Present**:
```toml
[feed.publications]
name = "Publications"
display_kinds = [30040]
mode = "global"
relays = [
    "wss://theforest.nostr1.com",
    "wss://thecitadel.nostr1.com",
    "wss://medschlr.nostr1.com"
]
```

**Templates Needed**:
- Need to create `publication-card.j2`
- Need publication-specific layouts

**flux Reference**:
- `flux/enricher/config.toml` - Publications feed
- `flux/enricher/alexandria_integration.py` - Publication handling
- Alexandria project for full implementation

---

## Phase 5: Profile & Social Features 👥 PLANNED

**Goal**: User profiles, follows, and social interactions

### 5.1 Profile Pages
**Priority**: HIGH
**Inspired by**: flux's profile-view feature

**Tasks**:
- [ ] Create `/profile/<npub>` route
- [ ] Fetch user's kind-0 (metadata)
- [ ] Display profile info (name, about, picture, banner)
- [ ] Show user's recent notes
- [ ] Show follower/following counts
- [ ] Add "Follow" button (future: kind-3 update)
- [ ] List user's articles, highlights
- [ ] Show zap stats (sent/received)

**Templates Available**:
- `profile-view.j2` - Full profile page
- `profile-header.j2` - Profile header component

**Implementation**:
```python
# In tendrl_render.py
@app.route('/profile/<npub>')
def profile_page(npub):
    pubkey = decode_npub(npub)
    profile = fetch_profile(pubkey)
    notes = fetch_user_notes(pubkey, limit=50)
    stats = compute_user_stats(pubkey)
    return render_template('profile-view.j2',
                           profile=profile,
                           notes=notes,
                           stats=stats)
```

**flux Reference**:
- `flux/enricher/main.py` - Profile fetching
- `flux/templates/profile-view.j2` - Profile layout

### 5.2 Follow List Management
**Priority**: MEDIUM
**Inspired by**: Contact list features

**Tasks**:
- [ ] Display current follow list
- [ ] Add search/filter for follows
- [ ] Group follows by category (future: kind-3 tags)
- [ ] Show follow activity stats
- [ ] Export follow list (CSV, JSON)
- [ ] Import follow list from other clients

**Template Available**: `contact-list.j2`

**Implementation**:
```python
@app.route('/follows')
def follows_page():
    follows = load_follow_list(user_npub)
    profiles = fetch_profiles(follows)
    stats = compute_follow_stats(follows)
    return render_template('contact-list.j2',
                           follows=profiles,
                           stats=stats)
```

**flux Reference**:
- `flux/templates/contact-list.j2` - Follow list display

### 5.3 Multi-User Support
**Priority**: LOW (Single-user works fine)

**Tasks**:
- [ ] Add user switcher UI
- [ ] Support multiple npubs in config
- [ ] Per-user follow lists
- [ ] User-specific feed filters
- [ ] Profile-scoped data paths

**Config Addition**:
```toml
# Current (single user)
npub = "npub1..."
pubkey = "dc4cd086..."

# Future (multi-user)
[[users]]
npub = "npub1m3xdpp..."
name = "liminal"
default = true

[[users]]
npub = "npub1another..."
name = "alt_account"
```

---

## Phase 6: Performance & Polish 🚀 PLANNED

**Goal**: Optimize for production use

### 6.1 Caching Strategy
**Priority**: HIGH
**Inspired by**: flux's two-tier caching (memory + SQLite)

**Current State**:
- nostrdb handles event caching (LMDB)
- No memory cache for rendered HTML
- No profile cache TTL

**Improvements**:
```python
# Add to tendrl_render.py
class TendrlRenderer:
    def __init__(self):
        self.profile_cache = {}       # In-memory profiles
        self.event_cache = LRUCache(1000)  # Recent events
        self.html_cache = {}          # Rendered HTML fragments

    def get_profile(self, pubkey):
        if pubkey in self.profile_cache:
            return self.profile_cache[pubkey]
        profile = fetch_from_nostrdb(pubkey)
        self.profile_cache[pubkey] = profile
        return profile
```

**Tasks**:
- [ ] Add in-memory profile cache (TTL: 1 hour)
- [ ] Cache rendered HTML fragments (note cards)
- [ ] Add Redis option for shared cache
- [ ] Implement cache invalidation on new events
- [ ] Add cache hit/miss metrics

### 6.2 Query Optimization
**Priority**: MEDIUM

**Tasks**:
- [ ] Add indexes to nostrdb (if possible with LMDB)
- [ ] Batch profile queries (fetch 25 at a time)
- [ ] Optimize tag extraction (pre-parse common tags)
- [ ] Add query result caching
- [ ] Profile slow queries with timing

### 6.3 UI Polish
**Priority**: MEDIUM

**Tasks**:
- [ ] Add loading spinners
- [ ] Add error messages (network failures)
- [ ] Improve mobile responsiveness
- [ ] Add dark mode toggle
- [ ] Add keyboard shortcuts
- [ ] Add accessibility (ARIA labels)
- [ ] Add animations (smooth transitions)

---

## Phase 7: Advanced Features 🔮 FUTURE

**Goal**: Features beyond flux

### 7.1 Search
**Priority**: HIGH for power users

**Options**:

**Option A**: Client-Side Search (Simple)
```javascript
function searchNotes(query) {
  return allNotes.filter(note =>
    note.content.toLowerCase().includes(query.toLowerCase()) ||
    note.author.name.toLowerCase().includes(query.toLowerCase())
  );
}
```

**Option B**: nostrdb Full-Text Search
- Would need nostrdb extension for FTS
- LMDB doesn't have built-in FTS
- Could use external index (Tantivy, MeiliSearch)

**Option C**: Hybrid (Best)
```python
# In tendrl_query
@app.route('/api/search')
def search():
    query = request.args.get('q')
    # Search nostrdb with regex/contains
    results = query_nostrdb(content_contains=query)
    return jsonify(results)
```

### 7.2 Bookmarks & Collections
**Priority**: MEDIUM

**Tasks**:
- [ ] Local bookmark storage (browser localStorage)
- [ ] Server-side bookmarks (kind TBD)
- [ ] Collections/folders
- [ ] Export bookmarks
- [ ] Share collections

### 7.3 Analytics & Stats
**Priority**: LOW (Nice to have)

**Tasks**:
- [ ] Feed statistics (events/day, authors, engagement)
- [ ] User stats (posts, reactions, zaps received)
- [ ] Trending topics (hashtag extraction)
- [ ] Engagement graphs (charts.js)
- [ ] Export data for analysis

### 7.4 Notifications
**Priority**: LOW (flux doesn't have this)

**Tasks**:
- [ ] Detect mentions of user
- [ ] Detect replies to user's notes
- [ ] Detect zaps received
- [ ] Browser notifications
- [ ] Notification feed

---

## Comparison: flux Phases vs Tendrl

| Phase | flux Status | Tendrl Status | Notes |
|-------|-------------|---------------|-------|
| **Phase 1**: Core fetching | ✅ Complete | ✅ Complete | Tendrl uses notedeck, flux uses nak |
| **Phase 2-5**: Basic feeds | ✅ Complete | ✅ Configured | Need testing |
| **Phase 6-9**: Enrichment | ✅ Complete | 🚧 In Progress | Rust enrichment vs Python |
| **Phase 10**: SPA Browser | ✅ Complete | ⏳ Planned | Tendrl: Phase 2 |
| **Phase 11**: Unified config | ✅ Complete | ✅ Complete | `tendrl_comprehensive.toml` |
| **Phase 12**: Advanced SPA | 🚧 In Progress | 📋 Planned | Reply composition, etc. |
| **Phase 13**: Editor integrations | 📋 Planned | ❓ Maybe | Emacs, Neovim, VSCode |

---

## Priority Ordering

### High Priority (Next 2-4 weeks)
1. ✅ Feed switcher UI with sidebar navigation
2. ✅ Real-time updates (polling or SSE)
3. ✅ Thread expansion (recursive replies)
4. ✅ Test all 9 feed types
5. ✅ Profile pages

### Medium Priority (1-2 months)
6. Pagination & infinite scroll
7. Quoted events & mentions
8. Repost handling (kind-6)
9. Search (basic)
10. Caching strategy

### Low Priority (3+ months)
11. Publications (complex structure)
12. Multi-user support
13. Bookmarks & collections
14. Analytics & stats
15. Notifications

---

## Contributing

When implementing features from this roadmap:

1. **Check flux Reference**: Most features have flux equivalent
2. **Start with Config**: Add to `tendrl_comprehensive.toml`
3. **Add Templates**: Copy/adapt from flux if available
4. **Test Standalone**: Verify works without flux
5. **Document**: Update this ROADMAP.md

See [FLUX_TO_TENDRL_GUIDE.md](./FLUX_TO_TENDRL_GUIDE.md) for translation patterns.

---

## Resources

- **flux/nostr-feeds**: https://github.com/your-username/nostr-feeds (if public)
- **flux ROADMAP**: `flux/CURRENT-STATE-AND-ROADMAP.org`
- **flux Templates**: `flux/templates/*.j2`
- **flux Config**: `flux/enricher/config.toml`
- **Tendrl Comparison**: [FEEDS_COMPARISON.md](./FEEDS_COMPARISON.md)
- **Tendrl Guide**: [FLUX_TO_TENDRL_GUIDE.md](./FLUX_TO_TENDRL_GUIDE.md)

---

**Last Updated**: October 31, 2025
**Next Milestone**: Phase 2.1 - Feed Switcher UI
