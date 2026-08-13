import {mkdirSync, rmSync} from "node:fs";
import {spawnSync} from "node:child_process";
import {dirname, resolve} from "node:path";
import {fileURLToPath} from "node:url";

const typescriptRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const rustRoot = resolve(typescriptRoot, "../rust");
const output = resolve(typescriptRoot, "public/rust-wasm");
const wasm = resolve(rustRoot, "target/wasm32-unknown-unknown/release/foilbench_wasm.wasm");

function cargo(commandArguments) {
  const executable = process.platform === "win32" ? "cargo.exe" : "cargo";
  const result = spawnSync(executable, commandArguments, {cwd: rustRoot, stdio: "inherit"});
  if (result.error !== undefined) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}

rmSync(output, {recursive: true, force: true});
mkdirSync(output, {recursive: true});
cargo(["build", "-p", "foilbench-wasm", "--target", "wasm32-unknown-unknown", "--release"]);
cargo(["run", "-p", "foilbench-wasm-bindgen", "--", wasm, output]);
