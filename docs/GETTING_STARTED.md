# Getting Started with Tendrl

**Quick start guide for running Tendrl with follow-filtered feeds**

---

## Prerequisites

### System Requirements
- Linux (tested on 6.12.53-1-lts)
- Rust 1.70+ (for building binaries)
- Python 3.10+ (for rendering system)
- ~100MB free disk space (for nostrdb)
- ~100MB RAM (for daemon)

### Install Dependencies

```bash
# Rust (if not already installed)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Python packages
pip3 install --user jinja2 flask python-dateutil

# Build Tendrl binaries
cd tendrl
cargo build --release

# Verify binaries built
ls -lh target/release/tendrl_*
```

---

## Quick Start (5 minutes)

### 1. Configure Your Identity

Edit `tendrl_enrichment_test.toml`:

```toml
# Replace with your npub and pubkey
npub = "npub1m3xdppkd0njmrqe2ma8a6ys39zvgp5k8u22mev8xsnqp4nh80srqhqa5sf"
pubkey = "dc4cd086cd7ce5b1832adf4fdd1211289880d2c7e295bcb0e684c01acee77c06"
name = "your_name"
```

To get your pubkey from npub:
```bash
# If you have nak installed
echo "npub1..." | nak decode

# Or use a Nostr tool/website to convert
```

### 2. Start the System

**Option A: Simple (Single Feed)**

```bash
# Terminal 1: Start render daemon (handles everything)
python3 tendrl_render_daemon.py --config tendrl_enrichment_test.toml

# Wait for "Initial backfill complete!"
# Then open browser: http://localhost:8000
```

**Option B: Manual (More Control)**

```bash
# Terminal 1: Start fetching daemon
./target/release/tendrl_daemon --config tendrl_enrichment_test.toml

# Terminal 2: Start render daemon
python3 tendrl_render_daemon.py --config tendrl_enrichment_test.toml

# Terminal 3: Start HTTP server
cd public
python3 -m http.server 8000

# Open browser: http://localhost:8000
```

### 3. View Your Feeds

Open in browser:
- http://localhost:8000/ - Home (enriched notes)
- http://localhost:8000/timeline.html - Your timeline
- http://localhost:8000/replies.html - Conversations
- http://localhost:8000/articles.html - Long-form content
- http://localhost:8000/highlights.html - Highlights

---

## Understanding What's Happening

### Step 1: Load Follow List
```
tendrl_daemon starts
├── Loads tendrl_enrichment_test.toml
├── Finds your npub/pubkey
├── Queries nostrdb for kind-3 event
├── Extracts p-tags (your follows)
└── Result: 533 follows loaded ✓
```

### Step 2: Fetch Events
```
For each feed with mode="follows":
├── Injects your 533 follows as authors filter
├── Queries nostrdb (local backfill)
│   └── Finds 1803 existing notes
├── Subscribes to relays (remote streaming)
│   ├── Connects to 4 relays
│   └── Streams new events in real-time
└── Stores all events in nostrdb
```

### Step 3: Render HTML
```
tendrl_render_daemon:
├── Queries enriched events from nostrdb
├── Fetches missing profiles
├── Applies Jinja2 templates
├── Generates HTML to public/
└── Auto-refreshes every 60 seconds
```

### Step 4: Serve UI
```
HTTP Server:
├── Serves HTML from public/
├── Serves CSS/JS from static/
└── Listens on http://localhost:8000
```

---

## Configuration Files

### `tendrl_enrichment_test.toml`
**Purpose**: Single feed with full enrichment (reactions, zaps, reposts)
**Best for**: Testing, single timeline
**Feeds**: 1 (notes_enriched)

### `tendrl_comprehensive.toml`
**Purpose**: Multiple feed types (timeline, replies, zaps, highlights, etc.)
**Best for**: Production use, exploring different feed types
**Feeds**: 9 (timeline, replies, global, zaps, my_zaps, highlights, articles, publications)

---

## Common Tasks

### Switch to Comprehensive Config (9 Feeds)

```bash
# Stop current daemon
pkill -f tendrl_render_daemon

# Start with comprehensive config
python3 tendrl_render_daemon.py --config tendrl_comprehensive.toml

# This generates 9 different feed pages:
# - timeline.html
# - replies.html
# - global.html
# - zaps.html
# - my_zaps.html
# - highlights.html
# - articles.html
# - publications.html
# - (+ original notes_enriched.html)
```

### Query Events Directly

```bash
# Get your follow-filtered notes (JSONL)
./target/release/tendrl_query --kinds 1 --limit 100

# Get zaps
./target/release/tendrl_query --kinds 9735 --limit 50

# Get with time filter
./target/release/tendrl_query --kinds 1 --since 1698768000 --limit 100

# Pipe to jq for pretty printing
./target/release/tendrl_query --kinds 1 --limit 5 | jq .
```

### Check System Status

```bash
# Check running processes
ps aux | grep tendrl

# Check database size
du -sh ~/.local/share/tendrl/nostrdb

# Check how many events stored
./target/release/tendrl_query --kinds 1 | wc -l
```

### View Logs

```bash
# If running render daemon in background
tail -f /tmp/tendrl_render.log

# If running daemon manually, it logs to stdout
./target/release/tendrl_daemon --config tendrl_enrichment_test.toml
```

---

## Troubleshooting

### Problem: "No follows loaded"

**Solution**:
```bash
# Check you have a kind-3 event in nostrdb
./target/release/tendrl_query --kinds 3 --limit 1

# If empty, you need to fetch your follow list first
# Use another Nostr client or tool to publish your follow list
```

### Problem: "Empty feed / No events"

**Solutions**:
1. **Check database has events**:
   ```bash
   ./target/release/tendrl_query --kinds 1 --limit 10
   ```
   If empty, daemon hasn't fetched yet. Wait 30-60 seconds.

2. **Check daemon is running**:
   ```bash
   ps aux | grep tendrl_daemon
   ```
   If not running, start it:
   ```bash
   ./target/release/tendrl_daemon --config tendrl_enrichment_test.toml &
   ```

3. **Check relays are connecting**:
   Run daemon in foreground to see logs:
   ```bash
   ./target/release/tendrl_daemon --config tendrl_enrichment_test.toml
   ```
   Look for "Connected to relay: ..." messages.

### Problem: "Template not found" errors

**Solution**:
```bash
# Check templates exist
ls -la templates/

# If missing, you're in wrong directory
# Navigate to tendrl repo root:
cd /home/user/Documents/Programming/tendrl-worspace/tendrl
```

### Problem: "HTTP server not responding"

**Solutions**:
1. **Check server is running**:
   ```bash
   lsof -i :8000
   ```

2. **Restart server**:
   ```bash
   pkill -f "http.server 8000"
   cd public
   python3 -m http.server 8000 &
   ```

3. **Check HTML files exist**:
   ```bash
   ls -lh public/*.html
   ```
   If missing, render daemon hasn't generated them yet.

### Problem: "Permission denied" or "Cannot connect to relay"

**Solutions**:
1. **Check internet connection**
2. **Try different relays** (edit config):
   ```toml
   [profile_fetchers]
   relays = [
       "wss://relay.damus.io",
       "wss://nos.lol"
   ]
   ```

3. **Check firewall** isn't blocking WebSocket connections

---

## Directory Structure

```
tendrl/
├── target/release/
│   ├── tendrl_daemon          # Event fetcher (Rust binary)
│   └── tendrl_query           # Query tool (Rust binary)
├── tendrl_render_daemon.py    # UI rendering daemon
├── tendrl_render.py           # Jinja2 renderer
├── templates/                 # 19 Jinja2 templates
├── static/                    # CSS/JS assets
├── public/                    # Generated HTML (created on first run)
├── tendrl_enrichment_test.toml    # Single feed config
├── tendrl_comprehensive.toml      # 9 feeds config
├── CURRENT_STATE.md           # What works now
├── ROADMAP.md                 # Future plans
├── FLUX_TO_TENDRL_GUIDE.md   # Translation guide
└── GETTING_STARTED.md         # This file
```

---

## Next Steps

### 1. Explore Documentation

- **[CURRENT_STATE.md](./CURRENT_STATE.md)** - Understand what works
- **[ROADMAP.md](./ROADMAP.md)** - See what's planned
- **[FEEDS_COMPARISON.md](./FEEDS_COMPARISON.md)** - Compare to flux
- **[FLUX_TO_TENDRL_GUIDE.md](./FLUX_TO_TENDRL_GUIDE.md)** - Convert flux patterns

### 2. Customize Your Feeds

Edit `tendrl_comprehensive.toml`:

```toml
# Add your own feed
[feed.my_custom_feed]
name = "My Custom Feed"
description = "Whatever I want"
display_kinds = [1, 30023]  # Notes + articles
mode = "follows"            # Or "global"

[feed.my_custom_feed.pattern]
filter_type = "hybrid"

[[feed.my_custom_feed.pattern.local_queries]]
kinds = [1, 30023]
limit = 100

[[feed.my_custom_feed.pattern.remote_filters]]
kinds = [1, 30023, 0]  # Include profiles
limit = 200

[feed.my_custom_feed.root]
kind = 1
template = "short-note-card.j2"

[feed.my_custom_feed.root.deps.author]
kind = 0
relation = "author"
required = true
```

Then restart daemon to load new config.

### 3. Customize Templates

Templates are in `templates/`. They use Jinja2:

```jinja2
{# templates/my-custom-card.j2 #}
<div class="note-card">
  <div class="author">{{ event.author.name }}</div>
  <div class="content">{{ event.content }}</div>
  <div class="stats">
    ❤️ {{ event.reactions.count }}
    ⚡ {{ event.zaps.total_sats }} sats
  </div>
</div>
```

### 4. Integrate with Other Tools

Tendrl outputs JSONL, so it pipes easily:

```bash
# Export to JSON file
./target/release/tendrl_query --kinds 1 --limit 1000 > notes.json

# Pipe to analysis script
./target/release/tendrl_query --kinds 1 | python analyze.py

# Pipe to custom renderer
./target/release/tendrl_query --kinds 9735 | node render-zaps.js
```

---

## Performance Tips

### 1. Tune Limits

In your config:
```toml
[[feed.timeline.pattern.local_queries]]
kinds = [1]
limit = 100  # Start small, increase if needed

[[feed.timeline.pattern.remote_filters]]
kinds = [1, 7, 9735]
limit = 250  # Per relay
```

### 2. Reduce Poll Interval

```bash
# Default: 30 seconds
python3 tendrl_render_daemon.py --poll-interval 30

# Faster updates: 15 seconds (more CPU)
python3 tendrl_render_daemon.py --poll-interval 15

# Slower updates: 60 seconds (less CPU)
python3 tendrl_render_daemon.py --poll-interval 60
```

### 3. Clean Database (if needed)

```bash
# WARNING: Deletes all stored events!
rm -rf ~/.local/share/tendrl/nostrdb
# Daemon will re-fetch on next start
```

---

## Getting Help

### Check Logs
```bash
# Daemon logs (if running in foreground)
./target/release/tendrl_daemon --config tendrl_enrichment_test.toml

# Render daemon logs
python3 tendrl_render_daemon.py --config tendrl_enrichment_test.toml
```

### Verify Config
```bash
# Check config syntax
python3 -c "import toml; toml.load('tendrl_enrichment_test.toml'); print('Config OK')"
```

### Debug Mode
```bash
# Rust daemon with debug logging
RUST_LOG=debug ./target/release/tendrl_daemon --config tendrl_enrichment_test.toml
```

---

## Comparison to Other Tools

| Tool | Purpose | Tendrl Equivalent |
|------|---------|-------------------|
| **nak** | CLI event fetching | `tendrl_daemon` + `tendrl_query` |
| **Damus/Amethyst** | Full Nostr client | Tendrl (headless, no UI) |
| **flux/nostr-feeds** | Python enricher | Tendrl (Rust + Python hybrid) |
| **nostr-rs-relay** | Relay implementation | N/A (Tendrl is a client) |

**Tendrl's Niche**: Fast local database + config-driven feeds + pipes to anything

---

## Resources

- **Tendrl GitHub**: (Add URL when available)
- **flux/nostr-feeds**: Original inspiration for multi-feed patterns
- **Nostr Protocol**: https://github.com/nostr-protocol/nostr
- **nostrdb**: https://github.com/damus-io/nostrdb
- **Notedeck**: https://github.com/damus-io/notedeck (relay pool patterns)

---

**Last Updated**: October 31, 2025
**Status**: Ready for testing
**Next**: Try the comprehensive config with 9 feeds!
