//! NIP-11: Relay Information Document
//!
//! Support for querying relay metadata via HTTP to discover capabilities,
//! limitations, and server attributes.
//!
//! See: https://github.com/nostr-protocol/nips/blob/master/11.md

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// Complete NIP-11 relay information document
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RelayInfo {
    /// Relay name (recommended < 30 characters)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,

    /// Detailed description (plain text, no markup)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,

    /// Banner image URL
    #[serde(skip_serializing_if = "Option::is_none")]
    pub banner: Option<String>,

    /// Icon image URL (recommended square)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub icon: Option<String>,

    /// Administrative contact pubkey (hex)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pubkey: Option<String>,

    /// Alternative contact (mailto: or https:)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub contact: Option<String>,

    /// List of supported NIP numbers
    #[serde(skip_serializing_if = "Option::is_none")]
    pub supported_nips: Option<Vec<u32>>,

    /// Relay software URL
    #[serde(skip_serializing_if = "Option::is_none")]
    pub software: Option<String>,

    /// Software version identifier
    #[serde(skip_serializing_if = "Option::is_none")]
    pub version: Option<String>,

    /// Privacy policy URL
    #[serde(skip_serializing_if = "Option::is_none")]
    pub privacy_policy: Option<String>,

    /// Terms of service URL
    #[serde(skip_serializing_if = "Option::is_none")]
    pub terms_of_service: Option<String>,

    /// Server limitations (most important for query optimization)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub limitation: Option<Limitation>,

    /// Event retention policies
    #[serde(skip_serializing_if = "Option::is_none")]
    pub retention: Option<Vec<RetentionPolicy>>,

    /// Countries whose laws may affect this relay
    #[serde(skip_serializing_if = "Option::is_none")]
    pub relay_countries: Option<Vec<String>>,

    /// Language tags for relay community
    #[serde(skip_serializing_if = "Option::is_none")]
    pub language_tags: Option<Vec<String>>,

    /// Content/topic tags
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tags: Option<Vec<String>>,

    /// Posting policy URL
    #[serde(skip_serializing_if = "Option::is_none")]
    pub posting_policy: Option<String>,

    /// Payment URL
    #[serde(skip_serializing_if = "Option::is_none")]
    pub payments_url: Option<String>,

    /// Fee schedules
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fees: Option<Fees>,

    /// Additional fields not in NIP-11 spec
    #[serde(flatten)]
    pub extra: HashMap<String, serde_json::Value>,
}

/// Server limitations - critical for query optimization
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Limitation {
    /// Maximum incoming JSON bytes (affects max event size)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_message_length: Option<u64>,

    /// Maximum subscriptions per WebSocket connection
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_subscriptions: Option<u32>,

    /// Maximum subscription ID length
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_subid_length: Option<u32>,

    /// Relay clamps filter `limit` to this value (MOST IMPORTANT)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_limit: Option<u32>,

    /// Maximum event tags count
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_event_tags: Option<u32>,

    /// Maximum content field length (unicode characters)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_content_length: Option<u64>,

    /// Minimum PoW difficulty required
    #[serde(skip_serializing_if = "Option::is_none")]
    pub min_pow_difficulty: Option<u32>,

    /// NIP-42 auth required before any action
    #[serde(skip_serializing_if = "Option::is_none")]
    pub auth_required: Option<bool>,

    /// Payment required before any action
    #[serde(skip_serializing_if = "Option::is_none")]
    pub payment_required: Option<bool>,

    /// Restricted writes (whitelist, etc)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub restricted_writes: Option<bool>,

    /// created_at lower limit (seconds)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub created_at_lower_limit: Option<i64>,

    /// created_at upper limit (seconds)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub created_at_upper_limit: Option<i64>,

    /// Default limit if not specified in filter
    #[serde(skip_serializing_if = "Option::is_none")]
    pub default_limit: Option<u32>,
}

/// Event retention policy
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RetentionPolicy {
    /// Event kinds this policy applies to
    #[serde(skip_serializing_if = "Option::is_none")]
    pub kinds: Option<Vec<KindOrRange>>,

    /// Retention time in seconds (null = forever, 0 = reject)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub time: Option<i64>,

    /// Maximum count of events to retain
    #[serde(skip_serializing_if = "Option::is_none")]
    pub count: Option<u32>,
}

/// Event kind or range [start, end]
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(untagged)]
pub enum KindOrRange {
    Single(u32),
    Range(u32, u32),
}

/// Fee schedules for pay-to-relay
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Fees {
    /// One-time admission fee
    #[serde(skip_serializing_if = "Option::is_none")]
    pub admission: Option<Vec<FeeSchedule>>,

    /// Recurring subscription fee
    #[serde(skip_serializing_if = "Option::is_none")]
    pub subscription: Option<Vec<FeeSchedule>>,

    /// Per-publication fees
    #[serde(skip_serializing_if = "Option::is_none")]
    pub publication: Option<Vec<PublicationFee>>,
}

/// Fee schedule entry
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FeeSchedule {
    /// Amount
    pub amount: u64,

    /// Unit (msats, sats, etc)
    pub unit: String,

    /// Period in seconds (for subscriptions)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub period: Option<u64>,
}

/// Publication fee (per kind)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PublicationFee {
    /// Event kinds this fee applies to
    #[serde(skip_serializing_if = "Option::is_none")]
    pub kinds: Option<Vec<u32>>,

    /// Amount
    pub amount: u64,

    /// Unit
    pub unit: String,
}

impl RelayInfo {
    /// Get the maximum limit this relay will accept
    /// Returns the relay's max_limit, or None if not specified
    pub fn max_limit(&self) -> Option<u32> {
        self.limitation.as_ref()?.max_limit
    }

    /// Get the default limit the relay uses when none specified
    pub fn default_limit(&self) -> Option<u32> {
        self.limitation.as_ref()?.default_limit
    }

    /// Check if relay requires payment
    pub fn requires_payment(&self) -> bool {
        self.limitation
            .as_ref()
            .and_then(|l| l.payment_required)
            .unwrap_or(false)
    }

    /// Check if relay requires auth
    pub fn requires_auth(&self) -> bool {
        self.limitation
            .as_ref()
            .and_then(|l| l.auth_required)
            .unwrap_or(false)
    }

    /// Check if relay has restricted writes
    pub fn has_restricted_writes(&self) -> bool {
        self.limitation
            .as_ref()
            .and_then(|l| l.restricted_writes)
            .unwrap_or(false)
    }

    /// Get optimized limit for queries (respects relay's max_limit)
    pub fn optimize_limit(&self, requested_limit: u32) -> u32 {
        match self.max_limit() {
            Some(max) => requested_limit.min(max),
            None => requested_limit,
        }
    }
}

/// Fetch relay information via NIP-11
///
/// # Arguments
/// * `relay_url` - WebSocket URL (wss://...) or HTTP URL (https://...)
///
/// # Returns
/// RelayInfo if successful, error otherwise
///
/// # Example
/// ```ignore
/// let info = fetch_relay_info("wss://relay.damus.io").await?;
/// println!("Relay max_limit: {:?}", info.max_limit());
/// ```
pub async fn fetch_relay_info(relay_url: &str) -> Result<RelayInfo, FetchError> {
    // Convert WebSocket URL to HTTP
    let http_url = relay_url
        .replace("wss://", "https://")
        .replace("ws://", "http://");

    // Build HTTP client with timeout
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(10))
        .build()
        .map_err(|e| FetchError::Http(e.to_string()))?;

    // Fetch with NIP-11 Accept header
    let response = client
        .get(&http_url)
        .header("Accept", "application/nostr+json")
        .send()
        .await
        .map_err(|e| FetchError::Http(e.to_string()))?;

    // Check status
    if !response.status().is_success() {
        return Err(FetchError::HttpStatus(response.status().as_u16()));
    }

    // Parse JSON
    let info: RelayInfo = response
        .json()
        .await
        .map_err(|e| FetchError::Parse(e.to_string()))?;

    Ok(info)
}

/// Errors that can occur when fetching relay info
#[derive(Debug, thiserror::Error)]
pub enum FetchError {
    #[error("HTTP error: {0}")]
    Http(String),

    #[error("HTTP status {0}")]
    HttpStatus(u16),

    #[error("Parse error: {0}")]
    Parse(String),
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_optimize_limit() {
        let info = RelayInfo {
            limitation: Some(Limitation {
                max_limit: Some(5000),
                ..Default::default()
            }),
            ..Default::default()
        };

        // Requested limit below max - should return requested
        assert_eq!(info.optimize_limit(100), 100);
        assert_eq!(info.optimize_limit(5000), 5000);

        // Requested limit above max - should clamp to max
        assert_eq!(info.optimize_limit(10000), 5000);
        assert_eq!(info.optimize_limit(100000), 5000);
    }

    #[test]
    fn test_requires_payment() {
        let mut info = RelayInfo::default();
        assert!(!info.requires_payment());

        info.limitation = Some(Limitation {
            payment_required: Some(true),
            ..Default::default()
        });
        assert!(info.requires_payment());
    }

    #[test]
    fn test_parse_example() {
        // Example from NIP-11
        let json = r#"{
            "name": "JellyFish",
            "limitation": {
                "max_limit": 5000,
                "default_limit": 500,
                "payment_required": true
            }
        }"#;

        let info: RelayInfo = serde_json::from_str(json).unwrap();
        assert_eq!(info.name, Some("JellyFish".to_string()));
        assert_eq!(info.max_limit(), Some(5000));
        assert_eq!(info.default_limit(), Some(500));
        assert!(info.requires_payment());
    }
}

impl Default for RelayInfo {
    fn default() -> Self {
        Self {
            name: None,
            description: None,
            banner: None,
            icon: None,
            pubkey: None,
            contact: None,
            supported_nips: None,
            software: None,
            version: None,
            privacy_policy: None,
            terms_of_service: None,
            limitation: None,
            retention: None,
            relay_countries: None,
            language_tags: None,
            tags: None,
            posting_policy: None,
            payments_url: None,
            fees: None,
            extra: HashMap::new(),
        }
    }
}

impl Default for Limitation {
    fn default() -> Self {
        Self {
            max_message_length: None,
            max_subscriptions: None,
            max_subid_length: None,
            max_limit: None,
            max_event_tags: None,
            max_content_length: None,
            min_pow_difficulty: None,
            auth_required: None,
            payment_required: None,
            restricted_writes: None,
            created_at_lower_limit: None,
            created_at_upper_limit: None,
            default_limit: None,
        }
    }
}
