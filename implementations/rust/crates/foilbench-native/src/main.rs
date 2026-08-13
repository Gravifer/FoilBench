fn main() {
    let command = std::env::args().nth(1).unwrap_or_else(|| "describe".into());
    if command != "describe" {
        eprintln!(
            "foilbench-rs currently supports only `describe`; no Rust solver is advertised yet"
        );
        std::process::exit(2);
    }
    println!(
        "{}",
        serde_json::json!({
            "implementation": "rust",
            "execution_target": "native",
            "phase": "3-foundation",
            "solvers": [],
            "canonical_read_versions": [1, 2],
            "canonical_write_version": 2
        })
    );
}
