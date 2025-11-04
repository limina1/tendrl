use super::{MergeKind, NotePayload, TimelineUnits, UnknownPks};
use crate::CachedNote;
use nostrdb::{Ndb, Note, Transaction};
use tracing::debug;

#[derive(Copy, Clone, Eq, PartialEq, Debug, Default, PartialOrd, Ord)]
pub enum ViewFilter {
    MentionsOnly,
    Notes,

    #[default]
    NotesAndReplies,

    All,
}

impl ViewFilter {
    pub fn filter_notes(cache: &CachedNote, note: &Note) -> bool {
        note.kind() == 6 || !cache.reply.borrow(note.tags()).is_reply()
    }

    fn identity(_cache: &CachedNote, _note: &Note) -> bool {
        true
    }

    fn notes_and_replies(_cache: &CachedNote, note: &Note) -> bool {
        note.kind() == 1 || note.kind() == 6
    }

    fn mentions_only(cache: &CachedNote, note: &Note) -> bool {
        if note.kind() != 1 {
            return false;
        }

        let note_reply = cache.reply.borrow(note.tags());

        note_reply.is_reply() || note_reply.mention().is_some()
    }

    pub fn filter(&self) -> fn(&CachedNote, &Note) -> bool {
        match self {
            ViewFilter::Notes => ViewFilter::filter_notes,
            ViewFilter::NotesAndReplies => ViewFilter::notes_and_replies,
            ViewFilter::All => ViewFilter::identity,
            ViewFilter::MentionsOnly => ViewFilter::mentions_only,
        }
    }
}

/// A timeline view is a filtered view of notes in a timeline. Two standard views
/// are "Notes" and "Notes & Replies". A timeline is associated with a Filter,
/// but a TimelineTab is a further filtered view of this Filter that can't
/// be captured by a Filter itself.
#[derive(Debug)]
pub struct TimelineTab {
    pub units: TimelineUnits,
    pub selection: i32,
    pub filter: ViewFilter,
}

impl TimelineTab {
    pub fn new(filter: ViewFilter) -> Self {
        TimelineTab::new_with_capacity(filter, 1000)
    }

    pub fn only_notes_and_replies() -> Vec<Self> {
        vec![TimelineTab::new(ViewFilter::NotesAndReplies)]
    }

    pub fn no_replies() -> Vec<Self> {
        vec![TimelineTab::new(ViewFilter::Notes)]
    }

    pub fn full_tabs() -> Vec<Self> {
        vec![
            TimelineTab::new(ViewFilter::Notes),
            TimelineTab::new(ViewFilter::NotesAndReplies),
        ]
    }

    pub fn notifications() -> Vec<Self> {
        vec![
            TimelineTab::new(ViewFilter::All),
            TimelineTab::new(ViewFilter::MentionsOnly),
        ]
    }

    pub fn new_with_capacity(filter: ViewFilter, cap: usize) -> Self {
        let selection = 0i32;

        TimelineTab {
            units: TimelineUnits::with_capacity(cap),
            selection,
            filter,
        }
    }

    pub(crate) fn insert<'a>(
        &mut self,
        payloads: Vec<&'a NotePayload>,
        ndb: &Ndb,
        txn: &Transaction,
        reversed: bool,
        use_front_insert: bool,
    ) -> Option<UnknownPks<'a>> {
        use super::units::InsertManyResponse;

        if payloads.is_empty() {
            return None;
        }

        let num_refs = payloads.len();

        let resp = self.units.merge_new_notes(payloads, ndb, txn);

        let InsertManyResponse::Some {
            entries_merged: _,
            merge_kind,
        } = resp.insertion_response
        else {
            return resp.tl_response;
        };

        // In the full notedeck_columns implementation, this updates VirtualList
        // For daemon use, we just log the merge
        match merge_kind {
            MergeKind::Spliced => {
                debug!("spliced when inserting {num_refs} new notes");
            }
            MergeKind::FrontInsert => {
                if !use_front_insert {
                    // Skip front insert optimization
                } else if !reversed {
                    debug!("inserting {num_refs} new notes at start");
                }
            }
        };

        resp.tl_response
    }

    pub fn select_down(&mut self) {
        debug!("select_down {}", self.selection + 1);
        if self.selection + 1 > self.units.len() as i32 {
            return;
        }

        self.selection += 1;
    }

    pub fn select_up(&mut self) {
        debug!("select_up {}", self.selection - 1);
        if self.selection - 1 < 0 {
            return;
        }

        self.selection -= 1;
    }
}

impl Default for TimelineTab {
    fn default() -> Self {
        TimelineTab::new(ViewFilter::default())
    }
}
