use wasm_bindgen::prelude::*;

/// Foundation handshake used by the TypeScript worker before solver exports land.
#[wasm_bindgen]
#[must_use]
pub fn describe() -> String {
    r#"{"implementation":"rust","execution_target":"wasm-browser","phase":"3-foundation","solvers":[]}"#.into()
}
