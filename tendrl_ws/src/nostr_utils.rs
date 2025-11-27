//! Nostr utilities for bech32 encoding/decoding of identifiers
//!
//! Provides functions for working with naddr, nevent, and other Nostr identifiers
//! following NIP-19 specification.

use anyhow::{anyhow, Result};
use nostr::nips::nip01::Coordinate;
use nostr::nips::nip19::{Nip19, Nip19Event};
use nostr::{EventId, FromBech32, Kind, PublicKey, ToBech32};

/// Identifier type for publication routing (matches gc-alexandria)
#[derive(Debug, Clone, PartialEq)]
pub enum IdentifierType {
    /// Raw hex event ID
    Id,
    /// Just the d-tag (searches across all authors)
    DTag,
    /// Full naddr (kind:pubkey:d-tag encoded as bech32)
    Naddr,
    /// nevent (event ID with optional relay hints)
    Nevent,
}

impl IdentifierType {
    pub fn as_str(&self) -> &'static str {
        match self {
            IdentifierType::Id => "id",
            IdentifierType::DTag => "d",
            IdentifierType::Naddr => "naddr",
            IdentifierType::Nevent => "nevent",
        }
    }

    pub fn from_str(s: &str) -> Option<Self> {
        match s {
            "id" => Some(IdentifierType::Id),
            "d" => Some(IdentifierType::DTag),
            "naddr" => Some(IdentifierType::Naddr),
            "nevent" => Some(IdentifierType::Nevent),
            _ => None,
        }
    }
}

/// Decoded naddr components
#[derive(Debug, Clone)]
pub struct NaddrData {
    pub kind: u64,
    pub pubkey: String,
    pub identifier: String,  // d-tag
    pub relays: Vec<String>,
}

/// Decoded nevent components
#[derive(Debug, Clone)]
pub struct NeventData {
    pub id: String,
    pub relays: Vec<String>,
    pub author: Option<String>,
    pub kind: Option<u64>,
}

/// Encode an naddr from components
/// Note: relay hints are not currently supported in encoding (only decoding)
pub fn encode_naddr(kind: u64, pubkey: &str, d_tag: &str, _relays: &[String]) -> Result<String> {
    let pubkey = PublicKey::parse(pubkey)
        .map_err(|e| anyhow!("Invalid pubkey: {}", e))?;

    let kind = Kind::from(kind as u16);

    let coordinate = Coordinate::new(kind, pubkey)
        .identifier(d_tag);

    coordinate.to_bech32().map_err(|e| anyhow!("Failed to encode naddr: {}", e))
}

/// Decode an naddr string to its components
pub fn decode_naddr(naddr: &str) -> Result<NaddrData> {
    let nip19 = Nip19::from_bech32(naddr)
        .map_err(|e| anyhow!("Failed to decode naddr: {}", e))?;

    match nip19 {
        Nip19::Coordinate(coord) => {
            Ok(NaddrData {
                kind: coord.kind.as_u16() as u64,
                pubkey: coord.public_key.to_hex(),
                identifier: coord.identifier,
                relays: coord.relays.into_iter().map(|r| r.to_string()).collect(),
            })
        }
        _ => Err(anyhow!("Not an naddr: expected Coordinate")),
    }
}

/// Encode a nevent from components
pub fn encode_nevent(id: &str, relays: &[String], author: Option<&str>, kind: Option<u64>) -> Result<String> {
    let event_id = EventId::parse(id)
        .map_err(|e| anyhow!("Invalid event ID: {}", e))?;

    // Build relay list
    let relay_strs: Vec<&str> = relays.iter().map(|s| s.as_str()).collect();

    let mut nip19_event = Nip19Event::new(event_id, relay_strs);

    // Add author if provided
    if let Some(author_hex) = author {
        if let Ok(pubkey) = PublicKey::parse(author_hex) {
            nip19_event = nip19_event.author(pubkey);
        }
    }

    // Add kind if provided
    if let Some(k) = kind {
        nip19_event = nip19_event.kind(Kind::from(k as u16));
    }

    nip19_event.to_bech32().map_err(|e| anyhow!("Failed to encode nevent: {}", e))
}

/// Decode a nevent string to its components
pub fn decode_nevent(nevent: &str) -> Result<NeventData> {
    let nip19 = Nip19::from_bech32(nevent)
        .map_err(|e| anyhow!("Failed to decode nevent: {}", e))?;

    match nip19 {
        Nip19::Event(event) => {
            Ok(NeventData {
                id: event.event_id.to_hex(),
                relays: event.relays.into_iter().map(|r| r.to_string()).collect(),
                author: event.author.map(|p| p.to_hex()),
                kind: event.kind.map(|k| k.as_u16() as u64),
            })
        }
        _ => Err(anyhow!("Not a nevent: expected Event")),
    }
}

/// Try to detect the identifier type from a string
pub fn detect_identifier_type(s: &str) -> IdentifierType {
    if s.starts_with("naddr1") {
        IdentifierType::Naddr
    } else if s.starts_with("nevent1") {
        IdentifierType::Nevent
    } else if s.len() == 64 && s.chars().all(|c| c.is_ascii_hexdigit()) {
        IdentifierType::Id
    } else {
        // Assume d-tag for anything else
        IdentifierType::DTag
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_naddr_roundtrip() {
        let kind = 30040u64;
        let pubkey = "7bdef7be22dd8e59f4600e044aa53a1cf975a9dc7d27df5833bc77db784a5805";
        let d_tag = "test-publication";
        let relays = vec!["wss://relay.damus.io".to_string()];

        let encoded = encode_naddr(kind, pubkey, d_tag, &relays).unwrap();
        assert!(encoded.starts_with("naddr1"));

        let decoded = decode_naddr(&encoded).unwrap();
        assert_eq!(decoded.kind, kind);
        assert_eq!(decoded.pubkey, pubkey);
        assert_eq!(decoded.identifier, d_tag);
    }

    #[test]
    fn test_detect_identifier_type() {
        assert_eq!(
            detect_identifier_type("naddr1qqxnzdesxgunsd3s"),
            IdentifierType::Naddr
        );
        assert_eq!(
            detect_identifier_type("nevent1qqst8cujky046negxgwwm5ynqwn53t8atevy"),
            IdentifierType::Nevent
        );
        // 64 hex chars = valid event ID
        assert_eq!(
            detect_identifier_type("abc123def456789012345678901234567890123456789012345678901234abcd"),
            IdentifierType::Id
        );
        assert_eq!(
            detect_identifier_type("my-publication-title"),
            IdentifierType::DTag
        );
    }
}
