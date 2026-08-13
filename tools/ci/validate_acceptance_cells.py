"""Require an exact, current acceptance-cell roster before aggregation."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

LANGUAGES = ("python", "julia", "typescript")
GATES = ("startup", "preview", "warm-switch", "scheduled")
CONFIGURATION = (
    "spec/conformance/fullsize-acceptance.json",
    "spec/conformance/solver-validity.json",
    "spec/contract-version.json",
    "spec/schemas/scenario.schema.json",
    "benchmark-matrices/preview-gate.json",
    "scenarios/airfoil/default.json",
)
TARGETS = {
    (language, gate): (
        "browser-worker" if language == "typescript" and gate == "preview"
        else "node" if language == "typescript"
        else "native"
    )
    for language in LANGUAGES
    for gate in GATES
}


def configuration_digest(repository: Path, commit: str) -> str:
    identities: list[str] = []
    for path in CONFIGURATION:
        blob = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", f"{commit}:{path}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        identities.append(f"{path}\0{blob}")
    return hashlib.sha256("\n".join(identities).encode()).hexdigest()


def validate_cells(root: Path, commit: str, repository: Path) -> None:
    digest = configuration_digest(repository, commit)
    observed: set[tuple[str, str]] = set()
    for path in root.rglob("cell.json"):
        cell = json.loads(path.read_text(encoding="utf-8"))
        required = {
            "schema_version", "commit", "configuration_digest", "producer",
            "execution_target", "gate", "status", "log_file", "log_sha256",
        }
        if set(cell) != required or cell["schema_version"] != 1:
            raise ValueError(f"malformed acceptance cell: {path}")
        key = (cell["producer"], cell["gate"])
        if key in observed:
            raise ValueError(f"duplicate acceptance cell: {key}")
        observed.add(key)
        if cell["commit"] != commit or cell["configuration_digest"] != digest:
            raise ValueError(f"stale acceptance cell: {path}")
        if cell["status"] != "passed":
            raise ValueError(f"failed acceptance cell: {path}")
        if cell["execution_target"] != TARGETS.get(key):
            raise ValueError(f"execution-target mismatch in acceptance cell: {path}")
        log_path = path.parent / cell["log_file"]
        if not log_path.is_file():
            raise ValueError(f"missing acceptance log: {log_path}")
        if hashlib.sha256(log_path.read_bytes()).hexdigest() != cell["log_sha256"]:
            raise ValueError(f"acceptance log digest mismatch: {log_path}")
    expected = {(language, gate) for language in LANGUAGES for gate in GATES}
    if observed != expected:
        raise ValueError(f"acceptance roster mismatch: missing={expected-observed}, extra={observed-expected}")
    print(f"Accepted {len(observed)} exact cells for {commit} ({digest})")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    arguments = parser.parse_args()
    repository = Path(__file__).resolve().parents[2]
    validate_cells(arguments.root, arguments.commit, repository)


if __name__ == "__main__":
    main()
