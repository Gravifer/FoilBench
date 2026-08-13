//! Explicit repository resource resolution for the native CLI.

use std::{
    env,
    path::{Path, PathBuf},
};

#[derive(Clone, Debug)]
pub struct ResourceResolver {
    root: PathBuf,
}

impl ResourceResolver {
    /// Locate the repository from an explicit path, `FOILBENCH_ROOT`, or a
    /// parent of the current directory.
    ///
    /// # Errors
    ///
    /// Returns an error when no directory contains the specification manifest.
    pub fn discover(explicit: Option<&Path>) -> Result<Self, String> {
        let selected = explicit
            .map(Path::to_path_buf)
            .or_else(|| env::var_os("FOILBENCH_ROOT").map(PathBuf::from));
        if let Some(root) = selected {
            return Self::new(&root);
        }
        let current = env::current_dir().map_err(|error| error.to_string())?;
        for candidate in current.ancestors() {
            if Self::is_root(candidate) {
                return Ok(Self {
                    root: candidate.to_path_buf(),
                });
            }
        }
        Err("could not locate FoilBench repository root".into())
    }

    /// Validate an explicit repository root.
    ///
    /// # Errors
    ///
    /// Returns an error when the expected specification manifest is absent.
    pub fn new(root: &Path) -> Result<Self, String> {
        let absolute = root.canonicalize().map_err(|error| error.to_string())?;
        if !Self::is_root(&absolute) {
            return Err("resource root lacks spec/contract-version.json".into());
        }
        Ok(Self { root: absolute })
    }

    fn is_root(path: &Path) -> bool {
        path.join("spec/contract-version.json").is_file()
    }

    #[must_use]
    pub fn root(&self) -> &Path {
        &self.root
    }

    #[must_use]
    pub fn resolve(&self, path: impl AsRef<Path>) -> PathBuf {
        let selected = path.as_ref();
        if selected.is_absolute() {
            selected.to_path_buf()
        } else {
            self.root.join(selected)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::ResourceResolver;

    #[test]
    fn discovers_repository_resources() {
        let resolver = ResourceResolver::discover(None).unwrap();
        assert!(
            resolver
                .resolve("spec/schemas/scenario.schema.json")
                .is_file()
        );
    }
}
