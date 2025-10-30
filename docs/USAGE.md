# Tendrl Usage Guide

Complete guide to installing, configuring, and using Tendrl.

## Table of Contents

1. [Installation](#installation)
2. [Configuration](#configuration)
3. [Running the Daemon](#running-the-daemon)
4. [Querying Data](#querying-data)
5. [Integration Examples](#integration-examples)
6. [Troubleshooting](#troubleshooting)
7. [Advanced Usage](#advanced-usage)

## Installation

### Prerequisites

- **Rust toolchain**: Install from [rustup.rs](https://rustup.rs/)
- **Git**: For cloning the repository
- **Disk space**: ~1-5 GB for nostrdb database (grows with usage)

### Build from Source

```bash
# Clone the repository
git clone https://github.com/yourusername/tendrl.git
cd tendrl

# Build release binaries
cargo build --release

# Binaries are in target/release/
ls -lh target/release/tendrl_*
```

**Expected output**:
```
-rwxr-xr-x 1 user user 8.2M Oct 30 10:00 tendrl_daemon
-rwxr-xr-x 1 user user 5.4M Oct 30 10:00 tendrl_query
```

### Install Binaries (Optional)

```bash
# Copy to ~/.local/bin (ensure it's in PATH)
cp target/release/tendrl_daemon ~/.local/bin/
cp target/release/tendrl_query ~/.local/bin/

# Or install system-wide (requires sudo)
sudo cp target/release/tendrl_daemon /usr/local/bin/
sudo cp target/release/tendrl_query /usr/local/bin/
```

### Verify Installation

```bash
tendrl_daemon --help
tendrl_query --help
```

## Configuration

### Create Configuration File

```bash
# Copy example config
cp examples/tendrl.toml.example tendrl.toml

# Edit with your preferred editor
nano tendrl.toml
```

### Basic Configuration

Minimal `tendrl.toml`:

```toml
[user]
relays = [
    "wss://relay.damus.io",
    "wss://nos.lol",
    "wss://relay.nostr.band"
]

[feed.zaps]
name = "Zaps"
description = "Lightning payment activity"
display_kinds = [9735]

[feed.zaps.pattern]
filter_type = "hybrid"
local_queries = [
    { kinds = [9735], limit = 500 },
    { kinds = [0], limit = 500 },
]
remote_filters = [
    { kinds = [9735, 0], limit = 250 }
]

[feed.zaps.root]
kind = 9735

[feed.zaps.root.deps.sender]
kind = 0
relation = "sender"
required = true

[feed.zaps.root.deps.recipient]
kind = 0
relation = "author"
required = true
```

### Configuration Locations

Tendrl looks for config in this order:

1. `--config` command-line argument
2. `./tendrl.toml` (current directory)
3. `~/.config/tendrl/tendrl.toml`
4. `/etc/tendrl/tendrl.toml`

**Recommended**: Keep `tendrl.toml` in project directory or `~/.config/tendrl/`.

### Validate Configuration

```bash
# Check TOML syntax
toml-cli check tendrl.toml

# Or just try starting the daemon
tendrl_daemon --config tendrl.toml --log-level debug
# If it starts without errors, config is valid
```

## Running the Daemon

### Basic Usage

```bash
# Start daemon with config
tendrl_daemon --config tendrl.toml
```

**Output**:
```
[2025-10-30T15:04:23Z INFO  tendrl_daemon] Starting Tendrl daemon
[2025-10-30T15:04:23Z INFO  tendrl_daemon] Loading config from: tendrl.toml
[2025-10-30T15:04:23Z INFO  tendrl_daemon] Database path: /home/user/.local/share/tendrl/nostrdb
[2025-10-30T15:04:23Z INFO  tendrl_daemon] Connecting to 3 relays...
[2025-10-30T15:04:23Z INFO  tendrl_daemon] Subscribing to 2 feeds...
[2025-10-30T15:04:23Z INFO  tendrl_daemon] Daemon running. Press Ctrl+C to stop.
```

### Command-Line Options

```bash
tendrl_daemon [OPTIONS]

Options:
  --config <PATH>        Path to tendrl.toml (default: ./tendrl.toml)
  --db <PATH>            Path to nostrdb directory (default: ~/.local/share/tendrl/nostrdb)
  --log-level <LEVEL>    Set logging level: error, warn, info, debug, trace
  -h, --help             Print help information
  -V, --version          Print version information
```

### Custom Database Path

```bash
# Store database in custom location
tendrl_daemon --config tendrl.toml --db ~/mydata/nostrdb
```

### Logging Levels

```bash
# Minimal output (errors only)
tendrl_daemon --config tendrl.toml --log-level error

# Normal output (info messages)
tendrl_daemon --config tendrl.toml --log-level info

# Verbose output (debug messages)
tendrl_daemon --config tendrl.toml --log-level debug

# Maximum verbosity (trace everything)
tendrl_daemon --config tendrl.toml --log-level trace
```

### Running as Background Service

#### Using systemd (Linux)

Create `/etc/systemd/system/tendrl.service`:

```ini
[Unit]
Description=Tendrl Nostr Feed Daemon
After=network.target

[Service]
Type=simple
User=your-username
ExecStart=/usr/local/bin/tendrl_daemon --config /home/your-username/.config/tendrl/tendrl.toml
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable tendrl
sudo systemctl start tendrl

# Check status
sudo systemctl status tendrl

# View logs
sudo journalctl -u tendrl -f
```

#### Using screen/tmux (Any Unix)

```bash
# Start in screen
screen -S tendrl
tendrl_daemon --config tendrl.toml
# Detach: Ctrl+A, D

# Reattach
screen -r tendrl

# Or use tmux
tmux new -s tendrl
tendrl_daemon --config tendrl.toml
# Detach: Ctrl+B, D

# Reattach
tmux attach -t tendrl
```

#### Using nohup (Simple Background)

```bash
# Start and detach
nohup tendrl_daemon --config tendrl.toml > tendrl.log 2>&1 &

# Check if running
ps aux | grep tendrl_daemon

# View logs
tail -f tendrl.log

# Stop
pkill tendrl_daemon
```

### Stopping the Daemon

```bash
# Graceful stop (Ctrl+C if foreground)
^C

# Or send SIGTERM
pkill tendrl_daemon

# Or if using systemd
sudo systemctl stop tendrl
```

**Important**: Always stop gracefully to ensure nostrdb flushes data to disk.

## Querying Data

### Basic Queries

```bash
# Query with database path
tendrl_query --db ~/.local/share/tendrl/nostrdb

# Defaults to fetching all events (not recommended for large databases)
```

### Filter by Event Kind

```bash
# Get zaps (kind 9735)
tendrl_query --db ~/nostrdb --kinds 9735

# Get multiple kinds
tendrl_query --db ~/nostrdb --kinds 1,6,7

# Get highlights
tendrl_query --db ~/nostrdb --kinds 9802
```

### Filter by Author

```bash
# Using npub
tendrl_query --db ~/nostrdb --author npub1abc123...

# Using hex pubkey
tendrl_query --db ~/nostrdb --author 1234567890abcdef...

# Combine with kind filter
tendrl_query --db ~/nostrdb --kinds 30023 --author npub1abc...
```

### Filter by Time Range

```bash
# Events since timestamp
tendrl_query --db ~/nostrdb --since 1704067200

# Events until timestamp
tendrl_query --db ~/nostrdb --until 1735689600

# Events in range
tendrl_query --db ~/nostrdb \
  --since 1704067200 \
  --until 1735689600

# Last 24 hours (using date command)
tendrl_query --db ~/nostrdb \
  --since $(date -d '24 hours ago' +%s)

# Last week
tendrl_query --db ~/nostrdb \
  --since $(date -d '7 days ago' +%s)
```

### Limit Results

```bash
# Get latest 50 events
tendrl_query --db ~/nostrdb --limit 50

# Get latest 1000 zaps
tendrl_query --db ~/nostrdb --kinds 9735 --limit 1000
```

### Combining Filters

```bash
# Latest 100 highlights from specific author in last 30 days
tendrl_query --db ~/nostrdb \
  --kinds 9802 \
  --author npub1abc... \
  --since $(date -d '30 days ago' +%s) \
  --limit 100

# All articles between two dates
tendrl_query --db ~/nostrdb \
  --kinds 30023 \
  --since 1704067200 \
  --until 1735689600 \
  --limit 500
```

### Output Format

Output is **JSONL** (JSON Lines):

```json
{"id":"abc123...","pubkey":"def456...","created_at":1234567890,"kind":9735,"tags":[["p","xyz"],["bolt11","lnbc..."]],"content":"","sig":"..."}
{"id":"xyz789...","pubkey":"ghi012...","created_at":1234567891,"kind":9735,"tags":[["p","abc"],["bolt11","lnbc..."]],"content":"","sig":"..."}
```

Each line is a valid JSON object (one event per line).

## Integration Examples

### With jq (JSON Processing)

#### Count Events by Kind

```bash
tendrl_query --db ~/nostrdb --limit 10000 | \
  jq -r '.kind' | \
  sort | uniq -c | sort -rn
```

**Output**:
```
4532 1
2341 7
1234 9735
 567 9802
 123 30023
```

#### Extract Zap Amounts

```bash
tendrl_query --db ~/nostrdb --kinds 9735 --limit 100 | \
  jq -r '.tags[] | select(.[0] == "bolt11") | .[1]' | \
  # Decode bolt11 invoices (requires bolt11 tool)
  while read invoice; do
    bolt11 decode "$invoice" | jq -r '.amount_msat'
  done | \
  awk '{sum+=$1} END {print "Total sats:", sum/1000}'
```

#### Find Most-Zapped Events

```bash
tendrl_query --db ~/nostrdb --kinds 9735 --limit 1000 | \
  jq -r '.tags[] | select(.[0] == "e") | .[1]' | \
  sort | uniq -c | sort -rn | head -10
```

#### List Unique Authors

```bash
tendrl_query --db ~/nostrdb --kinds 9802 --limit 500 | \
  jq -r '.pubkey' | \
  sort -u
```

### With Python

#### Simple Enricher

```python
#!/usr/bin/env python3
import sys
import json

for line in sys.stdin:
    try:
        event = json.loads(line)

        # Extract fields
        event_id = event['id']
        author = event['pubkey'][:8]  # First 8 chars
        kind = event['kind']
        content = event['content'][:100]  # First 100 chars

        # Print formatted
        print(f"[{kind}] {author}: {content}")

    except json.JSONDecodeError:
        continue
```

**Usage**:
```bash
tendrl_query --db ~/nostrdb --kinds 1 --limit 50 | ./enricher.py
```

#### Fetch and Cache Profiles

```python
#!/usr/bin/env python3
import sys
import json
import requests
from datetime import datetime

# Profile cache
profiles = {}

def get_profile(pubkey):
    if pubkey not in profiles:
        # Query nostr.band API for profile
        url = f"https://api.nostr.band/v0/stats/profile/{pubkey}/info"
        try:
            response = requests.get(url, timeout=5)
            data = response.json()
            profiles[pubkey] = data.get('profile', {})
        except:
            profiles[pubkey] = {'name': pubkey[:8]}

    return profiles[pubkey]

for line in sys.stdin:
    event = json.loads(line)
    profile = get_profile(event['pubkey'])

    # Add profile to event
    event['_profile'] = profile

    # Output enriched event
    print(json.dumps(event))
```

**Usage**:
```bash
tendrl_query --db ~/nostrdb --kinds 9802 --limit 50 | \
  ./enrich_profiles.py | \
  jq -r '"\(._profile.name): \(.content)"'
```

#### Render HTML

```python
#!/usr/bin/env python3
import sys
import json
from datetime import datetime

print("<html><body><h1>Highlights</h1>")

for line in sys.stdin:
    event = json.loads(line)

    # Parse fields
    content = event['content']
    author = event['pubkey'][:8]
    timestamp = datetime.fromtimestamp(event['created_at'])

    # Render HTML
    print(f"""
    <div class="highlight">
        <blockquote>{content}</blockquote>
        <footer>— {author} at {timestamp}</footer>
    </div>
    """)

print("</body></html>")
```

**Usage**:
```bash
tendrl_query --db ~/nostrdb --kinds 9802 --limit 100 | \
  ./render_html.py > highlights.html
```

### With Shell Scripts

#### Daily Digest Email

```bash
#!/bin/bash

DB="$HOME/.local/share/tendrl/nostrdb"
YESTERDAY=$(date -d 'yesterday' +%s)
TODAY=$(date +%s)

# Fetch yesterday's events
tendrl_query --db "$DB" --kinds 9735 --since "$YESTERDAY" --until "$TODAY" > /tmp/zaps.jsonl

# Count
COUNT=$(wc -l < /tmp/zaps.jsonl)

# Email
echo "Subject: Daily Zap Report: $COUNT zaps" | sendmail user@example.com < /tmp/zaps.jsonl
```

#### Backup Script

```bash
#!/bin/bash

DB="$HOME/.local/share/tendrl/nostrdb"
BACKUP="$HOME/backups/nostrdb-$(date +%Y%m%d).tar.gz"

# Stop daemon
pkill tendrl_daemon

# Wait for clean shutdown
sleep 2

# Backup
tar czf "$BACKUP" "$DB"

# Restart daemon
tendrl_daemon --config ~/.config/tendrl/tendrl.toml &

echo "Backup saved to $BACKUP"
```

### With nostr-feeds Enricher

[nostr-feeds](https://github.com/limina1/nostr-feeds) provides Jinja2-based rendering:

```bash
# Install nostr-feeds
pip install git+https://github.com/limina1/nostr-feeds

# Render publications feed
tendrl_query --db ~/nostrdb --kinds 30040 --limit 100 | \
  python -m nostr_feeds.enricher \
    --template publications.j2 \
    --output publications.html

# Render highlights feed
tendrl_query --db ~/nostrdb --kinds 9802 --limit 200 | \
  python -m nostr_feeds.enricher \
    --template highlights.j2 \
    --output highlights.html
```

## Troubleshooting

### Daemon won't start

**Symptom**: `tendrl_daemon` exits immediately.

**Check**:
```bash
# Run with debug logging
tendrl_daemon --config tendrl.toml --log-level debug
```

**Common causes**:
- Invalid TOML syntax in config file
- Database directory not writable
- Port conflicts (if daemon binds to port)

**Solutions**:
```bash
# Validate TOML
toml-cli check tendrl.toml

# Check database permissions
ls -ld ~/.local/share/tendrl/nostrdb

# Create directory if missing
mkdir -p ~/.local/share/tendrl/nostrdb
```

### No events being fetched

**Symptom**: Daemon runs but queries return empty.

**Check**:
```bash
# Query all events (no filters)
tendrl_query --db ~/.local/share/tendrl/nostrdb --limit 10
```

**Common causes**:
- Relays don't support requested event kinds
- Filters too restrictive
- Network connectivity issues
- Relays are offline

**Solutions**:
```bash
# Test relay connectivity
wscat -c wss://relay.damus.io

# Try different relays in tendrl.toml
relays = [
    "wss://nos.lol",
    "wss://relay.nostr.band",
    "wss://nostr.wine"
]

# Check daemon logs for errors
tendrl_daemon --config tendrl.toml --log-level info
```

### Query returns unexpected events

**Symptom**: Wrong events in query results.

**Explanation**: nostrdb stores **all** events from **all** feeds defined in `tendrl.toml`.

**Solution**: Use filters to narrow results:
```bash
# Be specific with filters
tendrl_query --db ~/nostrdb --kinds 9735 --since $(date -d '1 day ago' +%s)
```

### Database corruption

**Symptom**: LMDB errors, crashes, or inconsistent data.

**Check**:
```bash
# Try querying
tendrl_query --db ~/.local/share/tendrl/nostrdb --limit 1
```

**Recovery**:
```bash
# Stop daemon
pkill tendrl_daemon

# Backup corrupted database (just in case)
cp -r ~/.local/share/tendrl/nostrdb ~/nostrdb.corrupt

# Delete database
rm -rf ~/.local/share/tendrl/nostrdb

# Restart daemon (will recreate)
tendrl_daemon --config tendrl.toml
```

**Prevention**: Always stop daemon with Ctrl+C or SIGTERM (not SIGKILL).

### High disk usage

**Symptom**: Database directory consuming lots of space.

**Check size**:
```bash
du -sh ~/.local/share/tendrl/nostrdb
```

**Solutions**:

1. **Reduce limits in tendrl.toml**:
```toml
local_queries = [
    { kinds = [9735], limit = 100 },  # Reduced from 500
]
```

2. **Delete and rebuild**:
```bash
pkill tendrl_daemon
rm -rf ~/.local/share/tendrl/nostrdb
tendrl_daemon --config tendrl.toml  # Refetch with new limits
```

3. **Use separate databases for different feeds**:
```bash
tendrl_daemon --config zaps.toml --db ~/nostrdb-zaps
tendrl_daemon --config articles.toml --db ~/nostrdb-articles
```

### Slow queries

**Symptom**: `tendrl_query` takes seconds to complete.

**Check**:
```bash
time tendrl_query --db ~/nostrdb --kinds 9735 --limit 100
```

**Common causes**:
- Large database (millions of events)
- No index on queried field
- Disk I/O bottleneck (slow drive)

**Solutions**:

1. **Use more specific filters**:
```bash
# Instead of
tendrl_query --db ~/nostrdb --limit 10000

# Use
tendrl_query --db ~/nostrdb --kinds 9735 --since $(date -d '1 day ago' +%s) --limit 100
```

2. **Ensure database is on SSD** (nostrdb benefits from fast random reads)

3. **Reduce limit**:
```bash
tendrl_query --db ~/nostrdb --kinds 9735 --limit 50  # Faster
```

## Advanced Usage

### Multiple Configurations

Run separate daemons for different feeds:

```bash
# Terminal 1: Zaps daemon
tendrl_daemon --config zaps.toml --db ~/nostrdb-zaps

# Terminal 2: Articles daemon
tendrl_daemon --config articles.toml --db ~/nostrdb-articles

# Query each separately
tendrl_query --db ~/nostrdb-zaps --kinds 9735
tendrl_query --db ~/nostrdb-articles --kinds 30023
```

### Database Migrations

When updating Tendrl, you may need to migrate nostrdb:

```bash
# Backup old database
cp -r ~/.local/share/tendrl/nostrdb ~/nostrdb.backup

# Stop daemon
pkill tendrl_daemon

# Run migration (if provided by update)
tendrl_migrate --db ~/.local/share/tendrl/nostrdb

# Restart daemon
tendrl_daemon --config tendrl.toml
```

### Custom Enrichers

Write enrichers in any language that reads JSONL:

**Rust**:
```rust
use std::io::{self, BufRead};
use serde_json::Value;

fn main() {
    let stdin = io::stdin();
    for line in stdin.lock().lines() {
        let event: Value = serde_json::from_str(&line.unwrap()).unwrap();
        println!("Event kind: {}", event["kind"]);
    }
}
```

**JavaScript (Node.js)**:
```javascript
const readline = require('readline');

const rl = readline.createInterface({
  input: process.stdin,
  output: process.stdout,
  terminal: false
});

rl.on('line', (line) => {
  const event = JSON.parse(line);
  console.log(`Event kind: ${event.kind}`);
});
```

**Go**:
```go
package main

import (
    "bufio"
    "encoding/json"
    "fmt"
    "os"
)

func main() {
    scanner := bufio.NewScanner(os.Stdin)
    for scanner.Scan() {
        var event map[string]interface{}
        json.Unmarshal(scanner.Bytes(), &event)
        fmt.Printf("Event kind: %.0f\n", event["kind"])
    }
}
```

### Monitoring

#### Check Daemon Status

```bash
# If using systemd
systemctl status tendrl

# If using process manager
ps aux | grep tendrl_daemon

# Check database size
du -sh ~/.local/share/tendrl/nostrdb
```

#### Database Statistics

```bash
# Count events by kind
tendrl_query --db ~/nostrdb --limit 1000000 | \
  jq -r '.kind' | sort | uniq -c | sort -rn

# Count total events
tendrl_query --db ~/nostrdb --limit 1000000 | wc -l

# Oldest event
tendrl_query --db ~/nostrdb --limit 1000000 | \
  jq -r '.created_at' | sort -n | head -1 | \
  xargs -I {} date -d @{}

# Newest event
tendrl_query --db ~/nostrdb --limit 1000000 | \
  jq -r '.created_at' | sort -n | tail -1 | \
  xargs -I {} date -d @{}
```

### Performance Tuning

#### Optimize Database Location

```bash
# Use SSD for best performance
tendrl_daemon --config tendrl.toml --db /mnt/ssd/nostrdb
```

#### Adjust Query Limits

```toml
# In tendrl.toml, reduce limits for faster queries
local_queries = [
    { kinds = [9735], limit = 100 },  # Lower = faster queries
]
```

#### Separate Read/Write Instances

```bash
# Writer: Single daemon feeding database
tendrl_daemon --config tendrl.toml --db ~/nostrdb

# Readers: Multiple queries (read-only, safe to parallelize)
tendrl_query --db ~/nostrdb --kinds 9735 &
tendrl_query --db ~/nostrdb --kinds 9802 &
tendrl_query --db ~/nostrdb --kinds 30023 &
```

---

**Next**: See [TENDRL_TOML.md](TENDRL_TOML.md) for detailed configuration options.
