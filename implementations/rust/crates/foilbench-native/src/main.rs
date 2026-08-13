fn main() {
    let command = std::env::args().nth(1).unwrap_or_else(|| "describe".into());
    if command != "describe" {
        eprintln!(
            "foilbench-rs currently supports only `describe`; no Rust solver is advertised yet"
        );
        std::process::exit(2);
    }
    let description =
        foilbench_core::implementation_description(foilbench_core::ExecutionTarget::Native);
    println!("{}", serde_json::to_string(&description).unwrap());
}
