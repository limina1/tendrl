use crate::config::{FeedPattern, LocalQuery, RemoteFilter};
use crate::filter::{HybridFilter, NdbQueryPackage, ValidKind};
use nostrdb::Filter;

/// Build a HybridFilter from a TOML feed pattern
pub fn build_hybrid_filter(pattern: &FeedPattern) -> HybridFilter {
    let local = build_local_packages(&pattern.local_queries);
    let remote = build_remote_filters(&pattern.remote_filters);

    HybridFilter::split(local, remote)
}

/// Convert TOML local queries into NdbQueryPackage vector
fn build_local_packages(queries: &[LocalQuery]) -> Vec<NdbQueryPackage> {
    queries
        .iter()
        .map(|query| {
            let mut filter_builder = Filter::new()
                .kinds(query.kinds.clone())
                .limit(query.limit);

            // Add authors filter if provided
            if !query.authors.is_empty() {
                // Convert hex strings to byte arrays
                let author_refs: Vec<[u8; 32]> = query.authors
                    .iter()
                    .filter_map(|hex_str| {
                        let bytes = hex::decode(hex_str).ok()?;
                        if bytes.len() != 32 {
                            return None;
                        }
                        let mut array = [0u8; 32];
                        array.copy_from_slice(&bytes);
                        Some(array)
                    })
                    .collect();

                let author_refs_slice: Vec<&[u8; 32]> = author_refs.iter().collect();
                filter_builder = filter_builder.authors(author_refs_slice);
            }

            let filters = vec![filter_builder.build()];

            NdbQueryPackage {
                filters,
                kind: map_kind_to_valid(query.kinds.first().copied().unwrap_or(1)),
            }
        })
        .collect()
}

/// Convert TOML remote filters into Filter vector
fn build_remote_filters(filters: &[RemoteFilter]) -> Vec<Filter> {
    filters
        .iter()
        .map(|filter_spec| {
            let mut filter_builder = Filter::new()
                .kinds(filter_spec.kinds.clone())
                .limit(filter_spec.limit);

            // Add authors filter if provided
            if !filter_spec.authors.is_empty() {
                // Convert hex strings to byte arrays
                let author_refs: Vec<[u8; 32]> = filter_spec.authors
                    .iter()
                    .filter_map(|hex_str| {
                        let bytes = hex::decode(hex_str).ok()?;
                        if bytes.len() != 32 {
                            return None;
                        }
                        let mut array = [0u8; 32];
                        array.copy_from_slice(&bytes);
                        Some(array)
                    })
                    .collect();

                let author_refs_slice: Vec<&[u8; 32]> = author_refs.iter().collect();
                filter_builder = filter_builder.authors(author_refs_slice);
            }

            filter_builder.build()
        })
        .collect()
}

/// Map a kind number to ValidKind enum (for optimization)
fn map_kind_to_valid(kind: u64) -> ValidKind {
    match kind {
        0 => ValidKind::Zero,
        1 => ValidKind::One,
        6 => ValidKind::Six,
        other => ValidKind::Other(other), // Use Other variant for custom kinds
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::{LocalQuery, RemoteFilter, FeedPattern};

    #[test]
    fn test_build_local_packages() {
        let queries = vec![
            LocalQuery {
                kinds: vec![1],
                limit: 100,
                note: "".to_string(),
                authors: vec![],
            },
            LocalQuery {
                kinds: vec![6],
                limit: 50,
                note: "".to_string(),
                authors: vec![],
            },
        ];

        let packages = build_local_packages(&queries);
        assert_eq!(packages.len(), 2);
        assert_eq!(packages[0].filters.len(), 1);
        assert_eq!(packages[1].filters.len(), 1);
    }

    #[test]
    fn test_build_remote_filters() {
        let filters = vec![
            RemoteFilter {
                kinds: vec![1, 6],
                limit: 250,
                note: "".to_string(),
                relays: vec![],
                authors: vec![],
            },
        ];

        let remote = build_remote_filters(&filters);
        assert_eq!(remote.len(), 1);
    }

    #[test]
    fn test_build_hybrid_filter() {
        let pattern = FeedPattern {
            description: "Test".to_string(),
            filter_type: "hybrid".to_string(),
            local_queries: vec![LocalQuery {
                kinds: vec![1],
                limit: 500,
                note: "".to_string(),
                authors: vec![],
            }],
            remote_filters: vec![RemoteFilter {
                kinds: vec![1, 0],
                limit: 250,
                note: "".to_string(),
                relays: vec![],
                authors: vec![],
            }],
            enrichment: "client_computed".to_string(),
            relationship_building: "".to_string(),
            lazy_load: false,
            subscription_mode: "".to_string(),
            parse_bolt11: false,
            parse_zap_request: false,
        };

        let hybrid = build_hybrid_filter(&pattern);

        // Verify structure (this doesn't crash = success)
        let _ = hybrid.local();
        let _ = hybrid.remote();
    }

    #[test]
    fn test_map_kind_to_valid_custom() {
        // Test that custom kinds map to ValidKind::Other
        let kind = map_kind_to_valid(30023);
        match kind {
            ValidKind::Other(30023) => {},
            _ => panic!("Expected ValidKind::Other(30023)"),
        }
    }
}
