//! Follow list management
//!
//! This module handles loading and extracting follow lists from kind-3 contact list events.

use nostrdb::{Filter, Ndb, Transaction};

/// Load follow list for a given user pubkey
///
/// Queries nostrdb for the user's kind-3 event and extracts all p-tags (follows)
pub fn load_follow_list(ndb: &Ndb, user_pubkey: &str) -> Result<Vec<String>, FollowListError> {
    // Parse hex pubkey
    let pubkey_bytes = hex::decode(user_pubkey)
        .map_err(|e| FollowListError::InvalidPubkey(e.to_string()))?;

    if pubkey_bytes.len() != 32 {
        return Err(FollowListError::InvalidPubkey(
            "Pubkey must be 32 bytes".to_string(),
        ));
    }

    let mut pubkey_array = [0u8; 32];
    pubkey_array.copy_from_slice(&pubkey_bytes);

    // Query for kind-3 event
    let txn = Transaction::new(ndb)
        .map_err(|e| FollowListError::DatabaseError(e.to_string()))?;

    let filter = Filter::new()
        .kinds(vec![3])
        .authors(vec![&pubkey_array])
        .limit(1)
        .build();

    let results = ndb
        .query(&txn, &[filter], 1)
        .map_err(|e| FollowListError::QueryFailed(e.to_string()))?;

    // Extract follows from p-tags
    let mut follows = Vec::new();

    for query_result in results {
        let note = query_result.note;

        for tag in note.tags() {
            if tag.count() >= 2 {
                // Check if it's a p-tag
                if let Some("p") = tag.get_str(0) {
                    // Get the pubkey (might be stored as ID or string)
                    if let Some(ndb_str) = tag.get(1) {
                        let pubkey_hex = if let Some(s) = ndb_str.str() {
                            s.to_string()
                        } else if let Some(id) = ndb_str.id() {
                            hex::encode(id)
                        } else {
                            continue;
                        };

                        follows.push(pubkey_hex);
                    }
                }
            }
        }
    }

    if follows.is_empty() {
        return Err(FollowListError::NoFollowsFound);
    }

    tracing::info!(
        "Loaded {} follows for user {}",
        follows.len(),
        &user_pubkey[..8]
    );

    Ok(follows)
}

#[derive(Debug, thiserror::Error)]
pub enum FollowListError {
    #[error("Invalid pubkey format: {0}")]
    InvalidPubkey(String),

    #[error("Database error: {0}")]
    DatabaseError(String),

    #[error("Query failed: {0}")]
    QueryFailed(String),

    #[error("No follow list found (kind-3 event missing or empty)")]
    NoFollowsFound,
}
