use nostrdb::Filter;

/// The local and remote filter are related but slightly different
#[derive(Debug, Clone)]
pub struct SplitFilter {
    pub local: Vec<NdbQueryPackage>,
    pub remote: Vec<Filter>,
}

/// Either a [`SplitFilter`] or a regular unsplit filter. Split filters
/// have different remote and local filters but are tracked together.
#[derive(Debug, Clone)]
pub enum HybridFilter {
    Split(SplitFilter),
    Unsplit(Vec<Filter>),
}

impl HybridFilter {
    pub fn unsplit(filter: Vec<Filter>) -> Self {
        HybridFilter::Unsplit(filter)
    }

    pub fn split(local: Vec<NdbQueryPackage>, remote: Vec<Filter>) -> Self {
        HybridFilter::Split(SplitFilter { local, remote })
    }

    pub fn local(&self) -> NdbQueryPackages<'_> {
        match self {
            Self::Split(split) => NdbQueryPackages {
                packages: split.local.iter().map(NdbQueryPackage::borrow).collect(),
            },

            // local is the same as remote in unsplit
            Self::Unsplit(local) => NdbQueryPackages {
                packages: vec![NdbQueryPackageUnowned {
                    filters: local,
                    kind: None,
                }],
            },
        }
    }

    pub fn remote(&self) -> &[Filter] {
        match self {
            Self::Split(split) => &split.remote,

            // local is the same as remote in unsplit
            Self::Unsplit(remote) => remote,
        }
    }
}

/// `Ndb::query` retrieves the most recent notes of one kind until it can't find anymore THEN proceeds to the next kind.
/// This is not optimal for many scenarios, so this data structure represents data that is packaged optimally for one `Ndb::query`.
#[derive(Debug, Clone)]
pub struct NdbQueryPackage {
    pub filters: Vec<Filter>,
    pub kind: ValidKind,
}

impl NdbQueryPackage {
    pub fn borrow(&self) -> NdbQueryPackageUnowned<'_> {
        NdbQueryPackageUnowned {
            filters: &self.filters,
            kind: Some(self.kind.clone()),
        }
    }
}

#[derive(Debug, Clone)]
pub struct NdbQueryPackageUnowned<'a> {
    pub kind: Option<ValidKind>,
    pub filters: &'a Vec<Filter>,
}

pub struct NdbQueryPackages<'a> {
    pub packages: Vec<NdbQueryPackageUnowned<'a>>,
}

impl<'a> NdbQueryPackages<'a> {
    pub fn combined(&self) -> Vec<Filter> {
        let mut combined = Vec::new();
        for package in &self.packages {
            combined.extend_from_slice(package.filters);
        }

        combined
    }
}

#[derive(Debug, Clone)]
pub enum ValidKind {
    Zero,
    One,
    Six,
    Other(u64), // For custom kinds
}

impl ValidKind {
    pub fn kind(&self) -> u64 {
        match self {
            ValidKind::Zero => 0,
            ValidKind::One => 1,
            ValidKind::Six => 6,
            ValidKind::Other(k) => *k,
        }
    }
}
