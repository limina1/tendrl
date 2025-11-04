pub mod kinds;
pub mod subscription;
pub mod tabs;
pub mod units;
pub mod note_types;

pub use kinds::*;
pub use subscription::*;
pub use tabs::*;
pub use units::*;
pub use note_types::*;

use crate::{FilterState, FilterStates, NoteCache, UnknownIds};
use nostrdb::{Ndb, NoteKey, Transaction};

use crate::Result;

/// A timeline manages notes for a specific feed, handling both local (nostrdb)
/// and remote (relay) subscriptions.
#[derive(Debug)]
pub struct Timeline {
    pub kind: TimelineKind,
    pub filter: FilterStates,
    pub views: Vec<TimelineTab>,
    pub selected_view: usize,
    pub seen_latest_notes: bool,
    pub subscription: TimelineSub,
    pub enable_front_insert: bool,
}

impl Timeline {
    pub fn new(kind: TimelineKind, filter_state: FilterState, views: Vec<TimelineTab>) -> Self {
        let filter = FilterStates::new(filter_state);
        let subscription = TimelineSub::default();
        let selected_view = 0;
        let enable_front_insert = true; // Always true for daemon use

        Timeline {
            kind,
            filter,
            views,
            subscription,
            selected_view,
            enable_front_insert,
            seen_latest_notes: false,
        }
    }

    pub fn current_view(&self) -> &TimelineTab {
        &self.views[self.selected_view]
    }

    pub fn current_view_mut(&mut self) -> &mut TimelineTab {
        &mut self.views[self.selected_view]
    }

    /// Get the note refs for the filter with the widest scope
    pub fn all_or_any_entries(&self) -> &TimelineUnits {
        let widest_filter = self
            .views
            .iter()
            .map(|view| view.filter)
            .max()
            .expect("at least one filter exists");

        self.entries(widest_filter)
            .expect("should have at least notes")
    }

    pub fn entries(&self, view: ViewFilter) -> Option<&TimelineUnits> {
        self.view(view).map(|v| &v.units)
    }

    pub fn view(&self, view: ViewFilter) -> Option<&TimelineTab> {
        self.views.iter().find(|tab| tab.filter == view)
    }

    pub fn view_mut(&mut self, view: ViewFilter) -> Option<&mut TimelineTab> {
        self.views.iter_mut().find(|tab| tab.filter == view)
    }

    /// The main function used for inserting notes into timelines. Handles
    /// inserting into multiple views if we have them. All timeline note
    /// insertions should use this function.
    pub fn insert(
        &mut self,
        new_note_ids: &[NoteKey],
        ndb: &Ndb,
        txn: &Transaction,
        unknown_ids: &mut UnknownIds,
        note_cache: &mut NoteCache,
        reversed: bool,
    ) -> Result<()> {
        let mut payloads: Vec<NotePayload> = Vec::with_capacity(new_note_ids.len());

        for key in new_note_ids {
            let note = if let Ok(note) = ndb.get_note_by_key(txn, *key) {
                note
            } else {
                tracing::error!(
                    "hit race condition in poll_notes_into_view: note {:?} was not added to timeline",
                    key
                );
                continue;
            };

            // Ensure that unknown ids are captured when inserting notes into the timeline
            UnknownIds::update_from_note(txn, ndb, unknown_ids, note_cache, &note);

            payloads.push(NotePayload { note, key: *key });
        }

        for view in &mut self.views {
            let should_include = view.filter.filter();
            let mut filtered_payloads = Vec::with_capacity(payloads.len());
            for payload in &payloads {
                let cached_note = note_cache.cached_note_or_insert(payload.key, &payload.note);

                if should_include(cached_note, &payload.note) {
                    filtered_payloads.push(payload);
                }
            }

            if let Some(res) = view.insert(
                filtered_payloads,
                ndb,
                txn,
                reversed,
                self.enable_front_insert,
            ) {
                res.process(unknown_ids, ndb, txn);
            }
        }

        Ok(())
    }

    #[profiling::function]
    pub fn poll_notes_into_view(
        &mut self,
        ndb: &Ndb,
        txn: &Transaction,
        unknown_ids: &mut UnknownIds,
        note_cache: &mut NoteCache,
        reversed: bool,
    ) -> Result<()> {
        if !self.kind.should_subscribe_locally() {
            // don't need to poll for timelines that don't have local subscriptions
            return Ok(());
        }

        let sub = self
            .subscription
            .get_local()
            .ok_or_else(|| crate::error::Error::NoActiveSubscription)?;

        let new_note_ids = ndb.poll_for_notes(sub, 500);
        if new_note_ids.is_empty() {
            return Ok(());
        } else {
            self.seen_latest_notes = false;
        }

        self.insert(&new_note_ids, ndb, txn, unknown_ids, note_cache, reversed)
    }
}

pub enum MergeKind {
    FrontInsert,
    Spliced,
}
