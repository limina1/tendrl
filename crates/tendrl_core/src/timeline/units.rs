use std::collections::{HashMap, HashSet};

use enostr::Pubkey;
use nostrdb::{Ndb, Note, NoteKey, Transaction};
use crate::NoteRef;

// Use UnknownIds from tendrl_core
pub use crate::UnknownIds;

use super::note_types::{
    CompositeFragment, CompositeKey, NoteUnit, NoteUnitFragment, Reaction,
    ReactionFragment, RepostFragment, UnitKey,
};
use super::MergeKind;

pub struct NotePayload<'a> {
    pub note: Note<'a>,
    pub key: NoteKey,
}

impl<'a> NotePayload<'a> {
    pub fn noteref(&self) -> NoteRef {
        NoteRef {
            key: self.key,
            created_at: self.note.created_at(),
        }
    }
}

#[derive(Debug, Default)]
pub struct TimelineUnits {
    pub units: NoteUnits,
}

impl TimelineUnits {
    pub fn with_capacity(cap: usize) -> Self {
        Self {
            units: NoteUnits::new_with_cap(cap, false),
        }
    }

    pub fn from_refs_single(refs: Vec<NoteRef>) -> Self {
        let mut entries = TimelineUnits::default();
        refs.into_iter().for_each(|r| entries.merge_single_note(r));
        entries
    }

    pub fn len(&self) -> usize {
        self.units.len()
    }

    pub fn is_empty(&self) -> bool {
        self.units.len() == 0
    }

    /// returns number of new entries merged
    pub fn merge_new_notes<'a>(
        &mut self,
        payloads: Vec<&'a NotePayload>,
        ndb: &Ndb,
        txn: &Transaction,
    ) -> MergeResponse<'a> {
        let mut unknown_pks = HashSet::with_capacity(payloads.len());
        let new_fragments = payloads
            .into_iter()
            .filter_map(|p| to_fragment(p, ndb, txn))
            .map(|f| {
                if let Some(pk) = f.unknown_pk {
                    unknown_pks.insert(pk);
                }
                f.fragment
            })
            .collect();

        let tl_response = if unknown_pks.is_empty() {
            None
        } else {
            Some(UnknownPks { unknown_pks })
        };

        MergeResponse {
            insertion_response: self.units.merge_fragments(new_fragments),
            tl_response,
        }
    }

    pub fn latest(&self) -> Option<&NoteRef> {
        self.units.latest_ref()
    }

    pub fn merge_single_note(&mut self, note_ref: NoteRef) {
        self.units.merge_single_unit(note_ref);
    }

    /// Used in the view
    pub fn get(&self, index: usize) -> Option<&NoteUnit> {
        self.units.kth(index)
    }
}

pub struct MergeResponse<'a> {
    pub insertion_response: InsertManyResponse,
    pub tl_response: Option<UnknownPks<'a>>,
}

pub struct UnknownPks<'a> {
    pub(crate) unknown_pks: HashSet<&'a [u8; 32]>,
}

impl<'a> UnknownPks<'a> {
    pub fn process(&self, unknown_ids: &mut UnknownIds, ndb: &Ndb, txn: &Transaction) {
        for pk in &self.unknown_pks {
            unknown_ids.add_pubkey_if_missing(ndb, txn, pk);
        }
    }
}

pub struct NoteUnitFragmentResponse<'a> {
    pub fragment: NoteUnitFragment,
    pub unknown_pk: Option<&'a [u8; 32]>,
}

fn to_fragment<'a>(
    payload: &'a NotePayload,
    ndb: &Ndb,
    txn: &Transaction,
) -> Option<NoteUnitFragmentResponse<'a>> {
    match payload.note.kind() {
        1 => Some(NoteUnitFragmentResponse {
            fragment: NoteUnitFragment::Single(NoteRef {
                key: payload.key,
                created_at: payload.note.created_at(),
            }),
            unknown_pk: None,
        }),
        7 => to_reaction(payload, ndb, txn).map(|r| NoteUnitFragmentResponse {
            fragment: NoteUnitFragment::Composite(CompositeFragment::Reaction(r.fragment)),
            unknown_pk: Some(r.pk),
        }),
        6 => to_repost(payload, ndb, txn).map(RepostResponse::into),
        _ => None,
    }
}

fn to_reaction<'a>(
    payload: &'a NotePayload,
    ndb: &Ndb,
    txn: &Transaction,
) -> Option<ReactionResponse<'a>> {
    let reaction = payload.note.content();

    let mut note_reacted_to = None;

    for tag in payload.note.tags() {
        if tag.count() < 2 {
            continue;
        }

        let Some("e") = tag.get_str(0) else {
            continue;
        };

        let Some(react_to_id) = tag.get_id(1) else {
            continue;
        };

        note_reacted_to = Some(react_to_id);
    }

    let reacted_to_noteid = note_reacted_to?;

    let reaction_note_ref = payload.noteref();

    let reacted_to_note = ndb.get_note_by_id(txn, reacted_to_noteid).ok()?;

    let noteref_reacted_to = NoteRef {
        key: reacted_to_note.key()?,
        created_at: reacted_to_note.created_at(),
    };

    let sender_profilekey = ndb
        .get_profile_by_pubkey(txn, payload.note.pubkey())
        .ok()
        .and_then(|p| p.key());

    Some(ReactionResponse {
        fragment: ReactionFragment {
            noteref_reacted_to,
            reaction_note_ref,
            reaction: Reaction {
                reaction: reaction.to_string(),
                sender: Pubkey::new(*payload.note.pubkey()),
                sender_profilekey,
            },
        },
        pk: payload.note.pubkey(),
    })
}

pub struct ReactionResponse<'a> {
    fragment: ReactionFragment,
    pk: &'a [u8; 32], // reaction sender
}

pub struct RepostResponse<'a> {
    fragment: RepostFragment,
    reposter_pk: &'a [u8; 32],
}

impl<'a> From<RepostResponse<'a>> for NoteUnitFragmentResponse<'a> {
    fn from(value: RepostResponse<'a>) -> Self {
        Self {
            fragment: NoteUnitFragment::Composite(CompositeFragment::Repost(value.fragment)),
            unknown_pk: Some(value.reposter_pk),
        }
    }
}

/// Helper to get reposted note from a kind-6 repost event
fn get_reposted_note<'a>(
    ndb: &Ndb,
    txn: &'a Transaction,
    note: &Note,
) -> Option<Note<'a>> {
    // Check for "e" tag which points to the reposted note
    for tag in note.tags() {
        if tag.count() >= 2 {
            if let Some("e") = tag.get_str(0) {
                if let Some(ndb_str) = tag.get(1) {
                    // Try to get the note ID
                    if let Some(id) = ndb_str.id() {
                        // Direct ID bytes
                        if let Ok(note) = ndb.get_note_by_id(txn, id) {
                            return Some(note);
                        }
                    } else if let Some(hex_str) = ndb_str.str() {
                        // Try to decode hex string to bytes
                        if let Ok(bytes) = hex::decode(hex_str) {
                            if bytes.len() == 32 {
                                let mut arr = [0u8; 32];
                                arr.copy_from_slice(&bytes);
                                if let Ok(note) = ndb.get_note_by_id(txn, &arr) {
                                    return Some(note);
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    None
}

fn to_repost<'a>(
    payload: &'a NotePayload,
    ndb: &Ndb,
    txn: &Transaction,
) -> Option<RepostResponse<'a>> {
    let reposted_note = match get_reposted_note(ndb, txn, &payload.note) {
        Some(r) => r,
        None => {
            tracing::debug!(
                "Could not get reposted note for note id {}",
                enostr::NoteId::new(*payload.note.id()).hex()
            );
            return None;
        }
    };

    let reposted_key = match reposted_note.key() {
        Some(r) => r,
        None => {
            tracing::error!(
                "Could not get key of reposted note {}",
                enostr::NoteId::new(*reposted_note.id()).hex()
            );
            return None;
        }
    };

    Some(RepostResponse {
        fragment: RepostFragment {
            reposted_noteref: NoteRef {
                key: reposted_key,
                created_at: reposted_note.created_at(),
            },
            repost_noteref: payload.noteref(),
            reposter: Pubkey::new(*payload.note.pubkey()),
        },
        reposter_pk: payload.note.pubkey(),
    })
}

type StorageIndex = usize;

/// Provides efficient access to `NoteUnit`s
/// Useful for threads and timelines
/// when reversed=false, sorts from newest to oldest
#[derive(Debug, Default)]
pub struct NoteUnits {
    reversed: bool,
    storage: Vec<NoteUnit>,
    lookup: HashMap<UnitKey, StorageIndex>, // the key to index in `NoteUnits::storage`
    order: Vec<StorageIndex>, // the sorted order of the `NoteUnit`s in `NoteUnits::storage`
}

impl NoteUnits {
    pub fn contains_key(&self, k: &UnitKey) -> bool {
        self.lookup.contains_key(k)
    }

    pub fn new_with_cap(cap: usize, reversed: bool) -> Self {
        Self {
            reversed,
            storage: Vec::with_capacity(cap),
            lookup: HashMap::with_capacity(cap),
            order: Vec::with_capacity(cap),
        }
    }

    pub fn len(&self) -> usize {
        self.storage.len()
    }

    pub fn is_empty(&self) -> bool {
        self.storage.is_empty()
    }

    /// Get the kth index from 0..Self::len
    pub fn kth(&self, k: usize) -> Option<&NoteUnit> {
        if k >= self.order.len() {
            return None;
        }
        let idx = if self.reversed {
            self.order[self.order.len() - 1 - k]
        } else {
            self.order[k]
        };
        Some(&self.storage[idx])
    }

    /// Core bulk insert for already-built `NoteUnit`s
    /// Merges new `NoteUnit`s into `Self::storage`
    /// Updates `Self::order`
    fn merge_many_internal(
        &mut self,
        mut units: Vec<NoteUnit>,
        touched_indices: &[usize],
    ) -> InsertManyResponse {
        units.retain(|e| !self.lookup.contains_key(&e.key()));
        if units.is_empty() && touched_indices.is_empty() {
            return InsertManyResponse::Zero;
        }

        let mut touched = Vec::new();
        if !touched_indices.is_empty() {
            touched = touched_indices.to_vec();
            touched.sort_unstable(); // sort for later reinsertion
            touched.dedup();
            self.order.retain(|i| touched.binary_search(i).is_err()); // temporarily remove touched from Self::order
        }

        units.sort_unstable();
        units.dedup_by_key(|u| u.key());

        let base = self.storage.len();
        let mut new_order = Vec::with_capacity(units.len());
        self.storage.reserve(units.len());
        for (i, unit) in units.into_iter().enumerate() {
            let idx = base + i;
            let key = unit.key();
            self.storage.push(unit);
            self.lookup.insert(key, idx);
            new_order.push(idx);
        }

        let inserted_new = new_order.len();

        let front_insertion = if self.order.is_empty() || new_order.is_empty() {
            !new_order.is_empty()
        } else if self.reversed {
            // reversed is true, sorting should occur less recent to most recent (oldest to newest, opposite of `self.order`)
            let first_new = *new_order.first().unwrap(); // most recent unit of the new order
            let last_old = *self.order.last().unwrap(); // least recent unit of the current order

            // if the most recent unit of the new order is less recent than the least recent unit of the current order,
            // all current order units are less recent than the new order units.
            // In other words, they are all being inserted in the front
            self.storage[first_new] >= self.storage[last_old]
        } else {
            // reversed is false, sorting should occur most recent to least recent (newest to oldest, as it is in `self.order`)
            let last_new = *new_order.last().unwrap(); // least recent unit of the new order
            let first_old = *self.order.first().unwrap(); // most recent unit of the current order

            // if the least recent unit of the new order is more recent than the most recent unit of the current order,
            // all new units are more recent than the current units.
            // In other words, they are all being inserted in the front
            self.storage[last_new] <= self.storage[first_old]
        };

        let mut merged = Vec::with_capacity(self.order.len() + new_order.len());
        let (mut i, mut j) = (0, 0);
        while i < self.order.len() && j < new_order.len() {
            let index_left = self.order[i];
            let index_right = new_order[j];
            let left_unit = &self.storage[index_left];
            let right_unit = &self.storage[index_right];
            if left_unit <= right_unit {
                // the left unit is more recent than (or the same recency as) the right unit
                merged.push(index_left);
                i += 1;
            } else {
                merged.push(index_right);
                j += 1;
            }
        }
        merged.extend_from_slice(&self.order[i..]);
        merged.extend_from_slice(&new_order[j..]);

        // reinsert touched
        for touched_index in touched {
            let pos = merged
                .binary_search_by(|&i2| self.storage[i2].cmp(&self.storage[touched_index]))
                .unwrap_or_else(|p| p);
            merged.insert(pos, touched_index);
        }

        self.order = merged;

        if inserted_new == 0 {
            InsertManyResponse::Zero
        } else if front_insertion {
            InsertManyResponse::Some {
                entries_merged: inserted_new,
                merge_kind: MergeKind::FrontInsert,
            }
        } else {
            InsertManyResponse::Some {
                entries_merged: inserted_new,
                merge_kind: MergeKind::Spliced,
            }
        }
    }

    /// Merges `NoteUnitFragment`s
    /// `NoteUnitFragment::Single` is added normally
    /// if `NoteUnitFragment::Composite` exists already, it will fold the fragment into the `CompositeUnit`
    /// otherwise, it will generate the `NoteUnit::CompositeUnit` from the `NoteUnitFragment::Composite`
    pub fn merge_fragments(&mut self, frags: Vec<NoteUnitFragment>) -> InsertManyResponse {
        use super::note_types::CompositeUnit;

        let mut to_build: HashMap<CompositeKey, CompositeUnit> = HashMap::new(); // new composites by key
        let mut singles_to_build: Vec<NoteRef> = Vec::new();
        let mut singles_seen: HashSet<NoteKey> = HashSet::new();

        let mut touched = Vec::new();
        for frag in frags {
            match frag {
                NoteUnitFragment::Single(note_ref) => {
                    let key = note_ref.key;
                    if self.lookup.contains_key(&UnitKey::Single(key)) {
                        continue;
                    }
                    if singles_seen.insert(key) {
                        singles_to_build.push(note_ref);
                    }
                }
                NoteUnitFragment::Composite(c_frag) => {
                    let key = c_frag.get_underlying_noteref().key;
                    let composite_type = c_frag.get_type();

                    if let Some(&storage_idx) = self.lookup.get(&UnitKey::Composite(c_frag.key())) {
                        if let Some(NoteUnit::Composite(c_unit)) = self.storage.get_mut(storage_idx)
                        {
                            if c_frag.get_latest_ref() < c_unit.get_latest_ref() {
                                touched.push(storage_idx);
                            }
                            c_frag.fold_into(c_unit);
                            continue;
                        }
                    }
                    // aggregate for new composite
                    use std::collections::hash_map::Entry;
                    match to_build.entry(CompositeKey {
                        key,
                        composite_type,
                    }) {
                        Entry::Occupied(mut o) => {
                            c_frag.fold_into(o.get_mut());
                        }
                        Entry::Vacant(v) => {
                            v.insert(c_frag.into());
                        }
                    }
                }
            }
        }

        let mut items: Vec<NoteUnit> = Vec::with_capacity(singles_to_build.len() + to_build.len());
        items.extend(singles_to_build.into_iter().map(NoteUnit::Single));
        items.extend(to_build.into_values().map(NoteUnit::Composite));

        self.merge_many_internal(items, &touched)
    }

    /// Convenience method to merge a single note
    pub fn merge_single_unit(&mut self, note_ref: NoteRef) -> InsertionResponse {
        match self.merge_many_internal(vec![NoteUnit::Single(note_ref)], &[]) {
            InsertManyResponse::Zero => InsertionResponse::AlreadyExists,
            InsertManyResponse::Some {
                entries_merged: _,
                merge_kind,
            } => InsertionResponse::Merged(merge_kind),
        }
    }

    pub fn latest_ref(&self) -> Option<&NoteRef> {
        if self.reversed {
            self.order.last().map(|&i| &self.storage[i])
        } else {
            self.order.first().map(|&i| &self.storage[i])
        }
        .map(NoteUnit::get_latest_ref)
    }
}

pub enum InsertManyResponse {
    Zero,
    Some {
        entries_merged: usize,
        merge_kind: MergeKind,
    },
}

pub enum InsertionResponse {
    AlreadyExists,
    Merged(MergeKind),
}
