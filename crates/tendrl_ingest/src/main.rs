//! tendrl_ingest - Ingest Nostr events into nostrdb
//!
//! Reads JSON events from stdin and stores them in nostrdb.
//! Used by profile_daemon to store fetched profiles.

use anyhow::Result;
use clap::Parser;
use nostrdb::{Config, Ndb};
use std::io::{self, BufRead};

#[derive(Parser)]
struct Args {
    /// Path to nostrdb database
    #[arg(long, default_value = "/home/user/.local/share/tendrl/nostrdb")]
    db: String,
}

fn main() -> Result<()> {
    let args = Args::parse();

    // Open nostrdb
    let config = Config::new();
    let ndb = Ndb::new(&args.db, &config)?;

    // Read JSON events from stdin
    let stdin = io::stdin();
    for line in stdin.lock().lines() {
        let event_json = line?;
        if event_json.trim().is_empty() {
            continue;
        }

        // Process event into nostrdb
        match ndb.process_event(&event_json) {
            Ok(_) => {
                eprintln!("✓ Stored event");
            }
            Err(e) => {
                eprintln!("✗ Failed to store event: {}", e);
            }
        }
    }

    Ok(())
}
