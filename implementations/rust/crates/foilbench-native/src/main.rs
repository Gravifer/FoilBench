use std::{
    env,
    path::{Path, PathBuf},
    process::ExitCode,
};

use foilbench_native::{
    chaos::run_chaos,
    gates::run_gate,
    resources::ResourceResolver,
    runner::{BenchOptions, compare_results, run_matrix},
};

fn usage() -> &'static str {
    "foilbench-rs describe\n\
     foilbench-rs bench <matrix.json> [--solver stable-fluids] [--output DIR] [--root DIR]\n\
     foilbench-rs compare <results-dir> [--require-producer implementation/target]... [--root DIR]\n\
     foilbench-rs gate startup|preview|warm-switch|scheduled [--output FILE] [--root DIR]\n\
     foilbench-rs chaos-sweep|chaos-paired|chaos-preflight [--output FILE] [--root DIR]"
}

fn take_value(arguments: &[String], name: &str) -> Result<Option<String>, String> {
    let positions = arguments
        .iter()
        .enumerate()
        .filter(|(_, value)| value.as_str() == name)
        .collect::<Vec<_>>();
    if positions.len() > 1 {
        return Err(format!("{name} may be supplied at most once"));
    }
    positions.first().map_or(Ok(None), |(index, _)| {
        arguments
            .get(index + 1)
            .cloned()
            .map(Some)
            .ok_or_else(|| format!("{name} requires a value"))
    })
}

fn positional(arguments: &[String]) -> Vec<&str> {
    let mut output = Vec::new();
    let mut skip = false;
    for argument in arguments {
        if skip {
            skip = false;
            continue;
        }
        if matches!(
            argument.as_str(),
            "--solver" | "--output" | "--root" | "--require-producer"
        ) {
            skip = true;
        } else if !argument.starts_with('-') {
            output.push(argument.as_str());
        }
    }
    output
}

fn required_producers(arguments: &[String]) -> Result<Vec<(String, String)>, String> {
    let mut output = Vec::new();
    for (index, argument) in arguments.iter().enumerate() {
        if argument == "--require-producer" {
            let value = arguments
                .get(index + 1)
                .ok_or("--require-producer requires implementation/target")?;
            let (implementation, target) = value
                .split_once('/')
                .ok_or("producer must use implementation/target")?;
            if implementation.is_empty() || target.is_empty() {
                return Err("producer identity cannot be empty".into());
            }
            output.push((implementation.into(), target.into()));
        }
    }
    Ok(output)
}

fn run() -> Result<(), String> {
    let arguments = env::args().skip(1).collect::<Vec<_>>();
    let command = arguments.first().map_or("describe", String::as_str);
    if command == "describe" {
        let description =
            foilbench_core::implementation_description(foilbench_core::ExecutionTarget::Native);
        println!(
            "{}",
            serde_json::to_string_pretty(&description).map_err(|error| error.to_string())?
        );
        return Ok(());
    }
    let command_arguments = &arguments[1..];
    let root = take_value(command_arguments, "--root")?.map(PathBuf::from);
    let resolver = ResourceResolver::discover(root.as_deref())?;
    match command {
        "bench" => {
            let selected = positional(command_arguments);
            let matrix = selected
                .first()
                .ok_or_else(|| format!("bench requires a matrix path\n{}", usage()))?;
            let output = take_value(command_arguments, "--output")?.map(PathBuf::from);
            let solver_filter = take_value(command_arguments, "--solver")?;
            let destination = run_matrix(
                &resolver,
                &BenchOptions {
                    matrix: PathBuf::from(matrix),
                    output,
                    solver_filter,
                },
            )?;
            println!("{}", destination.display());
            Ok(())
        }
        "compare" => {
            let selected = positional(command_arguments);
            let directory = selected
                .first()
                .ok_or_else(|| format!("compare requires a result directory\n{}", usage()))?;
            let path = Path::new(directory);
            let comparison_directory = if path.is_absolute() {
                path.to_path_buf()
            } else {
                resolver.resolve(path)
            };
            let count = compare_results(
                &comparison_directory,
                &required_producers(command_arguments)?,
            )?;
            println!("validated {count} benchmark artifacts");
            Ok(())
        }
        "chaos-sweep" | "chaos-paired" | "chaos-preflight" => {
            let output = take_value(command_arguments, "--output")?.map(PathBuf::from);
            let result = run_chaos(&resolver, command, output)?;
            println!(
                "{}",
                serde_json::to_string_pretty(&result).map_err(|error| error.to_string())?
            );
            Ok(())
        }
        "gate" => {
            let selected = positional(command_arguments);
            let gate = selected
                .first()
                .ok_or_else(|| format!("gate requires a gate name\n{}", usage()))?;
            let output = take_value(command_arguments, "--output")?.map(PathBuf::from);
            let result = run_gate(&resolver, gate, output.as_deref())?;
            println!(
                "{}",
                serde_json::to_string_pretty(&result).map_err(|error| error.to_string())?
            );
            Ok(())
        }
        _ => Err(format!("unknown command {command}\n{}", usage())),
    }
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
