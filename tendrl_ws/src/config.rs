//! Configuration system for tendrl_ws
//!
//! Config-driven feed definitions following the nostr-feeds pattern.
//! Uses nostrdb as the source of truth.

use serde::Deserialize;
use std::collections::HashMap;
use std::path::Path;
use anyhow::Result;

/// Root configuration structure
#[derive(Debug, Deserialize, Clone)]
pub struct Config {
    pub global: GlobalConfig,
    #[serde(default)]
    pub feed: HashMap<String, FeedConfig>,
}

/// Global configuration settings
#[derive(Debug, Deserialize, Clone)]
pub struct GlobalConfig {
    #[serde(default = "default_template_dir")]
    pub template_dir: String,
    #[serde(default = "default_limit")]
    pub default_limit: u64,
    #[serde(default)]
    pub default_relays: Vec<String>,
}

fn default_template_dir() -> String {
    "templates".to_string()
}

fn default_limit() -> u64 {
    50
}

/// Feed configuration
#[derive(Debug, Deserialize, Clone)]
pub struct FeedConfig {
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub display_kinds: Vec<u64>,
    #[serde(default)]
    pub fetch_on_startup: bool,
    #[serde(default = "default_refresh_interval")]
    pub refresh_interval: u64,
    #[serde(default)]
    pub relays: Vec<String>,
    pub root: RootConfig,
}

fn default_refresh_interval() -> u64 {
    300
}

/// Root event configuration
#[derive(Debug, Deserialize, Clone)]
pub struct RootConfig {
    pub kind: u64,
    #[serde(default)]
    pub template: Option<String>,
    #[serde(default)]
    pub deps: HashMap<String, DependencyConfig>,
}

/// Dependency configuration
#[derive(Debug, Deserialize, Clone)]
pub struct DependencyConfig {
    #[serde(default)]
    pub kind: Option<u64>,
    pub relation: String,
    #[serde(default)]
    pub mode: DependencyMode,
    #[serde(default)]
    pub required: bool,
    #[serde(default)]
    pub fetch_on_demand: bool,
    #[serde(default)]
    pub fetch_any_kind: bool,
    #[serde(default)]
    pub max_depth: Option<u64>,
    #[serde(default)]
    pub stats: Vec<String>,
    #[serde(default)]
    pub expandable: bool,
}

/// Dependency fetch mode
#[derive(Debug, Deserialize, Clone, Default, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum DependencyMode {
    #[default]
    Single,
    Aggregate,
    Expanded,
    Tree,
}

/// Relation types for dependencies
#[derive(Debug, Clone, PartialEq)]
pub enum RelationType {
    /// Fetch author profile (kind 0) by pubkey
    Author,
    /// Fetch events referencing this event via 'e' tag
    ETag,
    /// Fetch events referencing this addressable event via 'a' tag
    ATag,
    /// Fetch addressable events listed IN this event's 'a' tags
    ATagChildren,
    /// Sender profile (for zaps)
    Sender,
    /// Parent author
    ParentAuthor,
    /// Target author
    TargetAuthor,
    /// Unknown/custom relation
    Custom(String),
}

impl From<&str> for RelationType {
    fn from(s: &str) -> Self {
        match s {
            "author" => RelationType::Author,
            "e_tag" => RelationType::ETag,
            "a_tag" => RelationType::ATag,
            "a_tag_children" => RelationType::ATagChildren,
            "sender" => RelationType::Sender,
            "parent_author" => RelationType::ParentAuthor,
            "target_author" => RelationType::TargetAuthor,
            other => RelationType::Custom(other.to_string()),
        }
    }
}

impl Config {
    /// Load configuration from a TOML file
    pub fn load<P: AsRef<Path>>(path: P) -> Result<Self> {
        let content = std::fs::read_to_string(path)?;
        let config: Config = toml::from_str(&content)?;
        Ok(config)
    }

    /// Load configuration from the default location
    pub fn load_default() -> Result<Self> {
        // Try multiple locations
        let locations = [
            "tendrl.toml",
            "config/tendrl.toml",
            "../tendrl.toml",
        ];

        for loc in locations {
            if Path::new(loc).exists() {
                return Self::load(loc);
            }
        }

        // Return default config if no file found
        Ok(Self::default())
    }

    /// Get a feed configuration by name
    pub fn get_feed(&self, name: &str) -> Option<&FeedConfig> {
        self.feed.get(name)
    }

    /// Get all feeds that should be fetched on startup
    pub fn startup_feeds(&self) -> Vec<&FeedConfig> {
        self.feed
            .values()
            .filter(|f| f.fetch_on_startup)
            .collect()
    }

    /// Get relays for a specific feed, falling back to global defaults
    pub fn relays_for_feed(&self, feed_name: &str) -> Vec<String> {
        if let Some(feed) = self.feed.get(feed_name) {
            if !feed.relays.is_empty() {
                return feed.relays.clone();
            }
        }
        self.global.default_relays.clone()
    }
}

impl Default for Config {
    fn default() -> Self {
        Self {
            global: GlobalConfig {
                template_dir: default_template_dir(),
                default_limit: default_limit(),
                default_relays: vec![
                    "wss://relay.damus.io".to_string(),
                    "wss://nos.lol".to_string(),
                    "wss://relay.nostr.band".to_string(),
                ],
            },
            feed: HashMap::new(),
        }
    }
}

impl FeedConfig {
    /// Get the kinds to fetch for this feed (from display_kinds or root.kind)
    pub fn fetch_kinds(&self) -> Vec<u64> {
        if self.display_kinds.is_empty() {
            vec![self.root.kind]
        } else {
            self.display_kinds.clone()
        }
    }

    /// Get dependency by name
    pub fn get_dep(&self, name: &str) -> Option<&DependencyConfig> {
        self.root.deps.get(name)
    }

    /// Get all dependencies with a specific relation type
    pub fn deps_by_relation(&self, relation: &str) -> Vec<(&String, &DependencyConfig)> {
        self.root
            .deps
            .iter()
            .filter(|(_, dep)| dep.relation == relation)
            .collect()
    }
}

impl DependencyConfig {
    /// Parse the relation string into a RelationType
    pub fn relation_type(&self) -> RelationType {
        RelationType::from(self.relation.as_str())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_relation_types() {
        assert_eq!(RelationType::from("author"), RelationType::Author);
        assert_eq!(RelationType::from("e_tag"), RelationType::ETag);
        assert_eq!(RelationType::from("a_tag"), RelationType::ATag);
        assert_eq!(RelationType::from("a_tag_children"), RelationType::ATagChildren);
        assert_eq!(
            RelationType::from("custom_thing"),
            RelationType::Custom("custom_thing".to_string())
        );
    }

    #[test]
    fn test_default_config() {
        let config = Config::default();
        assert_eq!(config.global.default_limit, 50);
        assert!(!config.global.default_relays.is_empty());
    }
}
