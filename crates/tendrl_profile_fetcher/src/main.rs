//! Profile Fetcher - Automatically fetch missing kind-0 profiles
//!
//! Uses the same RelayPool infrastructure as tendrl_daemon to fetch
//! missing profiles from relays and store them in nostrdb.

use clap::Parser;
use enostr::{ClientMessage, Filter, RelayEvent, RelayMessage, RelayPool};
use nostrdb::{Config, Ndb, Transaction};
use std::path::PathBuf;
use std::time::Duration;
use tokio::time;
use tracing::{info, warn};

#[derive(Parser, Debug)]
#[command(name = "tendrl_profile_fetcher")]
#[command(about = "Fetch missing kind-0 profiles from relays")]
struct Args {
    /// Path to nostrdb database
    #[arg(long, default_value = "/home/user/.local/share/tendrl/nostrdb")]
    db_path: PathBuf,

    /// Relay URLs (comma-separated)
    #[arg(long, default_value = "wss://relay.damus.io,wss://relay.nostr.band,wss://nos.lol")]
    relays: String,

    /// Poll interval in seconds
    #[arg(long, default_value = "120")]
    poll_interval: u64,

    /// Batch size (profiles per fetch)
    #[arg(long, default_value = "20")]
    batch_size: usize,
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Initialize logging
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .init();

    let args = Args::parse();

    info!("====================================");
    info!("Tendrl Profile Fetcher");
    info!("====================================");
    info!("Database: {}", args.db_path.display());
    info!("Poll interval: {}s", args.poll_interval);
    info!("Batch size: {}", args.batch_size);

    // Initialize nostrdb
    let mut config = Config::new();
    config.set_ingester_threads(2);
    let ndb = Ndb::new(&args.db_path.to_string_lossy(), &config)?;
    info!("✓ Opened nostrdb");

    // Parse relay URLs
    let relay_urls: Vec<String> = args
        .relays
        .split(',')
        .map(|s| s.trim().to_string())
        .collect();

    info!("Relays: {}", relay_urls.join(", "));

    // Create relay pool
    let mut pool = RelayPool::new();
    for url in &relay_urls {
        if let Err(e) = pool.add_url(url.clone(), || {}) {
            warn!("Failed to add relay {}: {}", url, e);
        }
    }

    info!("✓ Relay pool created");
    info!("");

    // Main loop
    let mut poll_count = 0;
    loop {
        poll_count += 1;
        info!("Poll #{}", poll_count);

        // Find missing profiles
        let missing_pubkeys = find_missing_profiles(&ndb, args.batch_size)?;

        if missing_pubkeys.is_empty() {
            info!("✓ All profiles present");
        } else {
            info!(
                "Found {} authors without profiles, fetching...",
                missing_pubkeys.len()
            );

            // Fetch profiles from relays
            fetch_profiles(&mut pool, &ndb, &missing_pubkeys).await?;
        }

        info!("Sleeping {}s...", args.poll_interval);
        time::sleep(Duration::from_secs(args.poll_interval)).await;
    }
}

/// Find authors in the database who don't have kind-0 profiles
fn find_missing_profiles(ndb: &Ndb, limit: usize) -> Result<Vec<[u8; 32]>, Box<dyn std::error::Error>> {
    let txn = Transaction::new(ndb)?;

    // Get all kind-1 note authors
    let note_filter = Filter::new().kinds(vec![1]).limit(1000).build();
    let note_results = ndb.query(&txn, &[note_filter], 1000)?;

    // Extract unique pubkeys
    let mut author_pubkeys = std::collections::HashSet::new();
    for result in note_results {
        author_pubkeys.insert(*result.note.pubkey());
    }

    info!("Found {} unique note authors", author_pubkeys.len());

    // Check which authors are missing profiles
    let mut missing = Vec::new();
    for pubkey in author_pubkeys {
        if missing.len() >= limit {
            break;
        }

        // Check if profile exists
        let profile_filter = Filter::new()
            .kinds(vec![0])
            .authors(vec![&pubkey])
            .limit(1)
            .build();

        let profile_results = ndb.query(&txn, &[profile_filter], 1)?;

        if profile_results.is_empty() {
            missing.push(pubkey);
        }
    }

    info!("Missing profiles: {}", missing.len());
    Ok(missing)
}

/// Fetch profiles from relays using RelayPool
async fn fetch_profiles(
    pool: &mut RelayPool,
    ndb: &Ndb,
    pubkeys: &[[u8; 32]],
) -> Result<(), Box<dyn std::error::Error>> {
    if pubkeys.is_empty() {
        return Ok(());
    }

    // Create filter for these pubkeys
    let author_refs: Vec<&[u8; 32]> = pubkeys.iter().collect();
    let filter = Filter::new()
        .kinds(vec![0])
        .authors(author_refs)
        .limit(pubkeys.len() as u64)
        .build();

    // Subscribe to relays
    let subid = "profile_fetch";
    let req = ClientMessage::req(subid.to_string(), vec![filter]);

    pool.send(&req);
    info!("✓ Sent REQ to {} relays", pool.urls().len());

    // Collect events for a limited time
    let mut fetched_count = 0;
    let timeout = Duration::from_secs(10);
    let start = std::time::Instant::now();

    while start.elapsed() < timeout {
        // Poll relay pool for new events
        while let Some(pool_event) = pool.try_recv() {
            let relay_url = pool_event.relay;
            let relay_event = RelayEvent::from(&pool_event.event);

            match relay_event {
                RelayEvent::Opened => {
                    info!("Connected to {}", relay_url);
                }
                RelayEvent::Message(msg) => match msg {
                    RelayMessage::Event(_subid, event_json) => {
                        // Store in nostrdb
                        if let Err(e) = ndb.process_event(&event_json) {
                            warn!("Failed to store profile: {}", e);
                        } else {
                            fetched_count += 1;
                            // Parse to get pubkey for logging
                            if let Ok(ev) = serde_json::from_str::<serde_json::Value>(&event_json) {
                                if let Some(pubkey) = ev.get("pubkey").and_then(|p| p.as_str()) {
                                    info!("✓ Stored profile for {}...", &pubkey[..8]);
                                }
                            }
                        }
                    }
                    RelayMessage::Eose(_) => {
                        info!("EOSE from {}", relay_url);
                    }
                    RelayMessage::OK(result) => {
                        info!("OK from {}: {:?}", relay_url, result);
                    }
                    RelayMessage::Notice(msg) => {
                        warn!("Notice from {}: {}", relay_url, msg);
                    }
                },
                RelayEvent::Error(e) => {
                    warn!("Error from {}: {}", relay_url, e);
                }
                RelayEvent::Closed => {
                    info!("Relay closed: {}", relay_url);
                }
                RelayEvent::Other(s) => {
                    info!("Other event from {}: {:?}", relay_url, s);
                }
            }
        }

        // Small sleep to avoid busy-waiting
        tokio::time::sleep(Duration::from_millis(100)).await;

        // If we got all profiles, exit early
        if fetched_count >= pubkeys.len() {
            break;
        }
    }

    // Close subscription
    let close = ClientMessage::close(subid.to_string());
    pool.send(&close);

    info!(
        "✓ Fetched {} profiles in {:.1}s",
        fetched_count,
        start.elapsed().as_secs_f32()
    );

    Ok(())
}
