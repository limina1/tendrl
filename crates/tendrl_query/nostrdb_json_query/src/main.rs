//! # nostrdb-json-query
//!
//! A CLI tool for querying nostrdb and outputting JSONL (JSON Lines) format.
//!
//! ## Usage Examples
//!
//! Query for zaps:
//! ```bash
//! nostrdb-json-query --db ~/.local/share/notedeck/db --kinds 9735 --limit 50
//! ```
//!
//! Query for notes from specific author:
//! ```bash
//! nostrdb-json-query --db ~/.local/share/notedeck/db --kinds 1 --author <pubkey_hex> --limit 100
//! ```
//!
//! Query with time range:
//! ```bash
//! nostrdb-json-query --db ~/.local/share/notedeck/db --kinds 1 --since 1704067000 --until 1704070000
//! ```
//!
//! Query multiple kinds:
//! ```bash
//! nostrdb-json-query --db ~/.local/share/notedeck/db --kinds 1,7,9735 --limit 100
//! ```
//!
//! ## Output Format
//!
//! Each event is output as a single line of JSON matching the Nostr event specification:
//! ```json
//! {"id":"...", "pubkey":"...", "created_at":..., "kind":..., "content":"...", "tags":[...], "sig":"..."}
//! ```
//!
//! ## Pipeline Usage
//!
//! This tool is designed to be used in pipelines. For example, to process with jq:
//! ```bash
//! nostrdb-json-query --db ~/.local/share/notedeck/db --kinds 1 --limit 1000 | jq -r '.content'
//! ```

use anyhow::{Context, Result};
use clap::Parser;
use nostrdb::{Config, FilterBuilder, Ndb, Note, Transaction};
use serde::Serialize;

#[derive(Parser)]
#[command(name = "nostrdb-json-query")]
#[command(about = "Query nostrdb and output JSONL")]
#[command(version)]
struct Args {
    /// Path to nostrdb database directory
    #[arg(short, long)]
    db: String,

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
            tag.into_iter()
                .filter_map(|item| item.str().map(|s| s.to_string()))
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

    // Open nostrdb at the specified path
    let config = Config::new();
    let ndb = Ndb::new(&args.db, &config)
        .with_context(|| format!("Failed to open nostrdb at {}", args.db))?;

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
    let txn = Transaction::new(&ndb)
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
