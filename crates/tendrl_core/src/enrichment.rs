//! Event enrichment engine
//!
//! This module implements per-event enrichment for feeds with `root` configuration.
//! It builds relationships between events based on tags and aggregates statistics.

use crate::config::{DepConfig, RootConfig};
use nostrdb::{Filter, Ndb, Note, Transaction};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// Enriched event with attached dependencies and stats
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EnrichedEvent {
    /// The root event (flattened fields)
    #[serde(flatten)]
    pub event: serde_json::Value,

    /// Enriched data attached by dependency resolution
    #[serde(rename = "_enriched")]
    pub enriched: EnrichmentData,
}

/// Container for all enrichment data
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct EnrichmentData {
    /// Aggregated statistics (count, total_sats, etc.)
    #[serde(skip_serializing_if = "HashMap::is_empty")]
    pub stats: HashMap<String, serde_json::Value>,

    /// Expanded events (full event data)
    #[serde(skip_serializing_if = "HashMap::is_empty")]
    pub events: HashMap<String, Vec<serde_json::Value>>,

    /// Single events (like author profile)
    #[serde(skip_serializing_if = "HashMap::is_empty")]
    pub single: HashMap<String, serde_json::Value>,
}

/// Enrichment engine that processes events according to root config
pub struct EnrichmentEngine<'a> {
    ndb: &'a Ndb,
    root_config: &'a RootConfig,
}

impl<'a> EnrichmentEngine<'a> {
    /// Create a new enrichment engine
    pub fn new(ndb: &'a Ndb, root_config: &'a RootConfig) -> Self {
        Self { ndb, root_config }
    }

    /// Enrich a single event with all configured dependencies
    pub fn enrich_event(&self, root_event: &Note, txn: &Transaction) -> Result<EnrichedEvent, EnrichmentError> {
        // Convert root event to JSON
        let event_json = note_to_json(root_event, txn)?;

        let mut enriched_data = EnrichmentData::default();

        // Process each dependency
        for (dep_name, dep_config) in &self.root_config.deps {
            match self.process_dependency(root_event, dep_config, txn) {
                Ok(result) => {
                    self.attach_dependency_result(&mut enriched_data, dep_name, dep_config, result);
                }
                Err(e) => {
                    if dep_config.required {
                        return Err(e);
                    }
                    // Optional dependency failed, continue
                }
            }
        }

        // AUTOMATIC PROFILE ENRICHMENT: Extract all pubkeys from stats and fetch profiles
        // Always includes the note author + any users who engaged (reacted, zapped, reposted)
        self.enrich_profiles(&mut enriched_data, root_event, txn)?;

        Ok(EnrichedEvent {
            event: event_json,
            enriched: enriched_data,
        })
    }

    /// Process a single dependency configuration
    fn process_dependency<'b>(
        &self,
        root: &Note,
        dep: &DepConfig,
        txn: &'b Transaction,
    ) -> Result<Vec<Note<'b>>, EnrichmentError> {
        let relation = dep.relation.as_str();

        match relation {
            "author" => self.fetch_author(root, dep, txn),
            "e_tag" => self.fetch_e_tag_related(root, dep, txn),
            "p_tag" => self.fetch_p_tag_related(root, dep, txn),
            _ => {
                tracing::warn!("Unknown relation type: {}", relation);
                Ok(vec![])
            }
        }
    }

    /// Fetch author profile (kind 0 where pubkey = root.pubkey)
    fn fetch_author<'b>(&self, root: &Note, _dep: &DepConfig, txn: &'b Transaction) -> Result<Vec<Note<'b>>, EnrichmentError> {
        let pubkey_bytes = root.pubkey();

        let filter = Filter::new()
            .kinds(vec![0]) // Profile metadata
            .authors(vec![pubkey_bytes])
            .limit(1)
            .build();

        let results = self.ndb
            .query(txn, &[filter], 1)
            .map_err(|e| EnrichmentError::QueryFailed(e.to_string()))?;

        // Extract notes from QueryResults
        Ok(results.into_iter().map(|qr| qr.note).collect())
    }

    /// Fetch events with e-tag referencing root event
    fn fetch_e_tag_related<'b>(&self, root: &Note, dep: &DepConfig, txn: &'b Transaction) -> Result<Vec<Note<'b>>, EnrichmentError> {
        let root_id = root.id();

        // Build filter for events with e-tag = root.id
        let mut filter_builder = Filter::new();

        if let Some(kind) = dep.kind {
            filter_builder = filter_builder.kinds(vec![kind]);
        } else if dep.fetch_any_kind {
            // Don't filter by kind, get all events referencing this
        } else {
            return Err(EnrichmentError::InvalidConfig(
                "e_tag relation requires either 'kind' or 'fetch_any_kind=true'".to_string()
            ));
        }

        let filter = filter_builder
            .events(vec![root_id])
            .limit(1000)
            .build();

        let results = self.ndb
            .query(txn, &[filter], 1000)
            .map_err(|e| EnrichmentError::QueryFailed(e.to_string()))?;

        // Extract notes from QueryResults
        Ok(results.into_iter().map(|qr| qr.note).collect())
    }

    /// Fetch events for mentioned pubkeys (p-tags)
    fn fetch_p_tag_related<'b>(&self, _root: &Note, _dep: &DepConfig, _txn: &'b Transaction) -> Result<Vec<Note<'b>>, EnrichmentError> {
        // TODO: Implement p-tag extraction properly
        // For now, return empty vec
        Ok(vec![])
    }

    /// Automatically enrich with profiles for all mentioned users
    fn enrich_profiles(&self, enriched: &mut EnrichmentData, root_event: &Note, txn: &Transaction) -> Result<(), EnrichmentError> {
        // Extract all pubkeys from stats
        let mut pubkeys = std::collections::HashSet::new();

        // ALWAYS include the note author's pubkey
        pubkeys.insert(hex::encode(root_event.pubkey()));

        // Also extract pubkeys from engagement stats (reactions, zaps, reposts)
        for (_stat_name, stat_value) in &enriched.stats {
            extract_pubkeys_from_value(stat_value, &mut pubkeys);
        }

        if pubkeys.is_empty() {
            return Ok(());
        }

        // Convert hex pubkeys to bytes
        let pubkey_bytes: Vec<[u8; 32]> = pubkeys.iter()
            .filter_map(|pk_hex| {
                if pk_hex.len() != 64 {
                    return None;
                }
                let bytes = hex::decode(pk_hex).ok()?;
                let mut array = [0u8; 32];
                array.copy_from_slice(&bytes);
                Some(array)
            })
            .collect();

        if pubkey_bytes.is_empty() {
            return Ok(());
        }

        // Batch fetch all profiles
        let filter = Filter::new()
            .kinds(vec![0])
            .authors(pubkey_bytes.iter().collect::<Vec<_>>())
            .limit(pubkey_bytes.len() as u64)
            .build();

        let results = self.ndb
            .query(txn, &[filter], pubkey_bytes.len() as i32)
            .map_err(|e| EnrichmentError::QueryFailed(e.to_string()))?;

        // Build profiles map: {pubkey: profile_json}
        let mut profiles_map = HashMap::new();
        for query_result in results {
            let note = query_result.note;
            let pubkey = hex::encode(note.pubkey());

            // Parse the content field to get profile data
            if let Ok(content) = serde_json::from_str::<serde_json::Value>(note.content()) {
                profiles_map.insert(pubkey, content);
            }
        }

        // Add profiles to enriched data
        if !profiles_map.is_empty() {
            enriched.single.insert("profiles".to_string(), serde_json::to_value(profiles_map).unwrap());
        }

        Ok(())
    }

    /// Attach dependency result to enriched data based on mode
    fn attach_dependency_result(
        &self,
        enriched: &mut EnrichmentData,
        dep_name: &str,
        dep_config: &DepConfig,
        notes: Vec<Note<'_>>,
    ) {
        let mode = dep_config.mode.as_str();

        match mode {
            "aggregate" => {
                // Compute structured stats (includes user lists, amounts, etc.)
                let stats = aggregate_stats(&notes, &dep_config.stats);
                enriched.stats.insert(dep_name.to_string(), serde_json::to_value(stats).unwrap());
            }
            "expanded" => {
                // Include full events only (no stats)
                let events: Vec<serde_json::Value> = notes.iter()
                    .filter_map(|n| note_to_json_simple(n).ok())
                    .collect();
                enriched.events.insert(dep_name.to_string(), events);
            }
            "" | "single" => {
                // Single event (like author profile)
                if let Some(note) = notes.first() {
                    if let Ok(json) = note_to_json_simple(note) {
                        enriched.single.insert(dep_name.to_string(), json);
                    }
                }
            }
            "tree" => {
                // TODO: Implement tree mode for threads
                tracing::warn!("Tree mode not yet implemented");
            }
            _ => {
                tracing::warn!("Unknown enrichment mode: {}", mode);
            }
        }
    }
}

/// Aggregate statistics from a collection of notes
fn aggregate_stats(notes: &[Note], stat_types: &[String]) -> HashMap<String, serde_json::Value> {
    let mut stats = HashMap::new();

    for stat_type in stat_types {
        match stat_type.as_str() {
            "count" => {
                stats.insert("count".to_string(), serde_json::json!(notes.len()));
            }
            "by_content" => {
                // Group by content field with user lists (for reactions)
                let mut content_groups: HashMap<String, Vec<String>> = HashMap::new();
                for note in notes {
                    let content = note.content().to_string();
                    let pubkey = hex::encode(note.pubkey());
                    content_groups.entry(content).or_insert_with(Vec::new).push(pubkey);
                }

                // Format as {emoji: {count: N, users: [...]}}
                let formatted: HashMap<String, serde_json::Value> = content_groups
                    .into_iter()
                    .map(|(content, users)| {
                        (content, serde_json::json!({
                            "count": users.len(),
                            "users": users
                        }))
                    })
                    .collect();

                stats.insert("by_content".to_string(), serde_json::to_value(formatted).unwrap());
            }
            "total_sats" => {
                // Sum sats from zap receipts
                let total: u64 = notes.iter()
                    .filter_map(|n| extract_zap_amount(n))
                    .sum();
                stats.insert("total_sats".to_string(), serde_json::json!(total));
            }
            "by_user" => {
                // Group by user (for zaps, reposts)
                let by_user: Vec<serde_json::Value> = notes.iter()
                    .map(|note| {
                        let pubkey = hex::encode(note.pubkey());
                        let amount = extract_zap_amount(note);

                        if let Some(sats) = amount {
                            serde_json::json!({
                                "pubkey": pubkey,
                                "amount": sats
                            })
                        } else {
                            serde_json::json!({
                                "pubkey": pubkey
                            })
                        }
                    })
                    .collect();

                stats.insert("by_user".to_string(), serde_json::to_value(by_user).unwrap());
            }
            "users" => {
                // Just list of pubkeys (for reposts)
                let users: Vec<String> = notes.iter()
                    .map(|note| hex::encode(note.pubkey()))
                    .collect();
                stats.insert("users".to_string(), serde_json::to_value(users).unwrap());
            }
            _ => {
                tracing::warn!("Unknown stat type: {}", stat_type);
            }
        }
    }

    stats
}

/// Extract zap amount from a kind 9735 event
fn extract_zap_amount(note: &Note) -> Option<u64> {
    // Look for bolt11 invoice in tags and decode amount
    for tag in note.tags() {
        if tag.count() >= 2 {
            if let Some("bolt11") = tag.get_str(0) {
                if let Some(invoice) = tag.get_str(1) {
                    return parse_bolt11_amount(invoice);
                }
            }
        }
    }
    None
}

/// Parse amount from bolt11 invoice string
fn parse_bolt11_amount(invoice: &str) -> Option<u64> {
    // LNBC invoice format: lnbc[amount][multiplier]...
    // Example: lnbc2500u... = 2500 microsats = 2.5 sats
    // Example: lnbc420n... = 420 nanosats = 0.00042 sats

    if !invoice.starts_with("lnbc") && !invoice.starts_with("lntb") {
        return None;
    }

    let amount_part = &invoice[4..];
    let mut amount_str = String::new();
    let mut multiplier = ' ';

    for c in amount_part.chars() {
        if c.is_ascii_digit() {
            amount_str.push(c);
        } else {
            multiplier = c;
            break;
        }
    }

    if amount_str.is_empty() {
        return None;
    }

    let base_amount: u64 = amount_str.parse().ok()?;

    // Convert to millisats based on multiplier
    let millisats = match multiplier {
        'm' => base_amount,                    // millisats
        'u' => base_amount * 1000,             // microsats (0.001 sats)
        'n' => base_amount / 1000,             // nanosats (0.000001 sats)
        'p' => base_amount / 1_000_000,        // picosats
        _ => return None,
    };

    // Convert millisats to sats
    Some(millisats / 1000)
}

/// Extract pubkeys from p-tags (TODO: implement properly)
fn extract_p_tags<'a>(_note: &'a Note<'a>) -> Vec<&'a [u8]> {
    // TODO: Implement hex to bytes conversion for p-tags
    vec![]
}

/// Recursively extract all pubkey strings from a JSON value
fn extract_pubkeys_from_value(value: &serde_json::Value, pubkeys: &mut std::collections::HashSet<String>) {
    match value {
        serde_json::Value::Object(map) => {
            for (key, val) in map {
                // If the key suggests it's a pubkey field, extract the value
                if key == "pubkey" || key == "users" {
                    if let serde_json::Value::String(pk) = val {
                        if pk.len() == 64 {
                            pubkeys.insert(pk.clone());
                        }
                    } else if let serde_json::Value::Array(arr) = val {
                        for item in arr {
                            if let serde_json::Value::String(pk) = item {
                                if pk.len() == 64 {
                                    pubkeys.insert(pk.clone());
                                }
                            }
                        }
                    }
                }
                // Recursively search nested objects
                extract_pubkeys_from_value(val, pubkeys);
            }
        }
        serde_json::Value::Array(arr) => {
            for item in arr {
                extract_pubkeys_from_value(item, pubkeys);
            }
        }
        _ => {}
    }
}

/// Convert Note to JSON with full transaction context
fn note_to_json(note: &Note, _txn: &Transaction) -> Result<serde_json::Value, EnrichmentError> {
    note_to_json_simple(note)
}

/// Convert Note to JSON (simplified)
fn note_to_json_simple(note: &Note) -> Result<serde_json::Value, EnrichmentError> {
    // Build tags array
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

    Ok(serde_json::json!({
        "id": hex::encode(note.id()),
        "pubkey": hex::encode(note.pubkey()),
        "created_at": note.created_at(),
        "kind": note.kind(),
        "tags": tags,
        "content": note.content(),
        "sig": hex::encode(note.sig()),
    }))
}

#[derive(Debug, thiserror::Error)]
pub enum EnrichmentError {
    #[error("Query failed: {0}")]
    QueryFailed(String),

    #[error("Invalid configuration: {0}")]
    InvalidConfig(String),

    #[error("Required dependency not found: {0}")]
    RequiredDependencyMissing(String),

    #[error("JSON serialization error: {0}")]
    JsonError(#[from] serde_json::Error),
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_bolt11_amount() {
        assert_eq!(parse_bolt11_amount("lnbc2500u1..."), Some(2)); // 2500 micro = 2.5 sats
        assert_eq!(parse_bolt11_amount("lnbc420n1..."), Some(0)); // 420 nano = 0.00042 sats
        assert_eq!(parse_bolt11_amount("lnbc1000000m1..."), Some(1000)); // 1M milli = 1000 sats
    }

    #[test]
    fn test_aggregate_count() {
        // Can't easily test without mock nostrdb Notes
        // Integration tests will cover this
    }
}
