/// What kind of timeline is it?
/// For tendrl daemon, we primarily use Generic for custom feeds defined in tendrl.toml
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum TimelineKind {
    /// Generic filter, references a hash of a filter (used by daemon for custom feeds)
    Generic(u64),

    /// Custom TOML-defined feed from tendrl.toml
    Custom(String),
}

impl TimelineKind {
    /// Some feeds are not realtime, like certain algo feeds
    pub fn should_subscribe_locally(&self) -> bool {
        match self {
            TimelineKind::Generic(_) => true,
            TimelineKind::Custom(_) => true,
        }
    }
}
