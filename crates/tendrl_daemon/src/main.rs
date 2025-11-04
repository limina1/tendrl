//! # Tendrl Daemon
//!
//! A background daemon that fetches Nostr events for custom feeds defined in tendrl.toml
//! and stores them in a local nostrdb instance, making them available for Notedeck.
//!
//! ## Architecture
//!
//! The daemon operates with the following components:
//!
//! 1. **Configuration Loading**: Parses tendrl.toml to load feed definitions
//! 2. **NostrDB Initialization**: Opens a local database for event storage
//! 3. **Relay Pool**: Manages WebSocket connections to Nostr relays
//! 4. **Timeline Setup**: Creates timelines for each feed with hybrid local/remote filtering
//! 5. **Event Loop**: Continuously polls relays and local subscriptions
//!
//! ## Event Flow
//!
//! ```text
//! Relays (Remote) ──────┐
//!                       ├──> Event Loop ──> NostrDB ──> Notedeck
//! Local Subscriptions ──┘
//! ```
//!
//! The daemon uses Notedeck's Timeline and HybridFilter APIs to:
//! - Query existing events from nostrdb for backfill (local queries)
//! - Subscribe to relays for new events (remote filters)
//! - Process incoming events into nostrdb with deduplication
//! - Poll local subscriptions to update timeline views

use enostr::{RelayEvent, RelayMessage, RelayPool};
use nostrdb::{Config, Ndb, Transaction};
use std::collections::HashMap;
use std::path::PathBuf;
use std::time::Duration;
use tendrl_core::{
    Timeline, TimelineKind, TimelineTab, TendrlConfig,
    load_follow_list, build_hybrid_filter, NoteCache,
    UnknownIds, FilterState, HybridFilter,
};
use tracing::{debug, error, info, warn};

/// Main daemon entry point - initializes components and runs the event loop
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Initialize tracing for logging
    tracing_subscriber::fmt()
        .with_max_level(tracing::Level::INFO)
        .init();

    // Parse command-line arguments
    let config_path = parse_args()?;
    info!("Starting tendrl-daemon with config: {:?}", config_path);

    // Load TendrlConfig from the specified path
    let tendrl_config = TendrlConfig::load_from_path(&config_path)
        .map_err(|e| format!("Failed to load config: {}", e))?;

    info!(
        "Successfully loaded tendrl config with {} feeds",
        tendrl_config.feed_ids().len()
    );

    // Initialize nostrdb with 1GB mapsize and 2 ingester threads
    // NostrDB is a high-performance database optimized for Nostr event storage
    // - 2 ingester threads for concurrent event processing
    // - 1GB memory-mapped database for fast queries
    info!("Initializing nostrdb...");
    let db_path = get_db_path();
    let ndb_config = Config::new()
        .set_ingester_threads(2)
        .set_mapsize(1024 * 1024 * 1024); // 1GB

    let ndb = Ndb::new(&db_path, &ndb_config)
        .map_err(|e| format!("Failed to open nostrdb: {}", e))?;

    info!("nostrdb opened at: {}", db_path);

    // Load user's follow list if user pubkey is configured
    let follow_list = if let Some(user_pubkey) = tendrl_config.user_pubkey() {
        info!("Loading follow list for user: {}...", &user_pubkey[..8]);
        match load_follow_list(&ndb, &user_pubkey) {
            Ok(follows) => {
                info!("✓ Loaded {} follows", follows.len());
                Some(follows)
            }
            Err(e) => {
                warn!("Failed to load follow list: {}. Feeds with mode='follows' will use empty author list.", e);
                None
            }
        }
    } else {
        info!("No user pubkey configured - follow-filtered feeds will be disabled");
        None
    };

    // Create RelayPool and add default relays
    info!("Creating relay pool...");
    let mut pool = RelayPool::new();
    let relay_urls = get_default_relays();

    for url in &relay_urls {
        info!("Adding relay: {}", url);
        // Note: add_url requires a wakeup callback, we use a no-op for daemon mode
        if let Err(e) = pool.add_url(url.clone(), || {}) {
            error!("Failed to add relay {}: {}", url, e);
        }
    }

    // Initialize timeline tracking and note cache
    let mut timelines: HashMap<String, Timeline> = HashMap::new();
    let mut note_cache = NoteCache::default();
    let mut unknown_ids = UnknownIds::default();

    // Setup timelines for each feed in tendrl config
    // Each feed gets:
    // 1. A HybridFilter (local queries + remote filters)
    // 2. A Timeline for managing note views
    // 3. Local subscription for backfilling from nostrdb
    // 4. Remote subscription to all configured relays
    info!("Setting up timelines for feeds...");
    {
        let txn = Transaction::new(&ndb)?;

        for (feed_id, feed_def) in &tendrl_config.feed {
            info!(
                "Setting up feed '{}' (mode: {}): {}",
                feed_id, feed_def.mode, feed_def.name
            );

            // Clone feed pattern and inject authors if mode = "follows"
            let mut pattern = feed_def.pattern.clone();

            if feed_def.mode == "follows" {
                if let Some(ref follows) = follow_list {
                    info!("Injecting {} authors into feed '{}'", follows.len(), feed_id);

                    // Inject authors into all local queries
                    for query in &mut pattern.local_queries {
                        if query.authors.is_empty() {
                            query.authors = follows.clone();
                        }
                    }

                    // Inject authors into all remote filters
                    for filter in &mut pattern.remote_filters {
                        if filter.authors.is_empty() {
                            filter.authors = follows.clone();
                        }
                    }
                } else {
                    warn!("Feed '{}' has mode='follows' but no follow list available - using empty author filter", feed_id);
                }
            }

            // Build HybridFilter from the (possibly modified) pattern
            // This converts LocalQuery and RemoteFilter specs into nostr Filter objects
            let filter = build_hybrid_filter(&pattern);

            // Create a Generic timeline for this custom feed
            // Using a simple hash of the feed_id as the timeline identifier
            let timeline_hash = hash_feed_id(feed_id);
            let timeline_kind = TimelineKind::Generic(timeline_hash);

            // Create timeline with the hybrid filter
            let mut timeline = Timeline::new(
                timeline_kind,
                FilterState::ready_hybrid(filter.clone()),
                TimelineTab::full_tabs(),
            );

            // Setup local nostrdb subscription for backfill from existing events
            if let Err(e) = setup_timeline_local_subscription(
                &ndb,
                &txn,
                &mut timeline,
                &mut note_cache,
                &mut unknown_ids,
                &filter,
            ) {
                error!("Failed to setup local subscription for '{}': {}", feed_id, e);
            }

            // Setup remote relay subscription - send REQ to all relays
            let sub_id = generate_subscription_id(feed_id);
            info!("Subscribing to relays with sub_id: {}", sub_id);

            for relay in &mut pool.relays {
                if let Err(e) = relay.subscribe(sub_id.clone(), filter.remote().to_vec()) {
                    error!("Failed to subscribe to relay {}: {}", relay.url(), e);
                }
            }

            // Store the subscription ID in the timeline
            timeline.subscription.force_add_remote(sub_id);

            timelines.insert(feed_id.clone(), timeline);
            info!("Timeline '{}' setup complete", feed_id);
        }
    }

    info!("All timelines initialized. Starting main event loop...");

    // Main event loop - poll relays and local subscriptions
    run_event_loop(&ndb, &mut pool, &mut timelines, &mut note_cache, &mut unknown_ids).await?;

    Ok(())
}

/// Main event loop - polls relay pool and local nostrdb subscriptions
async fn run_event_loop(
    ndb: &Ndb,
    pool: &mut RelayPool,
    timelines: &mut HashMap<String, Timeline>,
    note_cache: &mut NoteCache,
    unknown_ids: &mut UnknownIds,
) -> Result<(), Box<dyn std::error::Error>> {
    let mut event_count = 0u64;
    let mut poll_count = 0u64;

    loop {
        poll_count += 1;

        // Poll relay pool for new events from relays
        while let Some(pool_event) = pool.try_recv() {
            let relay_url = pool_event.relay;
            let relay_event = RelayEvent::from(&pool_event.event);

            match relay_event {
                RelayEvent::Opened => {
                    info!("Connected to relay: {}", relay_url);
                }

                RelayEvent::Message(msg) => match msg {
                    RelayMessage::Event(_subid, event_json) => {
                        // Parse the event JSON and process into nostrdb
                        match process_relay_event(ndb, relay_url, event_json) {
                            Ok(_) => {
                                event_count += 1;
                                if event_count % 100 == 0 {
                                    info!("Processed {} events so far", event_count);
                                }
                            }
                            Err(e) => {
                                debug!("Failed to process event from {}: {}", relay_url, e);
                            }
                        }
                    }

                    RelayMessage::Eose(subid) => {
                        info!("EOSE received from {} for subscription: {}", relay_url, subid);
                    }

                    RelayMessage::Notice(notice) => {
                        info!("Notice from {}: {}", relay_url, notice);
                    }

                    RelayMessage::OK(result) => {
                        debug!("OK response from {}: {:?}", relay_url, result);
                    }
                },

                RelayEvent::Error(err) => {
                    error!("Relay error from {}: {}", relay_url, err);
                }

                RelayEvent::Closed => {
                    warn!("Connection closed to relay: {}", relay_url);
                }

                RelayEvent::Other(_) => {
                    debug!("Other event from relay: {}", relay_url);
                }
            }
        }

        // Poll local nostrdb subscriptions for new notes
        // This checks if any new events that match our local filters have arrived
        {
            let txn = Transaction::new(ndb)?;

            for (feed_id, timeline) in timelines.iter_mut() {
                if let Err(e) = timeline.poll_notes_into_view(
                    ndb,
                    &txn,
                    unknown_ids,
                    note_cache,
                    false, // reversed = false for chronological order
                ) {
                    debug!("Error polling timeline '{}': {}", feed_id, e);
                }
            }
        }

        // Log periodic status
        if poll_count % 1000 == 0 {
            debug!(
                "Event loop stats - polls: {}, events processed: {}",
                poll_count, event_count
            );
        }

        // Small sleep to avoid busy-waiting and reduce CPU usage
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
}

/// Process a relay event by parsing and storing in nostrdb
fn process_relay_event(
    ndb: &Ndb,
    relay_url: &str,
    event_json: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    // Store the event directly in nostrdb (as JSON string)
    // nostrdb will handle deduplication automatically
    ndb.process_event(event_json)?;

    debug!("Stored event from relay {}", relay_url);

    Ok(())
}

/// Setup local nostrdb subscription for a timeline (for backfill from local db)
fn setup_timeline_local_subscription(
    ndb: &Ndb,
    txn: &Transaction,
    timeline: &mut Timeline,
    note_cache: &mut NoteCache,
    unknown_ids: &mut UnknownIds,
    filter: &HybridFilter,
) -> Result<(), Box<dyn std::error::Error>> {
    // Only subscribe locally if the timeline supports it
    if !timeline.kind.should_subscribe_locally() {
        return Ok(());
    }

    // Setup local subscription using the hybrid filter's local component
    timeline.subscription.try_add_local(ndb, filter);

    debug!(
        "Querying local nostrdb for timeline subscription: {:?}",
        timeline.subscription
    );

    // Query existing notes from nostrdb to backfill the timeline
    let note_refs = {
        let mut all_notes = Vec::new();

        for package in filter.local().packages {
            let limit = package
                .filters
                .iter()
                .map(|f| f.limit().unwrap_or(1) as i32)
                .sum();

            debug!("Querying local db with limit: {}", limit);

            let results = ndb.query(txn, package.filters, limit)?;
            all_notes.extend(results);
        }

        all_notes
    };

    debug!("Found {} existing notes in local db", note_refs.len());

    // Insert the notes into the timeline views
    if !note_refs.is_empty() {
        let note_keys: Vec<_> = note_refs.iter().map(|nr| nr.note_key).collect();
        timeline.insert(&note_keys, ndb, txn, unknown_ids, note_cache, false)?;
        info!(
            "Backfilled timeline with {} notes from local db",
            note_refs.len()
        );
    }

    Ok(())
}

/// Generate a unique subscription ID for a feed
fn generate_subscription_id(feed_id: &str) -> String {
    format!("tendrl_{}", feed_id)
}

/// Hash a feed ID to generate a timeline identifier
fn hash_feed_id(feed_id: &str) -> u64 {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};

    let mut hasher = DefaultHasher::new();
    feed_id.hash(&mut hasher);
    hasher.finish()
}

/// Get the database path for nostrdb
fn get_db_path() -> String {
    // Use XDG_DATA_HOME or ~/.local/share for Linux
    if let Some(data_home) = std::env::var_os("XDG_DATA_HOME") {
        format!("{}/tendrl/nostrdb", data_home.to_string_lossy())
    } else if let Some(home) = std::env::var_os("HOME") {
        format!("{}/.local/share/tendrl/nostrdb", home.to_string_lossy())
    } else {
        // Fallback to current directory
        "./tendrl_data/nostrdb".to_string()
    }
}

/// Get default relay list
fn get_default_relays() -> Vec<String> {
    vec![
        "wss://relay.damus.io".to_string(),
        "wss://nos.lol".to_string(),
        "wss://nostr.wine".to_string(),
        "wss://relay.nostr.band".to_string(),
    ]
}

/// Parse command-line arguments
fn parse_args() -> Result<PathBuf, Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().collect();

    // Default config path
    let mut config_path = PathBuf::from("tendrl.toml");

    // Parse --config argument
    for i in 0..args.len() {
        if args[i] == "--config" && i + 1 < args.len() {
            config_path = PathBuf::from(&args[i + 1]);
            break;
        }
    }

    // Verify the config file exists
    if !config_path.exists() {
        error!("Config file not found: {:?}", config_path);
        return Err(format!("Config file not found: {:?}", config_path).into());
    }

    Ok(config_path)
}
