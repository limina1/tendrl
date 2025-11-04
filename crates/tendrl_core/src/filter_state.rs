use crate::filter::HybridFilter;
use nostrdb::{Filter, Subscription};
use std::collections::HashMap;

/// Filter construction errors
#[derive(Debug, Clone, Copy, Eq, PartialEq, thiserror::Error)]
pub enum FilterError {
    #[error("empty contact list")]
    EmptyContactList,

    #[error("filter not ready")]
    FilterNotReady,
}

/// A unified subscription has a local and remote component. The remote subid
/// tracks data received remotely, and local tracks the local subscription.
#[derive(Debug, Clone)]
pub struct UnifiedSubscription {
    pub local: Subscription,
    pub remote: String,
}

/// Types of remote data being fetched
#[derive(Debug, Clone)]
pub enum FetchingRemoteType {
    Normal(UnifiedSubscription),
    Contact,
}

/// Types of remote data that have been received
#[derive(Debug, Clone)]
pub enum GotRemoteType {
    Normal(Subscription),
    Contact,
}

/// We may need to fetch some data from relays before our filter is ready.
/// [`FilterState`] tracks this progression through the filter lifecycle.
#[derive(Debug, Clone)]
pub enum FilterState {
    /// Filter needs remote data before it can be built
    NeedsRemote,

    /// Currently fetching remote data required for the filter
    FetchingRemote(FetchingRemoteType),

    /// Remote data has been received
    GotRemote(GotRemoteType),

    /// Filter is ready to use
    Ready(HybridFilter),

    /// Filter construction failed
    Broken(FilterError),
}

impl FilterState {
    /// Mark the filter as broken due to an error during construction
    pub fn broken(reason: FilterError) -> Self {
        Self::Broken(reason)
    }

    /// Create a ready filter from a vector of Filter objects
    pub fn ready(filter: Vec<Filter>) -> Self {
        Self::Ready(HybridFilter::unsplit(filter))
    }

    /// Create a ready filter from a pre-built HybridFilter
    pub fn ready_hybrid(filter: HybridFilter) -> Self {
        Self::Ready(filter)
    }

    /// Indicate that remote data is needed before the filter can be built
    pub fn needs_remote() -> Self {
        Self::NeedsRemote
    }

    /// Mark that remote data has been received with a local subscription
    pub fn got_remote(local_sub: Subscription) -> Self {
        Self::GotRemote(GotRemoteType::Normal(local_sub))
    }

    /// Mark that a remote subscription has been sent to fetch required data
    pub fn fetching_remote(sub_id: String, local_sub: Subscription) -> Self {
        let unified_sub = UnifiedSubscription {
            local: local_sub,
            remote: sub_id,
        };
        Self::FetchingRemote(FetchingRemoteType::Normal(unified_sub))
    }
}

/// Container for multiple filter states (per relay + initial)
#[derive(Debug, Clone)]
pub struct FilterStates {
    pub initial_state: FilterState,
    pub states: HashMap<String, FilterState>,
}

impl FilterStates {
    pub fn new(initial_state: FilterState) -> Self {
        FilterStates {
            initial_state,
            states: HashMap::new(),
        }
    }

    pub fn ready_hybrid(filter: HybridFilter) -> Self {
        FilterStates {
            initial_state: FilterState::Ready(filter),
            states: HashMap::new(),
        }
    }

    pub fn get_mut(&mut self, relay: &str) -> &FilterState {
        // if our initial state is ready, then just use that
        if let FilterState::Ready(_) = self.initial_state {
            &self.initial_state
        } else {
            // otherwise we look at relay states
            if !self.states.contains_key(relay) {
                self.states
                    .insert(relay.to_string(), self.initial_state.clone());
            }
            self.states.get(relay).unwrap()
        }
    }

    pub fn get_any_ready(&self) -> Option<&HybridFilter> {
        if let FilterState::Ready(fs) = &self.initial_state {
            Some(fs)
        } else {
            for (_k, v) in self.states.iter() {
                if let FilterState::Ready(ref fs) = v {
                    return Some(fs);
                }
            }
            None
        }
    }
}
