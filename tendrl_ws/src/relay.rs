//! Relay fetching for tendrl_ws
//!
//! Fetches events from Nostr relays and ingests them into nostrdb.

use anyhow::Result;
use futures::{SinkExt, StreamExt};
use nostrdb::Ndb;
use serde_json::{json, Value};
use std::time::{Duration, Instant};
use tokio::time::timeout;
use tokio_tungstenite::{connect_async, tungstenite::Message};
use tracing::{debug, info, warn};

/// Default relays to fetch from
pub const DEFAULT_RELAYS: &[&str] = &[
    "wss://relay.damus.io",
    "wss://nos.lol",
    "wss://relay.nostr.band",
];

/// Fetch publication events (kind 30040, 30041) from relays and ingest into nostrdb
pub async fn fetch_publications_simple(ndb: &Ndb, limit: u64) -> Result<usize> {
    fetch_publications_with_until(ndb, limit, None).await
}

/// Fetch publications older than the given timestamp (for "load more" functionality)
pub async fn fetch_publications_with_until(ndb: &Ndb, limit: u64, until: Option<u64>) -> Result<usize> {
    let relay_url = "wss://relay.damus.io";
    if let Some(ts) = until {
        info!("Fetching publications from {} (until={})", relay_url, ts);
    } else {
        info!("Fetching publications from {}", relay_url);
    }

    let (mut ws, _) = connect_async(relay_url).await?;

    // Send REQ for publication kinds with optional until parameter
    let sub_id = "fetch1";
    let req = if let Some(ts) = until {
        json!(["REQ", sub_id, {
            "kinds": [30040, 30041],
            "until": ts,
            "limit": limit
        }])
    } else {
        json!(["REQ", sub_id, {
            "kinds": [30040, 30041],
            "limit": limit
        }])
    };

    ws.send(Message::Text(req.to_string())).await?;

    let mut events_received = 0;
    let start = Instant::now();
    let fetch_timeout = Duration::from_secs(15);

    while start.elapsed() < fetch_timeout {
        match timeout(Duration::from_secs(5), ws.next()).await {
            Ok(Some(Ok(Message::Text(text)))) => {
                if let Ok(msg) = serde_json::from_str::<Vec<Value>>(&text) {
                    if msg.len() >= 2 {
                        let msg_type = msg[0].as_str().unwrap_or("");

                        match msg_type {
                            "EVENT" => {
                                if msg.len() >= 3 {
                                    // Ingest into nostrdb
                                    if let Err(e) = ndb.process_event(&text) {
                                        debug!("Failed to ingest: {}", e);
                                    } else {
                                        events_received += 1;
                                        if events_received % 50 == 0 {
                                            debug!("Ingested {} events so far", events_received);
                                        }
                                    }
                                }
                            }
                            "EOSE" => {
                                info!("EOSE received after {} events", events_received);
                                break;
                            }
                            _ => {}
                        }
                    }
                }
            }
            Ok(Some(Ok(_))) => {} // Other message types
            Ok(Some(Err(e))) => {
                warn!("WebSocket error: {}", e);
                break;
            }
            Ok(None) => break,
            Err(_) => {
                warn!("Timeout waiting for events");
                break;
            }
        }
    }

    // Close subscription
    let close = json!(["CLOSE", sub_id]);
    let _ = ws.send(Message::Text(close.to_string())).await;
    let _ = ws.close(None).await;

    info!("Fetched {} events in {:?}", events_received, start.elapsed());

    Ok(events_received)
}

/// Fetch publications from multiple relays
pub async fn fetch_from_multiple_relays(ndb: &Ndb, relays: &[&str], limit: u64) -> Result<usize> {
    let mut total = 0;

    for relay in relays {
        match fetch_from_relay(ndb, relay, limit).await {
            Ok(count) => {
                info!("Fetched {} from {}", count, relay);
                total += count;
            }
            Err(e) => {
                warn!("Failed to fetch from {}: {}", relay, e);
            }
        }
    }

    Ok(total)
}

/// Fetch specific events by IDs from relays
pub async fn fetch_events_by_ids(ndb: &Ndb, event_ids: &[String]) -> Result<Vec<Value>> {
    if event_ids.is_empty() {
        return Ok(vec![]);
    }

    let relay_url = "wss://relay.damus.io";
    debug!("Fetching {} events by ID from {}", event_ids.len(), relay_url);

    let (mut ws, _) = connect_async(relay_url).await?;

    let sub_id = "fetch_ids";
    let req = json!(["REQ", sub_id, {
        "ids": event_ids,
        "limit": event_ids.len()
    }]);

    ws.send(Message::Text(req.to_string())).await?;

    let mut fetched_events = vec![];
    let start = Instant::now();
    let fetch_timeout = Duration::from_secs(10);

    while start.elapsed() < fetch_timeout {
        match timeout(Duration::from_secs(3), ws.next()).await {
            Ok(Some(Ok(Message::Text(text)))) => {
                if let Ok(msg) = serde_json::from_str::<Vec<Value>>(&text) {
                    if msg.len() >= 2 {
                        match msg[0].as_str().unwrap_or("") {
                            "EVENT" if msg.len() >= 3 => {
                                // Ingest into nostrdb
                                let _ = ndb.process_event(&text);
                                // Also return the event
                                if let Some(event) = msg.get(2) {
                                    fetched_events.push(event.clone());
                                }
                            }
                            "EOSE" => break,
                            _ => {}
                        }
                    }
                }
            }
            Ok(Some(Ok(_))) => {}
            Ok(Some(Err(_))) | Ok(None) | Err(_) => break,
        }
    }

    let close = json!(["CLOSE", sub_id]);
    let _ = ws.send(Message::Text(close.to_string())).await;
    let _ = ws.close(None).await;

    debug!("Fetched {} events by ID", fetched_events.len());
    Ok(fetched_events)
}

/// Fetch an addressable event by kind:pubkey:d-tag from relays
/// This is the on-demand fetch for sections (kind 30041) when not in local DB
pub async fn fetch_by_address(
    ndb: &Ndb,
    kind: u64,
    pubkey: &str,
    d_tag: &str,
) -> Result<Option<Value>> {
    // Try multiple relays
    let relays = ["wss://relay.damus.io", "wss://nos.lol", "wss://relay.nostr.band"];

    for relay_url in relays {
        debug!("Fetching {}:{}:{} from {}", kind, &pubkey[..8], d_tag, relay_url);

        let ws_result = connect_async(relay_url).await;
        let (mut ws, _) = match ws_result {
            Ok(ws) => ws,
            Err(e) => {
                debug!("Failed to connect to {}: {}", relay_url, e);
                continue;
            }
        };

        let sub_id = "fetch_addr";
        let req = json!(["REQ", sub_id, {
            "kinds": [kind],
            "authors": [pubkey],
            "#d": [d_tag],
            "limit": 1
        }]);

        if ws.send(Message::Text(req.to_string())).await.is_err() {
            continue;
        }

        let start = Instant::now();
        let fetch_timeout = Duration::from_secs(5);
        let mut found_event: Option<Value> = None;

        while start.elapsed() < fetch_timeout {
            match timeout(Duration::from_secs(3), ws.next()).await {
                Ok(Some(Ok(Message::Text(text)))) => {
                    if let Ok(msg) = serde_json::from_str::<Vec<Value>>(&text) {
                        if msg.len() >= 2 {
                            match msg[0].as_str().unwrap_or("") {
                                "EVENT" if msg.len() >= 3 => {
                                    // Ingest into nostrdb for caching
                                    let _ = ndb.process_event(&text);
                                    // Return the event
                                    if let Some(event) = msg.get(2) {
                                        found_event = Some(event.clone());
                                    }
                                }
                                "EOSE" => break,
                                _ => {}
                            }
                        }
                    }
                }
                Ok(Some(Ok(_))) => {}
                Ok(Some(Err(_))) | Ok(None) | Err(_) => break,
            }
        }

        let close = json!(["CLOSE", sub_id]);
        let _ = ws.send(Message::Text(close.to_string())).await;
        let _ = ws.close(None).await;

        if found_event.is_some() {
            info!("Found {}:{}:{} from {}", kind, &pubkey[..8], d_tag, relay_url);
            return Ok(found_event);
        }
    }

    debug!("Section {}:{}:{} not found on any relay", kind, &pubkey[..8], d_tag);
    Ok(None)
}

/// Fetch from a single relay
async fn fetch_from_relay(ndb: &Ndb, relay_url: &str, limit: u64) -> Result<usize> {
    let (mut ws, _) = connect_async(relay_url).await?;

    let sub_id = format!("fetch_{}", &relay_url[6..].chars().take(8).collect::<String>());
    let req = json!(["REQ", &sub_id, {
        "kinds": [30040, 30041],
        "limit": limit
    }]);

    ws.send(Message::Text(req.to_string())).await?;

    let mut events_received = 0;
    let start = Instant::now();
    let fetch_timeout = Duration::from_secs(10);

    while start.elapsed() < fetch_timeout {
        match timeout(Duration::from_secs(3), ws.next()).await {
            Ok(Some(Ok(Message::Text(text)))) => {
                if let Ok(msg) = serde_json::from_str::<Vec<Value>>(&text) {
                    if msg.len() >= 2 {
                        match msg[0].as_str().unwrap_or("") {
                            "EVENT" if msg.len() >= 3 => {
                                if ndb.process_event(&text).is_ok() {
                                    events_received += 1;
                                }
                            }
                            "EOSE" => break,
                            _ => {}
                        }
                    }
                }
            }
            Ok(Some(Ok(_))) => {}
            Ok(Some(Err(_))) | Ok(None) | Err(_) => break,
        }
    }

    let close = json!(["CLOSE", &sub_id]);
    let _ = ws.send(Message::Text(close.to_string())).await;
    let _ = ws.close(None).await;

    Ok(events_received)
}
