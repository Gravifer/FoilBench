use wasm_bindgen::prelude::*;

/// Foundation handshake used by the TypeScript worker before solver exports land.
#[wasm_bindgen]
#[must_use]
pub fn describe() -> String {
    serde_json::to_string(&foilbench_core::implementation_description(
        foilbench_core::ExecutionTarget::WasmBrowser,
    ))
    .unwrap_or_else(|_| String::from(r#"{"implementation":"rust","execution_target":"wasm-browser","phase":"description-error","solvers":[]}"#))
}
