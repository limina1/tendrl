use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::path::Path;

/// Main configuration structure parsed from tendrl.toml
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct TendrlConfig {
    #[serde(default)]
    pub feed: HashMap<String, FeedDefinition>,
}

impl TendrlConfig {
    /// Load tendrl.toml from the given path
    pub fn load_from_path(path: &Path) -> Result<Self, TendrlConfigError> {
        let content = fs::read_to_string(path)
            .map_err(|e| TendrlConfigError::IoError(e.to_string()))?;

        let config: TendrlConfig = toml::from_str(&content)
            .map_err(|e| TendrlConfigError::ParseError(e.to_string()))?;

        Ok(config)
    }

    /// Get all feed IDs
    pub fn feed_ids(&self) -> Vec<String> {
        self.feed.keys().cloned().collect()
    }

    /// Get a specific feed definition
    pub fn get_feed(&self, id: &str) -> Option<&FeedDefinition> {
        self.feed.get(id)
    }
}

/// Definition of a custom feed
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct FeedDefinition {
    pub name: String,
    pub description: String,

    #[serde(default)]
    pub relay_mode: String,  // "custom", "general"

    #[serde(default)]
    pub relays: Vec<String>,

    #[serde(default)]
    pub fetch_strategy: String,  // "manual", "stream"

    #[serde(default)]
    pub filter_by_follows: bool,

    #[serde(default)]
    pub refetch_engagement: bool,

    pub display_kinds: Vec<u64>,

    pub pattern: FeedPattern,

    #[serde(default)]
    pub root: Option<RootConfig>,
}

/// Feed fetching pattern (how to query events)
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct FeedPattern {
    #[serde(default)]
    pub description: String,

    pub filter_type: String,  // "hybrid"

    pub local_queries: Vec<LocalQuery>,
    pub remote_filters: Vec<RemoteFilter>,

    #[serde(default)]
    pub enrichment: String,  // "client_computed"

    #[serde(default)]
    pub relationship_building: String,  // "e_tag_and_a_tag", "a_tag_tree", etc

    #[serde(default)]
    pub lazy_load: bool,

    #[serde(default)]
    pub subscription_mode: String,  // "streaming", "polling"

    #[serde(default)]
    pub parse_bolt11: bool,

    #[serde(default)]
    pub parse_zap_request: bool,
}

/// Local query specification (for nostrdb)
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct LocalQuery {
    pub kinds: Vec<u64>,

    #[serde(default = "default_limit")]
    pub limit: u64,

    #[serde(default)]
    pub note: String,  // Optional description/comment
}

/// Remote filter specification (for relays)
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct RemoteFilter {
    pub kinds: Vec<u64>,

    #[serde(default = "default_limit")]
    pub limit: u64,

    #[serde(default)]
    pub note: String,  // Optional description/comment

    #[serde(default)]
    pub relays: Vec<String>,  // Optional specific relays
}

/// Root event configuration (UI and dependencies)
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct RootConfig {
    pub kind: u64,

    #[serde(default)]
    pub template: String,

    #[serde(default)]
    pub deps: HashMap<String, DepConfig>,
}

/// Dependency configuration for event enrichment
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct DepConfig {
    #[serde(default)]
    pub kind: Option<u64>,

    pub relation: String,  // "author", "e_tag", "a_tag", "p_tag", etc

    #[serde(default)]
    pub required: bool,

    #[serde(default)]
    pub multiple: bool,

    #[serde(default)]
    pub mode: String,  // "tree", "expanded", "aggregate"

    #[serde(default)]
    pub max_depth: Option<u64>,

    #[serde(default)]
    pub fetch_any_kind: bool,
}

fn default_limit() -> u64 {
    500
}

#[derive(Debug, thiserror::Error)]
pub enum TendrlConfigError {
    #[error("Failed to read config file: {0}")]
    IoError(String),

    #[error("Failed to parse TOML: {0}")]
    ParseError(String),
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_basic_config() {
        let toml = r#"
[feed.test]
name = "Test Feed"
description = "A test feed"
display_kinds = [1]

[feed.test.pattern]
filter_type = "hybrid"

[[feed.test.pattern.local_queries]]
kinds = [1]
limit = 100

[[feed.test.pattern.remote_filters]]
kinds = [1]
limit = 50
"#;

        let config: TendrlConfig = toml::from_str(toml).unwrap();
        assert_eq!(config.feed.len(), 1);

        let feed = config.get_feed("test").unwrap();
        assert_eq!(feed.name, "Test Feed");
        assert_eq!(feed.display_kinds, vec![1]);
        assert_eq!(feed.pattern.local_queries.len(), 1);
        assert_eq!(feed.pattern.remote_filters.len(), 1);
    }
}
