use std::fmt;

#[derive(Debug)]
pub enum Error {
    NoActiveSubscription,
    NoteCache(String),
    Other(String),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::NoActiveSubscription => write!(f, "No active subscription found"),
            Error::NoteCache(msg) => write!(f, "Note cache error: {}", msg),
            Error::Other(msg) => write!(f, "{}", msg),
        }
    }
}

impl std::error::Error for Error {}

pub type Result<T> = std::result::Result<T, Error>;
