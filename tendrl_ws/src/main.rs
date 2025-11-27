//! # Tendrl Web Server
//!
//! HTTP server that serves Nostr publications from nostrdb with Jinja-style templates.
//!
//! ## Architecture
//!
//! ```text
//! Browser ──HTTP──> tendrl_ws (axum + tera) ──> nostrdb (LMDB)
//! ```
//!
//! ## Routes
//!
//! - `/` - Redirect to /feed
//! - `/feed` - Publication feed grid (kind 30040 events)
//! - `/publication/:type/:identifier` - Single publication view (gc-alexandria compatible)
//!   - `/publication/naddr/<naddr>` - By naddr (bech32 encoded kind:pubkey:d-tag)
//!   - `/publication/id/<hex-id>` - By event ID
//!   - `/publication/d/<d-tag>` - By d-tag only
//!   - `/publication/nevent/<nevent>` - By nevent
//! - `/visualize` - Event statistics and visualizations
//! - `/static/*` - Static assets (CSS, JS)

mod config;
mod nostr_utils;
mod query;
mod relay;

use std::collections::{HashMap, HashSet};
use std::error::Error;
use std::io::Write;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::Arc;

use anyhow::Result;
use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    response::{Html, IntoResponse, Redirect, Response},
    routing::get,
    Router,
};
use nostrdb::{Config as NdbConfig, Ndb};
use serde::Deserialize;
use serde_json::{json, Value};
use tera::{Context, Tera};
use tower_http::services::ServeDir;
use tracing::{debug, error, info, warn};

/// Shared application state
pub struct AppState {
    pub ndb: Ndb,
    pub tera: Tera,
    pub config: config::Config,
}

// Ndb is thread-safe (uses LMDB with concurrent readers)
unsafe impl Send for AppState {}
unsafe impl Sync for AppState {}

#[tokio::main]
async fn main() -> Result<()> {
    // Initialize tracing
    tracing_subscriber::fmt()
        .with_max_level(tracing::Level::INFO)
        .init();

    let args = parse_args();
    info!("Starting Tendrl web server on {}", args.bind_addr);

    // Initialize nostrdb
    let db_path = get_db_path();
    info!("Opening nostrdb at: {}", db_path);

    if let Some(parent) = std::path::Path::new(&db_path).parent() {
        std::fs::create_dir_all(parent)?;
    }

    let ndb_config = NdbConfig::new()
        .set_ingester_threads(2)
        .set_mapsize(1024 * 1024 * 1024);

    let ndb = Ndb::new(&db_path, &ndb_config)
        .map_err(|e| anyhow::anyhow!("Failed to open nostrdb: {}", e))?;

    info!("nostrdb opened successfully");

    // Load tendrl configuration
    let tendrl_config = config::Config::load_default().unwrap_or_else(|e| {
        warn!("Failed to load tendrl.toml: {}. Using defaults.", e);
        config::Config::default()
    });
    info!("Loaded {} feed configurations", tendrl_config.feed.len());
    for (name, feed) in &tendrl_config.feed {
        info!("  - {} (kind {}): {}", name, feed.root.kind, feed.description);
    }

    // Initialize Tera templates
    let template_dir = get_template_dir();
    info!("Loading templates from: {}", template_dir.display());
    info!("Template dir exists: {}", template_dir.exists());

    let glob_pattern = format!("{}/**/*.html", template_dir.display());
    info!("Tera glob pattern: {}", glob_pattern);

    let tera = match Tera::new(&glob_pattern) {
        Ok(t) => t,
        Err(e) => {
            error!("Template parsing error: {}", e);
            return Err(anyhow::anyhow!("Failed to load templates: {}", e));
        }
    };

    // Log all found template names
    let template_names: Vec<_> = tera.get_template_names().collect();
    info!("Loaded {} templates: {:?}", template_names.len(), template_names);

    // Create shared state
    let state = Arc::new(AppState {
        ndb,
        tera,
        config: tendrl_config,
    });

    // Fetch initial publications from relays (config-driven)
    for feed in state.config.startup_feeds() {
        info!("Fetching {} feed (kind {}) from relays...", feed.name, feed.root.kind);
        let relays = state.config.relays_for_feed(&feed.name);
        let limit = state.config.global.default_limit;

        match relay::fetch_publications_simple(&state.ndb, limit).await {
            Ok(count) => info!("Fetched {} events for {} feed", count, feed.name),
            Err(e) => error!("Failed to fetch {} feed: {}", feed.name, e),
        }
    }

    // Also fetch publications if no startup feeds defined
    if state.config.startup_feeds().is_empty() {
        info!("No startup feeds configured, fetching default publications...");
        match relay::fetch_publications_simple(&state.ndb, 200).await {
            Ok(count) => info!("Fetched {} publications from relays", count),
            Err(e) => error!("Failed to fetch from relays: {}", e),
        }
    }

    // Build router
    let static_dir = get_static_dir();
    info!("Serving static files from: {}", static_dir.display());

    let app = Router::new()
        .route("/", get(|| async { Redirect::permanent("/feed") }))
        .route("/feed", get(feed_handler))
        // gc-alexandria compatible routes: /publication/:type/:identifier
        .route("/publication/:type/:identifier", get(publication_handler))
        .route("/visualize", get(visualize_handler))
        .route("/api/feed", get(api_feed_handler))
        .route("/api/refresh", get(refresh_handler))
        .route("/api/load-more", get(load_more_handler))
        .route("/api/events", get(api_events_handler))
        // gc-alexandria compatible API routes
        .route("/api/publication/:type/:identifier", get(api_publication_handler))
        .route("/api/section/:address", get(api_section_handler))
        .nest_service("/static", ServeDir::new(static_dir))
        .with_state(state);

    // Start server
    let addr: SocketAddr = args.bind_addr.parse()?;
    let listener = tokio::net::TcpListener::bind(addr).await?;
    info!("Tendrl web server listening on http://{}", addr);

    axum::serve(listener, app).await?;

    Ok(())
}

// =============================================================================
// Query Parameters
// =============================================================================

#[derive(Deserialize)]
struct FeedQuery {
    kind: Option<u64>,
    sort: Option<String>,
    page: Option<u32>,
}

#[derive(Deserialize)]
#[allow(dead_code)]
struct VisualizeQuery {
    range: Option<String>,
    kind: Option<String>,
    group: Option<String>,
}

// =============================================================================
// Route Handlers
// =============================================================================

/// Build the address string for an addressable event (kind:pubkey:d-tag)
fn build_address(kind: u64, pubkey: &str, d_tag: &str) -> String {
    format!("{}:{}:{}", kind, pubkey, d_tag)
}

/// Extract all child addresses from an event's a-tags
/// Returns Vec of (kind, pubkey, d_tag) tuples
fn extract_child_addresses(event: &Value) -> Vec<String> {
    extract_a_tags(event)
        .into_iter()
        .map(|(kind, pubkey, d_tag)| build_address(kind, &pubkey, &d_tag))
        .collect()
}

/// Feed page - grid of publications
/// Only shows ROOT-LEVEL publications (not referenced by other 30040s' a-tags)
/// Now filters to ONLY kind 30040 events with pagination support
async fn feed_handler(
    State(state): State<Arc<AppState>>,
    Query(params): Query<FeedQuery>,
) -> Response {
    let mut context = Context::new();
    context.insert("active_page", "feed");

    // Only show kind 30040 (publication indexes), not 30041 (sections)
    let kinds: Vec<u64> = vec![30040];

    let filter = json!({
        "kinds": kinds,
        "limit": 500  // Fetch more to allow filtering and pagination
    });

    // Query nostrdb
    match query::query_local(&state.ndb, &[filter]) {
        Ok(events) => {
            // Build a set of all addresses that are children of other publications
            // These should be filtered out from the root feed
            let mut child_addresses: HashSet<String> = HashSet::new();

            for event in &events {
                let kind = event.get("kind").and_then(|v| v.as_u64()).unwrap_or(0);
                // Only 30040 (index) events can have children via a-tags
                if kind == 30040 {
                    for addr in extract_child_addresses(event) {
                        child_addresses.insert(addr);
                    }
                }
            }

            // Filter to only root-level publications (not children of other publications)
            let filtered_events: Vec<Value> = events
                .into_iter()
                .filter(|event| {
                    let kind = event.get("kind").and_then(|v| v.as_u64()).unwrap_or(0);
                    let pubkey = event.get("pubkey").and_then(|v| v.as_str()).unwrap_or("");
                    let d_tag = extract_tag(event, "d").unwrap_or_default();

                    // Build this event's address
                    let my_address = build_address(kind, pubkey, &d_tag);

                    // Keep if NOT in child_addresses (i.e., it's a root)
                    !child_addresses.contains(&my_address)
                })
                .collect();

            // Pagination: 20 per page
            let page_size = 20;
            let page = params.page.unwrap_or(1).max(1);
            let offset = ((page - 1) * page_size) as usize;
            let total_count = filtered_events.len();
            let has_more = offset + (page_size as usize) < total_count;

            let publications: Vec<Value> = filtered_events
                .into_iter()
                .skip(offset)
                .take(page_size as usize)
                .map(|event| {
                    let d_tag = extract_tag(&event, "d").unwrap_or_default();
                    let title = extract_tag(&event, "title")
                        .or_else(|| extract_tag(&event, "name"))
                        .or_else(|| if !d_tag.is_empty() { Some(d_tag.clone()) } else { None })
                        .unwrap_or_else(|| "Untitled".to_string());
                    let summary = extract_tag(&event, "summary");
                    let image = extract_tag(&event, "image");
                    let author_name = extract_tag(&event, "author");
                    let version = extract_tag(&event, "version");
                    let pubkey = event.get("pubkey").and_then(|v| v.as_str()).unwrap_or("");
                    let kind = event.get("kind").and_then(|v| v.as_u64()).unwrap_or(0);
                    let created_at = event.get("created_at").and_then(|v| v.as_i64()).unwrap_or(0);
                    let id = event.get("id").and_then(|v| v.as_str()).unwrap_or("");

                    // Count children (sections) from a-tags
                    let children = extract_child_addresses(&event);
                    let section_count = children.len();

                    // Build proper naddr (bech32 encoded) or fall back to event ID
                    let (url_type, url_identifier) = if !d_tag.is_empty() && !pubkey.is_empty() {
                        match nostr_utils::encode_naddr(kind, pubkey, &d_tag, &[]) {
                            Ok(naddr) => ("naddr".to_string(), naddr),
                            Err(_) => ("id".to_string(), id.to_string()),
                        }
                    } else {
                        ("id".to_string(), id.to_string())
                    };

                    // Escape event JSON for safe embedding in HTML attributes
                    let event_json_escaped = event.to_string()
                        .replace('&', "&amp;")
                        .replace('<', "&lt;")
                        .replace('>', "&gt;")
                        .replace('"', "&quot;")
                        .replace('\'', "&#x27;");

                    json!({
                        "id": id,
                        "title": title,
                        "summary": summary,
                        "image": image,
                        "author_name": author_name,
                        "version": version,
                        "pubkey": pubkey,
                        "kind": kind,
                        "created_at": created_at,
                        "url_type": url_type,
                        "url_identifier": url_identifier,
                        "section_count": section_count,
                        "event_json": event_json_escaped
                    })
                })
                .collect();

            context.insert("publications", &publications);
            context.insert("total_count", &total_count);
            context.insert("displayed_count", &publications.len());
            context.insert("current_page", &page);
            context.insert("kind_filter", &params.kind);
            context.insert("sort", &params.sort.unwrap_or_else(|| "recent".to_string()));
            context.insert("has_more", &has_more);
        }
        Err(e) => {
            error!("Query failed: {}", e);
            context.insert("publications", &Vec::<Value>::new());
            context.insert("total_count", &0);
            context.insert("error", &e.to_string());
        }
    }

    render_template(&state.tera, "feed.html", context)
}

/// Path parameters for publication routes (gc-alexandria compatible)
#[derive(Deserialize)]
struct PublicationPath {
    /// The identifier type: naddr, nevent, id, or d
    #[serde(rename = "type")]
    id_type: String,
    /// The actual identifier value
    identifier: String,
}

/// Single publication view (gc-alexandria compatible URL scheme)
/// Routes: /publication/naddr/<naddr>, /publication/id/<id>, /publication/d/<d-tag>, /publication/nevent/<nevent>
async fn publication_handler(
    State(state): State<Arc<AppState>>,
    Path(path): Path<PublicationPath>,
) -> Response {
    let mut context = Context::new();
    context.insert("active_page", "publication");

    // Parse identifier based on type
    let event = match path.id_type.as_str() {
        "naddr" => {
            // Decode naddr and query by kind/author/d-tag
            match nostr_utils::decode_naddr(&path.identifier) {
                Ok(naddr_data) => {
                    let filter = json!({
                        "kinds": [naddr_data.kind],
                        "authors": [naddr_data.pubkey],
                        "#d": [naddr_data.identifier],
                        "limit": 1
                    });
                    query::query_local(&state.ndb, &[filter])
                        .ok()
                        .and_then(|e| e.into_iter().next())
                }
                Err(e) => {
                    warn!("Failed to decode naddr {}: {}", path.identifier, e);
                    None
                }
            }
        }
        "nevent" => {
            // Decode nevent and query by event ID
            match nostr_utils::decode_nevent(&path.identifier) {
                Ok(nevent_data) => {
                    let filter = json!({
                        "ids": [nevent_data.id],
                        "limit": 1
                    });
                    query::query_local(&state.ndb, &[filter])
                        .ok()
                        .and_then(|e| e.into_iter().next())
                }
                Err(e) => {
                    warn!("Failed to decode nevent {}: {}", path.identifier, e);
                    None
                }
            }
        }
        "id" => {
            // Query by event ID directly
            let filter = json!({
                "ids": [path.identifier.clone()],
                "limit": 1
            });
            query::query_local(&state.ndb, &[filter])
                .ok()
                .and_then(|e| e.into_iter().next())
        }
        "d" => {
            // Query by d-tag only (any author)
            let filter = json!({
                "kinds": [30040, 30041],
                "#d": [path.identifier.clone()],
                "limit": 1
            });
            query::query_local(&state.ndb, &[filter])
                .ok()
                .and_then(|e| e.into_iter().next())
        }
        _ => {
            context.insert("error", &format!("Unknown identifier type: {}", path.id_type));
            context.insert("title", "Invalid Request");
            context.insert("sections", &Vec::<Value>::new());
            return render_template(&state.tera, "publication.html", context);
        }
    };

    match event {
        Some(event) => {
            let title = extract_tag(&event, "title")
                .or_else(|| extract_tag(&event, "name"))
                .unwrap_or_else(|| "Untitled Publication".to_string());
            let summary = extract_tag(&event, "summary");
            let d_tag = extract_tag(&event, "d").unwrap_or_default();
            let pubkey = event.get("pubkey").and_then(|v| v.as_str()).unwrap_or("");
            let kind = event.get("kind").and_then(|v| v.as_u64()).unwrap_or(0);
            let created_at = event.get("created_at").and_then(|v| v.as_i64()).unwrap_or(0);
            let event_id = event.get("id").and_then(|v| v.as_str()).unwrap_or("");

            // Generate proper naddr for this event
            let naddr = if !d_tag.is_empty() && !pubkey.is_empty() {
                nostr_utils::encode_naddr(kind, pubkey, &d_tag, &[])
                    .unwrap_or_else(|_| format!("{}:{}:{}", kind, pubkey, d_tag))
            } else {
                event_id.to_string()
            };

            context.insert("title", &title);
            context.insert("summary", &summary);
            context.insert("pubkey", &pubkey);
            context.insert("kind", &kind);
            context.insert("created_at", &created_at);
            context.insert("naddr", &naddr);
            context.insert("url_type", &path.id_type);
            context.insert("url_identifier", &path.identifier);
            context.insert("index_event_json", &event.to_string());

            // Extract sections from e-tags (for kind 30040 index)
            let sections: Vec<Value> = extract_e_tags(&event)
                .iter()
                .enumerate()
                .map(|(i, e_tag)| {
                    // Try to fetch each section
                    let section_filter = json!({
                        "ids": [e_tag],
                        "limit": 1
                    });

                    let section_event = query::query_local(&state.ndb, &[section_filter])
                        .ok()
                        .and_then(|e| e.into_iter().next());

                    if let Some(sect) = section_event {
                        let sect_title = extract_tag(&sect, "title")
                            .unwrap_or_else(|| format!("Section {}", i + 1));
                        let content = sect.get("content")
                            .and_then(|v| v.as_str())
                            .unwrap_or("");

                        json!({
                            "title": sect_title,
                            "content": content,
                            "depth": 1,
                            "event_json": sect.to_string()
                        })
                    } else {
                        json!({
                            "title": format!("Section {}", i + 1),
                            "content": null,
                            "depth": 1,
                            "loading": false,
                            "event_id": e_tag
                        })
                    }
                })
                .collect();

            context.insert("sections", &sections);
            context.insert("section_count", &sections.len());

            // Fetch reactions and replies
            let reaction_filter = json!({
                "kinds": [7],
                "#e": [event_id],
                "limit": 100
            });

            if let Ok(reactions) = query::query_local(&state.ndb, &[reaction_filter]) {
                let reaction_counts = count_reactions(&reactions);
                context.insert("reactions", &reaction_counts);
            }

            let reply_filter = json!({
                "kinds": [1],
                "#e": [event_id],
                "limit": 20
            });

            if let Ok(replies) = query::query_local(&state.ndb, &[reply_filter]) {
                let reply_data: Vec<Value> = replies
                    .iter()
                    .map(|r| {
                        json!({
                            "pubkey": r.get("pubkey").and_then(|v| v.as_str()).unwrap_or(""),
                            "content": r.get("content").and_then(|v| v.as_str()).unwrap_or(""),
                            "created_at": r.get("created_at").and_then(|v| v.as_i64()).unwrap_or(0)
                        })
                    })
                    .collect();
                context.insert("replies", &reply_data);
            }
        }
        None => {
            context.insert("error", &format!("Publication not found: {}/{}", path.id_type, path.identifier));
            context.insert("title", "Not Found");
            context.insert("sections", &Vec::<Value>::new());
        }
    }

    render_template(&state.tera, "publication.html", context)
}

/// Visualize page - event statistics
async fn visualize_handler(
    State(state): State<Arc<AppState>>,
    Query(_params): Query<VisualizeQuery>,
) -> Response {
    let mut context = Context::new();
    context.insert("active_page", "visualize");

    // Get event counts
    let total_filter = json!({ "limit": 10000 });
    let pub_filter = json!({ "kinds": [30040, 30041], "limit": 10000 });

    let total_events = query::query_local(&state.ndb, &[total_filter])
        .map(|e| e.len())
        .unwrap_or(0);

    let total_publications = query::query_local(&state.ndb, &[pub_filter])
        .map(|e| e.len())
        .unwrap_or(0);

    context.insert("total_events", &total_events);
    context.insert("total_publications", &total_publications);
    context.insert("total_authors", &0);  // Would need distinct query
    context.insert("events_today", &0);   // Would need date filter

    // Kind distribution
    let kind_filter = json!({ "limit": 5000 });
    if let Ok(events) = query::query_local(&state.ndb, &[kind_filter]) {
        let mut kind_counts: HashMap<u64, usize> = HashMap::new();
        for event in &events {
            if let Some(kind) = event.get("kind").and_then(|v| v.as_u64()) {
                *kind_counts.entry(kind).or_insert(0) += 1;
            }
        }

        let total: usize = kind_counts.values().sum();
        let mut distribution: Vec<Value> = kind_counts
            .into_iter()
            .map(|(kind, count)| {
                let pct = if total > 0 { (count * 100) / total } else { 0 };
                json!({
                    "kind": kind,
                    "count": count,
                    "percentage": pct
                })
            })
            .collect();

        distribution.sort_by(|a, b| {
            b.get("count").and_then(|v| v.as_u64())
                .cmp(&a.get("count").and_then(|v| v.as_u64()))
        });

        context.insert("kind_distribution", &distribution);

        // Recent events
        let recent: Vec<Value> = events
            .into_iter()
            .take(10)
            .map(|e| {
                json!({
                    "id": e.get("id").and_then(|v| v.as_str()).unwrap_or(""),
                    "kind": e.get("kind").and_then(|v| v.as_u64()).unwrap_or(0),
                    "created_at": e.get("created_at").and_then(|v| v.as_i64()).unwrap_or(0),
                    "title": extract_tag(&e, "title")
                })
            })
            .collect();

        context.insert("recent_events", &recent);
    }

    render_template(&state.tera, "visualize.html", context)
}

/// API endpoint to refresh publications from relays
async fn refresh_handler(State(state): State<Arc<AppState>>) -> Response {
    info!("Refreshing publications from relays...");

    match relay::fetch_publications_simple(&state.ndb, 200).await {
        Ok(count) => {
            info!("Refreshed {} publications", count);
            (
                StatusCode::OK,
                [("content-type", "application/json")],
                json!({"success": true, "fetched": count}).to_string(),
            ).into_response()
        }
        Err(e) => {
            error!("Refresh failed: {}", e);
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                [("content-type", "application/json")],
                json!({"success": false, "error": e.to_string()}).to_string(),
            ).into_response()
        }
    }
}

/// Query parameters for load-more endpoint
#[derive(Deserialize)]
struct LoadMoreQuery {
    limit: Option<u64>,
}

/// API endpoint to load older publications from relays
/// Finds the oldest event in nostrdb and fetches events with until < oldest_timestamp
async fn load_more_handler(
    State(state): State<Arc<AppState>>,
    Query(params): Query<LoadMoreQuery>,
) -> Response {
    let limit = params.limit.unwrap_or(100);
    info!("Loading more publications (limit={})...", limit);

    // Find the oldest event timestamp in nostrdb
    let filter = json!({
        "kinds": [30040, 30041],
        "limit": 1000  // Get enough to find the actual oldest
    });

    let oldest_timestamp = match query::query_local(&state.ndb, &[filter]) {
        Ok(events) => {
            events.iter()
                .filter_map(|e| e.get("created_at").and_then(|v| v.as_u64()))
                .min()
        }
        Err(e) => {
            error!("Failed to query for oldest event: {}", e);
            None
        }
    };

    let until = match oldest_timestamp {
        Some(ts) => {
            info!("Found oldest event at timestamp {}, fetching older events", ts);
            Some(ts)
        }
        None => {
            info!("No existing events found, fetching latest");
            None
        }
    };

    match relay::fetch_publications_with_until(&state.ndb, limit, until).await {
        Ok(count) => {
            info!("Loaded {} more publications", count);
            (
                StatusCode::OK,
                [("content-type", "application/json")],
                json!({
                    "success": true,
                    "fetched": count,
                    "until_timestamp": until
                }).to_string(),
            ).into_response()
        }
        Err(e) => {
            error!("Load more failed: {}", e);
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                [("content-type", "application/json")],
                json!({"success": false, "error": e.to_string()}).to_string(),
            ).into_response()
        }
    }
}

/// API endpoint for infinite scroll
async fn api_feed_handler(
    State(state): State<Arc<AppState>>,
    Query(params): Query<FeedQuery>,
) -> Response {
    let page = params.page.unwrap_or(1);
    let limit = 20;
    let offset = (page - 1) * limit;

    let filter = json!({
        "kinds": [30040, 30041],
        "limit": limit + offset + 1  // Fetch one extra to check if more exist
    });

    match query::query_local(&state.ndb, &[filter]) {
        Ok(events) => {
            let has_more = events.len() > (offset + limit) as usize;
            let publications: Vec<Value> = events
                .into_iter()
                .skip(offset as usize)
                .take(limit as usize)
                .map(|event| {
                    let title = extract_tag(&event, "title").unwrap_or_else(|| "Untitled".to_string());
                    let d_tag = extract_tag(&event, "d").unwrap_or_default();
                    let pubkey = event.get("pubkey").and_then(|v| v.as_str()).unwrap_or("");
                    let kind = event.get("kind").and_then(|v| v.as_u64()).unwrap_or(0);
                    let id = event.get("id").and_then(|v| v.as_str()).unwrap_or("");

                    // Build proper naddr URL components
                    let (url_type, url_identifier) = if !d_tag.is_empty() && !pubkey.is_empty() {
                        match nostr_utils::encode_naddr(kind, pubkey, &d_tag, &[]) {
                            Ok(naddr) => ("naddr".to_string(), naddr),
                            Err(_) => ("id".to_string(), id.to_string()),
                        }
                    } else {
                        ("id".to_string(), id.to_string())
                    };

                    json!({
                        "title": title,
                        "url_type": url_type,
                        "url_identifier": url_identifier,
                        "event": event
                    })
                })
                .collect();

            let response = json!({
                "publications": publications,
                "hasMore": has_more,
                "page": page
            });

            (
                StatusCode::OK,
                [("content-type", "application/json")],
                response.to_string(),
            ).into_response()
        }
        Err(e) => {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                [("content-type", "application/json")],
                json!({"error": e.to_string()}).to_string(),
            ).into_response()
        }
    }
}

/// Query parameters for network API
#[derive(Deserialize)]
struct NetworkQuery {
    limit: Option<u64>,        // Legacy: global limit (fallback)
    limit_30040: Option<u64>,  // Limit for index events
    limit_30041: Option<u64>,  // Limit for section events
    limit_30023: Option<u64>,  // Limit for article events
    limit_30818: Option<u64>,  // Limit for wiki events
    max_depth: Option<u32>,    // Recursive depth for 30040/30041 traversal (0-5, default 2)
    kinds: Option<String>,     // Comma-separated list of kinds to include
}

/// API endpoint for network visualization (star-mode graph)
/// Returns nodes with isContainer flag and links with isSequential flag
/// Supports recursive depth traversal of 30040/30041 hierarchies
async fn api_events_handler(
    State(state): State<Arc<AppState>>,
    Query(params): Query<NetworkQuery>,
) -> Response {
    let max_depth = params.max_depth.unwrap_or(2).min(5); // Cap at 5 to prevent runaway
    let enabled_kinds: HashSet<u64> = params.kinds
        .as_ref()
        .map(|s| s.split(',').filter_map(|k| k.trim().parse().ok()).collect())
        .unwrap_or_else(|| vec![30040, 30041].into_iter().collect());

    // Per-kind limits with defaults
    let default_limit = params.limit.unwrap_or(50);
    let mut kind_limits: HashMap<u64, u64> = HashMap::new();
    kind_limits.insert(30040, params.limit_30040.unwrap_or(default_limit));
    kind_limits.insert(30041, params.limit_30041.unwrap_or(default_limit));
    kind_limits.insert(30023, params.limit_30023.unwrap_or(default_limit));
    kind_limits.insert(30818, params.limit_30818.unwrap_or(default_limit));

    // Build graph recursively
    let mut nodes: Vec<Value> = vec![];
    let mut links: Vec<Value> = vec![];
    let mut visited: HashSet<String> = HashSet::new();
    let mut a_tag_to_id: HashMap<String, String> = HashMap::new();
    let mut pubkeys: HashSet<String> = HashSet::new();
    let mut fetched_per_kind: HashMap<u64, usize> = HashMap::new();

    // Step 1: Fetch events for each enabled kind with its specific limit
    let mut all_events: Vec<Value> = vec![];
    for kind in &enabled_kinds {
        let limit = kind_limits.get(kind).copied().unwrap_or(default_limit);
        let filter = json!({
            "kinds": [kind],
            "limit": limit
        });

        if let Ok(mut events) = query::query_local(&state.ndb, &[filter]) {
            fetched_per_kind.insert(*kind, events.len());
            all_events.append(&mut events);
        }
    }

    // Step 2: Get total counts for ALL publication events in nostrdb (unfiltered)
    let total_filter = json!({
        "kinds": [30040, 30041, 30023, 30818],
        "limit": 10000
    });
    let mut total_counts: HashMap<u64, usize> = HashMap::new();
    let mut total_all = 0usize;
    if let Ok(all_available) = query::query_local(&state.ndb, &[total_filter]) {
        for event in &all_available {
            if let Some(kind) = event.get("kind").and_then(|v| v.as_u64()) {
                *total_counts.entry(kind).or_insert(0) += 1;
                total_all += 1;
            }
        }
    }

    // Step 3: Process fetched events (with recursive expansion for 30040)
    for event in all_events {
        let kind = event.get("kind").and_then(|v| v.as_u64()).unwrap_or(0);

        // For 30040 indexes, recursively expand children
        if kind == 30040 {
            process_event_recursive(
                &state.ndb,
                event,
                0,
                max_depth,
                &enabled_kinds,
                &mut nodes,
                &mut links,
                &mut visited,
                &mut a_tag_to_id,
                &mut pubkeys,
            );
        } else {
            // For other kinds, just add directly without recursion
            if let Some(id) = event.get("id").and_then(|v| v.as_str()) {
                if !visited.contains(id) {
                    visited.insert(id.to_string());

                    let pubkey_str = event.get("pubkey").and_then(|v| v.as_str()).unwrap_or("");
                    if !pubkey_str.is_empty() {
                        pubkeys.insert(pubkey_str.to_string());
                    }

                    let title = extract_tag(&event, "title")
                        .or_else(|| extract_tag(&event, "d"))
                        .unwrap_or_else(|| if id.len() >= 8 { id[..8].to_string() } else { id.to_string() });
                    let content = event.get("content").and_then(|v| v.as_str()).unwrap_or("");
                    let content_preview = if content.len() > 200 {
                        format!("{}...", &content[..200])
                    } else {
                        content.to_string()
                    };

                    nodes.push(json!({
                        "id": id,
                        "kind": kind,
                        "pubkey": pubkey_str,
                        "title": title,
                        "depth": 0,
                        "isContainer": false,
                        "contentPreview": content_preview
                    }));
                }
            }
        }
    }

    // Step 3.5: Connect orphaned 30041/30818/30023 to their parent 30040 indexes
    // This handles cases where events were fetched directly (not through recursion)
    let node_ids: HashSet<String> = nodes.iter()
        .filter_map(|n| n.get("id").and_then(|v| v.as_str()).map(String::from))
        .collect();

    // Build reverse lookup: child a-tag -> parent node id
    let mut child_to_parent: HashMap<String, String> = HashMap::new();
    for node in &nodes {
        if let Some(kind) = node.get("kind").and_then(|v| v.as_u64()) {
            if kind == 30040 {
                if let Some(id) = node.get("id").and_then(|v| v.as_str()) {
                    // Find all events referenced by this 30040's a-tags
                    let node_filter = json!({
                        "ids": [id],
                        "limit": 1
                    });
                    if let Ok(mut parent_events) = query::query_local(&state.ndb, &[node_filter]) {
                        if let Some(parent_event) = parent_events.pop() {
                            for (child_kind, child_pubkey, child_d_tag) in extract_a_tags(&parent_event) {
                                let child_key = format!("{}:{}:{}", child_kind, child_pubkey, child_d_tag);
                                child_to_parent.insert(child_key, id.to_string());
                            }
                        }
                    }
                }
            }
        }
    }

    // Create links for orphaned children (30040, 30041, 30818, 30023)
    for node in &nodes {
        if let Some(kind) = node.get("kind").and_then(|v| v.as_u64()) {
            if kind == 30040 || kind == 30041 || kind == 30818 || kind == 30023 {
                if let (Some(child_id), Some(pubkey)) = (
                    node.get("id").and_then(|v| v.as_str()),
                    node.get("pubkey").and_then(|v| v.as_str()),
                ) {
                    // Fetch the actual event to get the d-tag
                    let child_filter = json!({
                        "ids": [child_id],
                        "limit": 1
                    });

                    if let Ok(mut child_events) = query::query_local(&state.ndb, &[child_filter]) {
                        if let Some(child_event) = child_events.pop() {
                            let d_tag = extract_tag(&child_event, "d").unwrap_or_default();
                            if !d_tag.is_empty() {
                                let child_key = format!("{}:{}:{}", kind, pubkey, d_tag);
                                if let Some(parent_id) = child_to_parent.get(&child_key) {
                                    // Check if link already exists
                                    let link_exists = links.iter().any(|link| {
                                        link.get("source").and_then(|v| v.as_str()) == Some(parent_id) &&
                                        link.get("target").and_then(|v| v.as_str()) == Some(child_id)
                                    });

                                    if !link_exists && node_ids.contains(parent_id) {
                                        links.push(json!({
                                            "source": parent_id,
                                            "target": child_id,
                                            "isSequential": true
                                        }));
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // Step 4: Fetch author profiles (kind 0) for display names
    let mut authors: Vec<Value> = vec![];
    for pk in &pubkeys {
        let profile_filter = json!({
            "kinds": [0],
            "authors": [pk],
            "limit": 1
        });
        if let Ok(profiles) = query::query_local(&state.ndb, &[profile_filter]) {
            if let Some(profile) = profiles.into_iter().next() {
                let content = profile.get("content").and_then(|v| v.as_str()).unwrap_or("{}");
                if let Ok(meta) = serde_json::from_str::<Value>(content) {
                    let name = meta.get("name")
                        .or_else(|| meta.get("display_name"))
                        .and_then(|v| v.as_str())
                        .map(|s| s.to_string());
                    let picture = meta.get("picture").and_then(|v| v.as_str()).map(|s| s.to_string());
                    authors.push(json!({
                        "pubkey": pk,
                        "name": name,
                        "picture": picture
                    }));
                }
            }
        }
    }

    // Count displayed nodes by kind
    let mut displayed_counts: HashMap<u64, usize> = HashMap::new();
    for node in &nodes {
        if let Some(kind) = node.get("kind").and_then(|v| v.as_u64()) {
            *displayed_counts.entry(kind).or_insert(0) += 1;
        }
    }

    let response = json!({
        "nodes": nodes,
        "links": links,
        "authors": authors,
        "stats": {
            "displayed": nodes.len(),
            "totalLoaded": total_all,
            "fetchedByKind": fetched_per_kind,  // Number of events fetched per kind
            "displayedByKind": displayed_counts,
            "loadedByKind": total_counts
        }
    });

    (
        StatusCode::OK,
        [("content-type", "application/json")],
        response.to_string(),
    ).into_response()
}

/// Recursively process an event and its children via a-tags
/// Only uses events already in the database (no relay fetching for performance)
fn process_event_recursive(
    ndb: &nostrdb::Ndb,
    event: Value,
    depth: u32,
    max_depth: u32,
    enabled_kinds: &HashSet<u64>,
    nodes: &mut Vec<Value>,
    links: &mut Vec<Value>,
    visited: &mut HashSet<String>,
    a_tag_to_id: &mut HashMap<String, String>,
    pubkeys: &mut HashSet<String>,
) {
    let id = match event.get("id").and_then(|v| v.as_str()) {
        Some(id) => id.to_string(),
        None => return,
    };

    // Skip if already visited
    if visited.contains(&id) {
        return;
    }
    visited.insert(id.clone());

    let kind = event.get("kind").and_then(|v| v.as_u64()).unwrap_or(0);

    // Skip if kind not enabled
    if !enabled_kinds.contains(&kind) {
        return;
    }

    let pubkey = event.get("pubkey").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let d_tag = extract_tag(&event, "d").unwrap_or_default();
    let title = extract_tag(&event, "title")
        .or_else(|| extract_tag(&event, "d"))
        .unwrap_or_else(|| if id.len() >= 8 { id[..8].to_string() } else { id.clone() });
    let content = event.get("content").and_then(|v| v.as_str()).unwrap_or("");
    let content_preview = if content.len() > 200 {
        format!("{}...", &content[..200])
    } else {
        content.to_string()
    };

    // Register in a-tag lookup
    if !d_tag.is_empty() {
        let a_key = format!("{}:{}:{}", kind, pubkey, d_tag);
        a_tag_to_id.insert(a_key, id.clone());
    }

    // Track pubkey for profile lookup
    if !pubkey.is_empty() {
        pubkeys.insert(pubkey.clone());
    }

    // Add node
    let is_container = kind == 30040;
    nodes.push(json!({
        "id": id,
        "kind": kind,
        "pubkey": pubkey,
        "title": title,
        "depth": depth,
        "isContainer": is_container,
        "contentPreview": content_preview
    }));

    // If at max depth, stop recursion
    if depth >= max_depth {
        return;
    }

    // Extract children from a-tags and recurse
    // Only recurse into 30040 (indexes) and 30041 (sections)
    for (child_kind, child_pubkey, child_d_tag) in extract_a_tags(&event) {
        // Only process publication kinds (30040 indexes and 30041 sections)
        if child_kind != 30040 && child_kind != 30041 {
            continue;
        }

        // Check if child is in enabled kinds
        if !enabled_kinds.contains(&child_kind) {
            continue;
        }

        // Check local DB only (no relay fetching for performance)
        let child_filter = json!({
            "kinds": [child_kind],
            "authors": [child_pubkey],
            "#d": [child_d_tag],
            "limit": 1
        });

        let child_event = match query::query_local(ndb, &[child_filter]) {
            Ok(children) => children.into_iter().next(),
            Err(_) => None,
        };

        // Process the child if found in DB
        if let Some(child) = child_event {
            let child_id = child.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string();

            if !child_id.is_empty() && !visited.contains(&child_id) {
                // Add link from parent to child
                links.push(json!({
                    "source": id,
                    "target": child_id,
                    "isSequential": true
                }));

                // Recurse into child
                // For 30040 (indexes), continue recursing up to max_depth
                // For 30041 (sections), they're leaf nodes but we still process them
                process_event_recursive(
                    ndb,
                    child,  // Pass by value
                    depth + 1,
                    max_depth,
                    enabled_kinds,
                    nodes,
                    links,
                    visited,
                    a_tag_to_id,
                    pubkeys,
                );
            }
        }
    }
}

/// API endpoint for publication with TOC structure (gc-alexandria compatible)
/// Returns index event + section addresses + hierarchical TOC
async fn api_publication_handler(
    State(state): State<Arc<AppState>>,
    Path(path): Path<PublicationPath>,
) -> Response {
    // Parse identifier based on type (same logic as publication_handler)
    let event = match path.id_type.as_str() {
        "naddr" => {
            match nostr_utils::decode_naddr(&path.identifier) {
                Ok(naddr_data) => {
                    let filter = json!({
                        "kinds": [naddr_data.kind],
                        "authors": [naddr_data.pubkey],
                        "#d": [naddr_data.identifier],
                        "limit": 1
                    });
                    query::query_local(&state.ndb, &[filter])
                        .ok()
                        .and_then(|e| e.into_iter().next())
                }
                Err(_) => None,
            }
        }
        "nevent" => {
            match nostr_utils::decode_nevent(&path.identifier) {
                Ok(nevent_data) => {
                    let filter = json!({
                        "ids": [nevent_data.id],
                        "limit": 1
                    });
                    query::query_local(&state.ndb, &[filter])
                        .ok()
                        .and_then(|e| e.into_iter().next())
                }
                Err(_) => None,
            }
        }
        "id" => {
            let filter = json!({
                "ids": [path.identifier.clone()],
                "limit": 1
            });
            query::query_local(&state.ndb, &[filter])
                .ok()
                .and_then(|e| e.into_iter().next())
        }
        "d" => {
            let filter = json!({
                "kinds": [30040, 30041],
                "#d": [path.identifier.clone()],
                "limit": 1
            });
            query::query_local(&state.ndb, &[filter])
                .ok()
                .and_then(|e| e.into_iter().next())
        }
        _ => None,
    };

    match event {
        Some(event) => {
            let pubkey = event.get("pubkey").and_then(|v| v.as_str()).unwrap_or("");
            let kind = event.get("kind").and_then(|v| v.as_u64()).unwrap_or(0);
            let d_tag = extract_tag(&event, "d").unwrap_or_default();

            // Extract sections from a-tags (for kind 30040 index)
            let section_addresses: Vec<String> = extract_a_tags(&event)
                .iter()
                .map(|(k, p, d)| format!("{}:{}:{}", k, p, d))
                .collect();

            // Build TOC with titles (lazy-loaded, just addresses for now)
            let toc: Vec<Value> = section_addresses
                .iter()
                .enumerate()
                .map(|(i, addr)| {
                    // Try to fetch section to get its title
                    let parts: Vec<&str> = addr.splitn(3, ':').collect();
                    let title = if parts.len() >= 3 {
                        let section_filter = json!({
                            "kinds": [parts[0].parse::<u64>().unwrap_or(30041)],
                            "authors": [parts[1]],
                            "#d": [parts[2]],
                            "limit": 1
                        });
                        query::query_local(&state.ndb, &[section_filter])
                            .ok()
                            .and_then(|e| e.into_iter().next())
                            .and_then(|e| extract_tag(&e, "title"))
                            .unwrap_or_else(|| format!("Section {}", i + 1))
                    } else {
                        format!("Section {}", i + 1)
                    };

                    json!({
                        "address": addr,
                        "title": title,
                        "depth": 0,
                        "children": []
                    })
                })
                .collect();

            let response = json!({
                "index": {
                    "event": event,
                    "address": format!("{}:{}:{}", kind, pubkey, d_tag),
                    "sections": section_addresses
                },
                "toc": toc
            });

            (
                StatusCode::OK,
                [("content-type", "application/json")],
                response.to_string(),
            ).into_response()
        }
        None => {
            (
                StatusCode::NOT_FOUND,
                [("content-type", "application/json")],
                json!({"error": format!("Publication not found: {}/{}", path.id_type, path.identifier)}).to_string(),
            ).into_response()
        }
    }
}

/// API endpoint for fetching a single section by address
/// Address format: "kind:pubkey:d-tag"
async fn api_section_handler(
    State(state): State<Arc<AppState>>,
    Path(address): Path<String>,
) -> Response {
    // Parse address: "kind:pubkey:d-tag"
    let parts: Vec<&str> = address.splitn(3, ':').collect();

    if parts.len() < 3 {
        return (
            StatusCode::BAD_REQUEST,
            [("content-type", "application/json")],
            json!({"error": "Invalid address format. Expected: kind:pubkey:d-tag"}).to_string(),
        ).into_response();
    }

    let kind: u64 = match parts[0].parse() {
        Ok(k) => k,
        Err(_) => return (
            StatusCode::BAD_REQUEST,
            [("content-type", "application/json")],
            json!({"error": "Invalid kind in address"}).to_string(),
        ).into_response(),
    };
    let pubkey = parts[1];
    let d_tag = parts[2];

    // Query nostrdb for this specific section
    let filter = json!({
        "kinds": [kind],
        "authors": [pubkey],
        "#d": [d_tag],
        "limit": 1
    });

    // First try local DB
    let local_result = query::query_local(&state.ndb, &[filter.clone()]);

    let event = match local_result {
        Ok(events) if !events.is_empty() => {
            events.into_iter().next().unwrap()
        }
        Ok(_) => {
            // Not in local DB - fetch from relays (on-demand)
            info!("Section {} not in local DB, fetching from relays...", address);

            match relay::fetch_by_address(&state.ndb, kind, pubkey, d_tag).await {
                Ok(Some(event)) => event,
                Ok(None) => {
                    return (
                        StatusCode::NOT_FOUND,
                        [("content-type", "application/json")],
                        json!({"error": format!("Section not found: {}", address)}).to_string(),
                    ).into_response();
                }
                Err(e) => {
                    warn!("Relay fetch failed for {}: {}", address, e);
                    return (
                        StatusCode::NOT_FOUND,
                        [("content-type", "application/json")],
                        json!({"error": format!("Section not found: {}", address)}).to_string(),
                    ).into_response();
                }
            }
        }
        Err(e) => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                [("content-type", "application/json")],
                json!({"error": e.to_string()}).to_string(),
            ).into_response();
        }
    };

    // Check if this is a nested index (kind 30040) vs actual content (30041, 30818, 30023)
    let event_kind = event.get("kind").and_then(|v| v.as_u64()).unwrap_or(0);
    let title = extract_tag(&event, "title").unwrap_or_else(|| d_tag.to_string());

    if event_kind == 30040 {
        // This is a nested index - it has a-tags pointing to actual content
        // Extract children and return them for hierarchical rendering
        let children: Vec<Value> = extract_a_tags(&event)
            .into_iter()
            .map(|(child_kind, child_pubkey, child_d_tag)| {
                json!({
                    "address": format!("{}:{}:{}", child_kind, child_pubkey, child_d_tag),
                    "kind": child_kind,
                    "title": child_d_tag.clone()  // Will be updated when fetched
                })
            })
            .collect();

        // For nested indices, try to get summary or content from the first child
        // or return metadata about the index structure
        let response = json!({
            "event": event,
            "address": address,
            "title": title,
            "kind": event_kind,
            "is_index": true,
            "children": children,
            "content": "",  // Indices don't have direct content
            "html": format!("<p class=\"index-notice\">This is an index containing {} sections. Click on sections in the table of contents to view their content.</p>", children.len())
        });

        return (
            StatusCode::OK,
            [("content-type", "application/json")],
            response.to_string(),
        ).into_response();
    }

    // This is actual content (30041, 30818, 30023)
    let raw_content = event.get("content").and_then(|v| v.as_str()).unwrap_or("");

    // Render AsciiDoc to HTML using the system asciidoctor binary
    let rendered_html = render_asciidoc(raw_content);

    let response = json!({
        "event": event,
        "address": address,
        "title": title,
        "kind": event_kind,
        "is_index": false,
        "content": raw_content,      // Raw AsciiDoc content (for reference)
        "html": rendered_html        // Pre-rendered HTML from asciidoctor
    });

    (
        StatusCode::OK,
        [("content-type", "application/json")],
        response.to_string(),
    ).into_response()
}

// =============================================================================
// Helper Functions
// =============================================================================

/// Render AsciiDoc content to HTML using the asciidoctor binary
///
/// Uses the system-installed `asciidoctor` command to convert AsciiDoc to HTML.
/// Falls back to returning raw content wrapped in <pre> if asciidoctor is not available.
fn render_asciidoc(content: &str) -> String {
    // Spawn asciidoctor with embedded mode (no header/footer)
    let result = Command::new("asciidoctor")
        .args([
            "-s",           // Standalone mode (no header/footer)
            "-o", "-",      // Output to stdout
            "-",            // Read from stdin
        ])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn();

    match result {
        Ok(mut child) => {
            // Write content to stdin
            if let Some(mut stdin) = child.stdin.take() {
                let _ = stdin.write_all(content.as_bytes());
            }

            // Wait for completion and get output
            match child.wait_with_output() {
                Ok(output) if output.status.success() => {
                    String::from_utf8_lossy(&output.stdout).to_string()
                }
                Ok(output) => {
                    let stderr = String::from_utf8_lossy(&output.stderr);
                    error!("asciidoctor failed: {}", stderr);
                    // Return raw content with error message
                    format!(
                        "<div class=\"asciidoc-error\">AsciiDoc render error: {}</div><pre>{}</pre>",
                        stderr,
                        html_escape(content)
                    )
                }
                Err(e) => {
                    error!("Failed to wait for asciidoctor: {}", e);
                    format!("<pre>{}</pre>", html_escape(content))
                }
            }
        }
        Err(e) => {
            // asciidoctor not found - return raw content
            error!("asciidoctor not found: {}. Returning raw content.", e);
            format!("<pre>{}</pre>", html_escape(content))
        }
    }
}

/// Simple HTML escaping for fallback display
fn html_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}

/// Render a Tera template to HTML response
fn render_template(tera: &Tera, template: &str, context: Context) -> Response {
    match tera.render(template, &context) {
        Ok(html) => Html(html).into_response(),
        Err(e) => {
            // Get full error chain for debugging
            let mut error_msg = format!("Template: {}\nError: {}", template, e);
            if let Some(source) = e.source() {
                error_msg.push_str(&format!("\nCaused by: {}", source));
            }
            error!("Template render error:\n{}", error_msg);
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Html(format!("<h1>Template Error</h1><pre>{}</pre>", error_msg)),
            ).into_response()
        }
    }
}

/// Extract a tag value from a Nostr event
fn extract_tag(event: &Value, tag_name: &str) -> Option<String> {
    event
        .get("tags")?
        .as_array()?
        .iter()
        .find(|tag| {
            tag.as_array()
                .and_then(|t| t.first())
                .and_then(|v| v.as_str())
                == Some(tag_name)
        })
        .and_then(|tag| tag.as_array())
        .and_then(|tag| tag.get(1))
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
}

/// Extract all e-tags (event references) from a Nostr event
fn extract_e_tags(event: &Value) -> Vec<String> {
    event
        .get("tags")
        .and_then(|t| t.as_array())
        .map(|tags| {
            tags.iter()
                .filter_map(|tag| {
                    let arr = tag.as_array()?;
                    if arr.first()?.as_str()? == "e" {
                        arr.get(1)?.as_str().map(|s| s.to_string())
                    } else {
                        None
                    }
                })
                .collect()
        })
        .unwrap_or_default()
}

/// Extract all a-tags (addressable event references) from a Nostr event
/// Returns tuples of (kind, pubkey, d-tag) parsed from "kind:pubkey:d-tag" format
fn extract_a_tags(event: &Value) -> Vec<(u64, String, String)> {
    event
        .get("tags")
        .and_then(|t| t.as_array())
        .map(|tags| {
            tags.iter()
                .filter_map(|tag| {
                    let arr = tag.as_array()?;
                    if arr.first()?.as_str()? == "a" {
                        let a_value = arr.get(1)?.as_str()?;
                        let parts: Vec<&str> = a_value.splitn(3, ':').collect();
                        if parts.len() >= 3 {
                            let kind = parts[0].parse::<u64>().ok()?;
                            let pubkey = parts[1].to_string();
                            let d_tag = parts[2].to_string();
                            Some((kind, pubkey, d_tag))
                        } else {
                            None
                        }
                    } else {
                        None
                    }
                })
                .collect()
        })
        .unwrap_or_default()
}

/// Count reactions by emoji
fn count_reactions(reactions: &[Value]) -> Vec<Value> {
    let mut counts: HashMap<String, usize> = HashMap::new();

    for reaction in reactions {
        let emoji = reaction
            .get("content")
            .and_then(|v| v.as_str())
            .unwrap_or("+")
            .to_string();

        *counts.entry(emoji).or_insert(0) += 1;
    }

    counts
        .into_iter()
        .map(|(emoji, count)| json!({"emoji": emoji, "count": count}))
        .collect()
}

// =============================================================================
// Configuration
// =============================================================================

struct Args {
    bind_addr: String,
}

fn parse_args() -> Args {
    let args: Vec<String> = std::env::args().collect();
    let mut bind_addr = "127.0.0.1:3000".to_string();

    for i in 0..args.len() {
        if (args[i] == "--bind" || args[i] == "-b") && i + 1 < args.len() {
            bind_addr = args[i + 1].clone();
        }
    }

    Args { bind_addr }
}

fn get_db_path() -> String {
    if let Some(data_home) = std::env::var_os("XDG_DATA_HOME") {
        format!("{}/tendrl/nostrdb", data_home.to_string_lossy())
    } else if let Some(home) = std::env::var_os("HOME") {
        format!("{}/.local/share/tendrl/nostrdb", home.to_string_lossy())
    } else {
        "./tendrl_data/nostrdb".to_string()
    }
}

fn get_template_dir() -> PathBuf {
    // Check for templates - prioritize crate's own templates directory
    let candidates = [
        // CARGO_MANIFEST_DIR is baked in at compile time - most reliable
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("templates"),
        // When running from workspace root
        PathBuf::from("crates/tendrl_ws/templates"),
        // When running from crate directory
        PathBuf::from("templates"),
    ];

    for path in &candidates {
        // Check both that it exists AND contains .html files
        if path.exists() && path.join("base.html").exists() {
            return path.clone();
        }
    }

    // Fall back to compile-time path
    candidates[0].clone()
}

fn get_static_dir() -> PathBuf {
    let candidates = [
        // CARGO_MANIFEST_DIR is baked in at compile time - most reliable
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("static"),
        // When running from workspace root
        PathBuf::from("crates/tendrl_ws/static"),
        // When running from crate directory
        PathBuf::from("static"),
    ];

    for path in &candidates {
        // Check both that it exists AND contains expected files
        if path.exists() && path.join("css").exists() {
            return path.clone();
        }
    }

    // Fall back to compile-time path
    candidates[0].clone()
}
