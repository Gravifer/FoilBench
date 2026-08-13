use std::{env, path::PathBuf, process::ExitCode};

fn run() -> Result<(), String> {
    let mut arguments = env::args_os().skip(1);
    let input = arguments
        .next()
        .map(PathBuf::from)
        .ok_or("missing input WASM path")?;
    let output = arguments
        .next()
        .map(PathBuf::from)
        .ok_or("missing output directory")?;
    if arguments.next().is_some() {
        return Err("expected exactly input WASM and output directory".into());
    }
    let mut bindings = wasm_bindgen_cli_support::Bindgen::new();
    bindings
        .input_path(input)
        .typescript(true)
        .web(true)
        .map_err(|error| error.to_string())?;
    bindings.generate(output).map_err(|error| error.to_string())
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("error: {error}");
            ExitCode::from(2)
        }
    }
}
