/// Unknown ID tracking for Tendrl daemon
///
/// This is a minimal extraction from Notedeck's UnknownIds system,
/// designed specifically for tendrl_daemon's needs without pulling in
/// heavy dependencies on NoteCache, NoteRef, etc.
///
/// The full version with note parsing lives in Notedeck. This version
/// provides just the tracking container and basic operations.

use enostr::{Filter, NoteId, Pubkey};
use nostr::RelayUrl;
use nostrdb::{Ndb, Transaction};
use std::collections::{HashMap, HashSet};
use std::time::{Duration, Instant};

/// Tracks unknown pubkeys and note IDs that need to be fetched
///
/// This struct maintains a registry of missing data that should be
/// requested from relays. It includes debouncing to avoid spamming
/// relays with requests.
#[derive(Default, Debug)]
pub struct UnknownIds {
    ids: HashMap<UnknownId, HashSet<RelayUrl>>,
    first_updated: Option<Instant>,
    last_updated: Option<Instant>,
}

impl UnknownIds {
    /// Check if enough time has passed to send a request
    ///
    /// This implements a simple debouncer:
    /// - Returns true immediately on first update
    /// - Returns true if 2+ seconds have passed since last update
    /// - Returns false otherwise
    pub fn ready_to_send(&self) -> bool {
        if self.ids.is_empty() {
            return false;
        }

        // Trigger on first set
        if self.first_updated == self.last_updated {
            return true;
        }

        let last_updated = if let Some(last) = self.last_updated {
            last
        } else {
            return true;
        };

        Instant::now() - last_updated >= Duration::from_secs(2)
    }

    /// Iterate over all tracked unknown IDs
    pub fn ids_iter(&self) -> impl ExactSizeIterator<Item = &UnknownId> {
        self.ids.keys()
    }

    /// Get mutable access to the internal ID map
    pub fn ids_mut(&mut self) -> &mut HashMap<UnknownId, HashSet<RelayUrl>> {
        &mut self.ids
    }

    /// Clear all tracked IDs
    pub fn clear(&mut self) {
        self.ids = HashMap::default();
    }

    /// Generate Nostr filters for fetching the tracked IDs
    ///
    /// Returns filters that can be sent to relays to fetch:
    /// - kind-0 (metadata) events for unknown pubkeys
    /// - Events by ID for unknown notes
    pub fn filter(&self) -> Option<Vec<Filter>> {
        let ids: Vec<&UnknownId> = self.ids.keys().collect();
        get_unknown_ids_filter(&ids)
    }

    /// Mark that IDs have been updated
    ///
    /// Updates timestamps for debouncing logic
    pub fn mark_updated(&mut self) {
        let now = Instant::now();
        if self.first_updated.is_none() {
            self.first_updated = Some(now);
        }
        self.last_updated = Some(now);
    }

    /// Add an unknown ID if it's not already tracked
    pub fn add_unknown_id_if_missing(&mut self, ndb: &Ndb, txn: &Transaction, unk_id: &UnknownId) {
        match unk_id {
            UnknownId::Pubkey(pk) => self.add_pubkey_if_missing(ndb, txn, pk),
            UnknownId::Id(note_id) => self.add_note_id_if_missing(ndb, txn, note_id.bytes()),
        }
    }

    /// Add a pubkey to track if we don't have its profile
    pub fn add_pubkey_if_missing(&mut self, ndb: &Ndb, txn: &Transaction, pubkey: &[u8; 32]) {
        // We already have this profile, skip
        if ndb.get_profile_by_pubkey(txn, pubkey).is_ok() {
            return;
        }

        let unknown_id = UnknownId::Pubkey(Pubkey::new(*pubkey));
        if self.ids.contains_key(&unknown_id) {
            return;
        }
        self.ids.entry(unknown_id).or_default();
        self.mark_updated();
    }

    /// Add a note ID to track if we don't have the note
    pub fn add_note_id_if_missing(&mut self, ndb: &Ndb, txn: &Transaction, note_id: &[u8; 32]) {
        // We already have this note, skip
        if ndb.get_note_by_id(txn, note_id).is_ok() {
            return;
        }

        let unknown_id = UnknownId::Id(NoteId::new(*note_id));
        if self.ids.contains_key(&unknown_id) {
            return;
        }
        self.ids.entry(unknown_id).or_default();
        self.mark_updated();
    }
}

/// Represents either an unknown pubkey or note ID
#[derive(Hash, Clone, Copy, PartialEq, Eq, Debug)]
pub enum UnknownId {
    Pubkey(Pubkey),
    Id(NoteId),
}

impl UnknownId {
    /// Returns the pubkey if this is a pubkey variant
    pub fn is_pubkey(&self) -> Option<&Pubkey> {
        match self {
            UnknownId::Pubkey(pk) => Some(pk),
            _ => None,
        }
    }

    /// Returns the note ID if this is an ID variant
    pub fn is_id(&self) -> Option<&NoteId> {
        match self {
            UnknownId::Id(id) => Some(id),
            _ => None,
        }
    }
}

/// Generate Nostr filters for fetching unknown IDs
///
/// Creates two filters:
/// 1. For pubkeys: kind-0 (metadata) events
/// 2. For note IDs: events matching those IDs
///
/// Limits to 500 IDs total to avoid huge requests
fn get_unknown_ids_filter(ids: &[&UnknownId]) -> Option<Vec<Filter>> {
    if ids.is_empty() {
        return None;
    }

    // Limit to 500 IDs to keep requests reasonable
    let ids = &ids[0..500.min(ids.len())];
    let mut filters: Vec<Filter> = vec![];

    // Filter for unknown pubkeys (fetch their profiles)
    let pks: Vec<&[u8; 32]> = ids
        .iter()
        .flat_map(|id| id.is_pubkey().map(|pk| pk.bytes()))
        .collect();
    if !pks.is_empty() {
        let pk_filter = Filter::new().authors(pks).kinds([0]).build();
        filters.push(pk_filter);
    }

    // Filter for unknown note IDs
    let note_ids: Vec<&[u8; 32]> = ids
        .iter()
        .flat_map(|id| id.is_id().map(|id| id.bytes()))
        .collect();
    if !note_ids.is_empty() {
        filters.push(Filter::new().ids(note_ids).build());
    }

    Some(filters)
}
