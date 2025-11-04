//! # tendrl-query
//!
//! A CLI tool for querying nostrdb and outputting JSONL (JSON Lines) format.
//! Supports both independent stream mode and per-event enrichment mode.
//!
//! ## Usage Examples
//!
//! ### Independent Stream Mode (default)
//!
//! Query for zaps:
//! ```bash
//! tendrl_query --db ~/.local/share/tendrl/nostrdb --kinds 9735 --limit 50
//! ```
//!
//! Query for notes from specific author:
//! ```bash
//! tendrl_query --db ~/.local/share/tendrl/nostrdb --kinds 1 --author <pubkey_hex> --limit 100
//! ```
//!
//! Query multiple kinds:
//! ```bash
//! tendrl_query --db ~/.local/share/tendrl/nostrdb --kinds 1,7,9735 --limit 100
//! ```
//!
//! ### Per-Event Enrichment Mode
//!
//! Query with enrichment (requires tendrl.toml):
//! ```bash
//! tendrl_query --feed notes --config tendrl.toml --limit 100
//! ```
//!
//! This reads the feed configuration from tendrl.toml and enriches each root event
//! with its dependencies (reactions, zaps, author profiles, etc.)
//!
//! ## Output Format
//!
//! ### Independent Mode
//! Each event is output as a single line of JSON:
//! ```json
//! {"id":"...", "pubkey":"...", "created_at":..., "kind":..., "content":"...", "tags":[...], "sig":"..."}
//! ```
//!
//! ### Enrichment Mode
//! Each root event includes an `_enriched` field with dependency data:
//! ```json
//! {
//!   "id":"...", "kind":1, "content":"...",
//!   "_enriched": {
//!     "author": {"id":"...", "kind":0, "content":"..."},
//!     "stats": {"reactions": {"count": 42}, "zaps": {"count": 5, "total_sats": 21000}}
//!   }
//! }
//! ```

use anyhow::{Context, Result};
use clap::Parser;
use nostrdb::{Config, FilterBuilder, Ndb, Note, Transaction};
use serde::Serialize;
use std::path::PathBuf;
use tendrl_core::{EnrichmentEngine, TendrlConfig};

#[derive(Parser)]
#[command(name = "tendrl-query")]
#[command(about = "Query nostrdb and output JSONL with optional enrichment")]
#[command(version)]
struct Args {
    /// Path to nostrdb database directory
    #[arg(short, long)]
    db: Option<String>,

    /// Filter by event kind(s), comma-separated
    #[arg(short, long)]
    kinds: Option<String>,

    /// Filter by author pubkey (hex)
    #[arg(short, long)]
    author: Option<String>,

    /// Maximum number of results
    #[arg(short, long, default_value = "100")]
    limit: usize,

    /// Filter events since timestamp (unix epoch)
    #[arg(long)]
    since: Option<u64>,

    /// Filter events until timestamp (unix epoch)
    #[arg(long)]
    until: Option<u64>,

    /// Feed name for per-event enrichment mode (requires --config)
    #[arg(short, long)]
    feed: Option<String>,

    /// Path to tendrl.toml configuration file (required for --feed)
    #[arg(short, long)]
    config: Option<PathBuf>,
}

/// Nostr event struct matching the standard JSON format
#[derive(Serialize)]
struct NostrEvent {
    id: String,
    pubkey: String,
    created_at: u64,
    kind: u32,
    tags: Vec<Vec<String>>,
    content: String,
    sig: String,
}

/// Convert a nostrdb Note to a NostrEvent for JSON serialization
fn note_to_event(note: &Note) -> NostrEvent {
    // Extract note ID as hex
    let id = hex::encode(note.id());

    // Extract pubkey as hex
    let pubkey = hex::encode(note.pubkey());

    // Extract created_at timestamp
    let created_at = note.created_at();

    // Extract kind
    let kind = note.kind();

    // Extract tags
    let tags: Vec<Vec<String>> = note
        .tags()
        .iter()
        .map(|tag| {
            (0..tag.count())
                .filter_map(|i| {
                    tag.get(i).map(|ndb_str| {
                        // NdbStr can be either a string or an ID (32-byte array)
                        if let Some(s) = ndb_str.str() {
                            s.to_string()
                        } else if let Some(id) = ndb_str.id() {
                            hex::encode(id)
                        } else {
                            String::new()
                        }
                    })
                })
                .collect()
        })
        .collect();

    // Extract content
    let content = note.content().to_string();

    // Extract signature as hex
    let sig = hex::encode(note.sig());

    NostrEvent {
        id,
        pubkey,
        created_at,
        kind,
        tags,
        content,
        sig,
    }
}

/// Parse comma-separated kinds string into Vec<u64>
fn parse_kinds(kinds_str: &str) -> Result<Vec<u64>> {
    kinds_str
        .split(',')
        .map(|s| {
            s.trim()
                .parse::<u64>()
                .with_context(|| format!("Invalid kind value: {}", s))
        })
        .collect()
}

/// Parse hex pubkey string into 32-byte array
fn parse_pubkey(pubkey_hex: &str) -> Result<[u8; 32]> {
    let pubkey_hex = pubkey_hex.trim();

    if pubkey_hex.len() != 64 {
        anyhow::bail!("Pubkey must be 64 hex characters (32 bytes)");
    }

    let bytes = hex::decode(pubkey_hex)
        .with_context(|| format!("Invalid hex pubkey: {}", pubkey_hex))?;

    let mut array = [0u8; 32];
    array.copy_from_slice(&bytes);
    Ok(array)
}

fn main() -> Result<()> {
    // Parse command line arguments
    let args = Args::parse();

    // Determine database path
    let db_path = if let Some(db) = &args.db {
        db.clone()
    } else if let Some(config_path) = &args.config {
        // Default to ~/.local/share/tendrl/nostrdb if config specified
        dirs::data_local_dir()
            .context("Could not determine local data directory")?
            .join("tendrl")
            .join("nostrdb")
            .to_str()
            .context("Invalid path")?
            .to_string()
    } else {
        anyhow::bail!("Either --db or --config must be specified");
    };

    // Open nostrdb at the specified path
    let config = Config::new();
    let ndb = Ndb::new(&db_path, &config)
        .with_context(|| format!("Failed to open nostrdb at {}", db_path))?;

    // Check if we're in enrichment mode
    if let Some(feed_name) = &args.feed {
        // Per-event enrichment mode
        query_with_enrichment(&ndb, &args, feed_name)?;
    } else {
        // Independent stream mode
        query_independent(&ndb, &args)?;
    }

    Ok(())
}

/// Independent stream mode: query and output events as-is
fn query_independent(ndb: &Ndb, args: &Args) -> Result<()> {
    // Build filter from arguments
    let mut filter_builder = FilterBuilder::new();

    // Add kinds filter if specified
    if let Some(kinds_str) = &args.kinds {
        let kinds = parse_kinds(kinds_str)?;
        filter_builder = filter_builder.kinds(kinds);
    }

    // Add author filter if specified
    if let Some(author_hex) = &args.author {
        let pubkey = parse_pubkey(author_hex)?;
        filter_builder = filter_builder.authors(vec![&pubkey]);
    }

    // Add time range filters if specified
    if let Some(since) = args.since {
        filter_builder = filter_builder.since(since);
    }

    if let Some(until) = args.until {
        filter_builder = filter_builder.until(until);
    }

    // Add limit
    filter_builder = filter_builder.limit(args.limit as u64);

    // Build the filter
    let filter = filter_builder.build();

    // Execute query
    let txn = Transaction::new(ndb)
        .context("Failed to create transaction")?;

    let results = ndb.query(&txn, &[filter], args.limit as i32)
        .context("Query failed")?;

    // Process each result and output as JSONL
    for query_result in results {
        let event = note_to_event(&query_result.note);

        let json = serde_json::to_string(&event)
            .context("Failed to serialize event to JSON")?;

        println!("{}", json);
    }

    Ok(())
}

/// Per-event enrichment mode: query root events and enrich with dependencies
fn query_with_enrichment(ndb: &Ndb, args: &Args, feed_name: &str) -> Result<()> {
    // Load tendrl config
    let config_path = args.config.as_ref()
        .context("--config is required when using --feed")?;

    let tendrl_config = TendrlConfig::load_from_path(config_path)
        .context("Failed to load tendrl.toml")?;

    // Get feed definition
    let feed = tendrl_config.get_feed(feed_name)
        .with_context(|| format!("Feed '{}' not found in config", feed_name))?;

    // Check if feed has root config (required for enrichment)
    let root_config = feed.root.as_ref()
        .with_context(|| format!("Feed '{}' has no root configuration (not enrichable)", feed_name))?;

    // Build filter for root events only
    let mut filter_builder = FilterBuilder::new();
    filter_builder = filter_builder.kinds(feed.display_kinds.clone());

    // Add time range filters if specified
    if let Some(since) = args.since {
        filter_builder = filter_builder.since(since);
    }

    if let Some(until) = args.until {
        filter_builder = filter_builder.until(until);
    }

    // Add limit
    filter_builder = filter_builder.limit(args.limit as u64);

    // Build the filter
    let filter = filter_builder.build();

    // Execute query for root events
    let txn = Transaction::new(ndb)
        .context("Failed to create transaction")?;

    let root_events = ndb.query(&txn, &[filter], args.limit as i32)
        .context("Query failed")?;

    // Create enrichment engine
    let engine = EnrichmentEngine::new(ndb, root_config);

    // Enrich each root event and output
    for query_result in root_events {
        match engine.enrich_event(&query_result.note, &txn) {
            Ok(enriched) => {
                let json = serde_json::to_string(&enriched)
                    .context("Failed to serialize enriched event")?;
                println!("{}", json);
            }
            Err(e) => {
                eprintln!("Warning: Failed to enrich event {}: {}", hex::encode(query_result.note.id()), e);
            }
        }
    }

    Ok(())
}
