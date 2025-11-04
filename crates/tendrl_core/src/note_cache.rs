use nostrdb::{Note, NoteKey, NoteReply, NoteReplyBuf};
use std::collections::HashMap;

/// Reference to a note in nostrdb
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct NoteRef {
    pub key: NoteKey,
    pub created_at: u64,
}

impl NoteRef {
    pub fn new(key: NoteKey, created_at: u64) -> Self {
        NoteRef { key, created_at }
    }

    pub fn key(&self) -> NoteKey {
        self.key
    }

    pub fn created_at(&self) -> u64 {
        self.created_at
    }
}

impl PartialOrd for NoteRef {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for NoteRef {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        // Sort by created_at descending (newest first)
        match self.created_at.cmp(&other.created_at) {
            std::cmp::Ordering::Equal => self.key.cmp(&other.key),
            std::cmp::Ordering::Less => std::cmp::Ordering::Greater,
            std::cmp::Ordering::Greater => std::cmp::Ordering::Less,
        }
    }
}

/// Helper function to extract a tag value from a note by tag name
///
/// Searches through a note's tags to find the first tag with the given name
/// and returns its value (the second element of the tag).
///
/// # Example
/// ```ignore
/// // For a note with tags: [["client", "Damus"], ["e", "abc123"]]
/// let client = event_tag(&note, "client"); // Some("Damus")
/// let reply = event_tag(&note, "e"); // Some("abc123")
/// ```
pub fn event_tag<'a>(ev: &Note<'a>, name: &str) -> Option<&'a str> {
    ev.tags().iter().find_map(|tag| {
        if tag.count() < 2 {
            return None;
        }

        let cur_name = tag.get_str(0)?;

        if cur_name != name {
            return None;
        }

        tag.get_str(1)
    })
}

/// Cached metadata for a note
///
/// Stores processed information about a note that doesn't need to be
/// recomputed every time the note is accessed.
#[derive(Clone)]
pub struct CachedNote {
    /// Client application that created the note (from "client" tag)
    pub client: Option<String>,
    /// Reply information extracted from note tags
    pub reply: NoteReplyBuf,
}

impl CachedNote {
    /// Create a new cached note from a raw note
    ///
    /// Extracts and caches:
    /// - The "client" tag value if present
    /// - Reply information from e/p tags
    pub fn new(note: &Note) -> Self {
        let reply = NoteReply::new(note.tags()).to_owned();
        let client = event_tag(note, "client");

        CachedNote {
            client: client.map(|c| c.to_string()),
            reply,
        }
    }
}

/// Cache for note metadata
///
/// Maintains a cache of processed note information to avoid recomputing
/// tag parsing and reply extraction on every access.
#[derive(Default)]
pub struct NoteCache {
    pub cache: HashMap<NoteKey, CachedNote>,
}

impl NoteCache {
    /// Get a cached note or insert it if not present (mutable reference)
    ///
    /// If the note is already cached, returns a mutable reference to it.
    /// Otherwise, creates a new CachedNote from the raw note and inserts it.
    pub fn cached_note_or_insert_mut(&mut self, note_key: NoteKey, note: &Note) -> &mut CachedNote {
        self.cache
            .entry(note_key)
            .or_insert_with(|| CachedNote::new(note))
    }

    /// Get a cached note by key
    ///
    /// Returns None if the note is not in the cache.
    pub fn cached_note(&self, note_key: NoteKey) -> Option<&CachedNote> {
        self.cache.get(&note_key)
    }

    /// Get mutable access to the underlying cache HashMap
    pub fn cache_mut(&mut self) -> &mut HashMap<NoteKey, CachedNote> {
        &mut self.cache
    }

    /// Get a cached note or insert it if not present (immutable reference)
    ///
    /// If the note is already cached, returns an immutable reference to it.
    /// Otherwise, creates a new CachedNote from the raw note and inserts it.
    pub fn cached_note_or_insert(&mut self, note_key: NoteKey, note: &Note) -> &CachedNote {
        self.cache
            .entry(note_key)
            .or_insert_with(|| CachedNote::new(note))
    }
}
