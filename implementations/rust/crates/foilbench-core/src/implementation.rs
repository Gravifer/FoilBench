use serde::Serialize;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum ExecutionTarget {
    Native,
    WasmBrowser,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct ImplementationDescription {
    pub implementation: &'static str,
    pub execution_target: ExecutionTarget,
    pub phase: &'static str,
    pub solvers: Vec<&'static str>,
    pub canonical_manifest_models: Vec<u32>,
    pub canonical_payload_io: bool,
}

#[must_use]
pub fn implementation_description(target: ExecutionTarget) -> ImplementationDescription {
    ImplementationDescription {
        implementation: "rust",
        execution_target: target,
        phase: "3-lbm",
        solvers: vec!["stable-fluids", "lbm-d2q9"],
        canonical_manifest_models: vec![1, 2],
        canonical_payload_io: matches!(target, ExecutionTarget::Native),
    }
}

#[cfg(test)]
mod tests {
    use super::{ExecutionTarget, implementation_description};

    #[test]
    fn native_and_wasm_share_capabilities() {
        let native = implementation_description(ExecutionTarget::Native);
        let wasm = implementation_description(ExecutionTarget::WasmBrowser);
        assert_eq!(native.solvers, wasm.solvers);
        assert_eq!(
            native.canonical_manifest_models,
            wasm.canonical_manifest_models
        );
        assert!(native.canonical_payload_io);
        assert!(!wasm.canonical_payload_io);
        assert_ne!(native.execution_target, wasm.execution_target);
    }
}
