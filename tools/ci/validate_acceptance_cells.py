"""Require an exact, current Revision 5 representative-cell roster."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from jsonschema import Draft202012Validator

CELLS = frozenset(
    [
        *((language, "native", gate) for language in ("python", "julia") for gate in ("startup", "preview", "warm-switch", "scheduled")),
        *(("typescript", "node", gate) for gate in ("startup", "warm-switch", "scheduled")),
        ("typescript", "browser-worker", "preview"),
        *(("rust", "native", gate) for gate in ("startup", "preview", "warm-switch", "scheduled")),
        ("rust", "wasm-browser", "preview"),
        ("rust", "wasm-browser", "production-browser"),
    ]
)
CONFIGURATION = (
    "spec/conformance/fullsize-acceptance.json",
    "spec/conformance/solver-validity.json",
    "spec/contract-version.json",
    "spec/schemas/scenario.schema.json",
    "spec/conformance/fullsize-acceptance-v2.json",
    "spec/schemas/acceptance-cell-v2.schema.json",
    "benchmark-matrices/preview-gate.json",
    "scenarios/airfoil/default.json",
)


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
    schema = json.loads((repository / "spec/schemas/acceptance-cell-v2.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    observed: set[tuple[str, str, str]] = set()
    for path in root.rglob("cell.json"):
        cell = json.loads(path.read_text(encoding="utf-8"))
        validator.validate(cell)
        key = (cell["implementation"], cell["execution_target"], cell["gate"])
        if key in observed:
            raise ValueError(f"duplicate acceptance cell: {key}")
        observed.add(key)
        if cell["commit"] != commit or cell["configuration_digest"] != digest:
            raise ValueError(f"stale acceptance cell: {path}")
        log_file = cell["log_file"]
        if not isinstance(log_file, str) or not log_file or Path(log_file).is_absolute():
            raise ValueError(f"invalid acceptance log path: {path}")
        cell_directory = path.parent.resolve()
        try:
            log_path = (cell_directory / log_file).resolve(strict=True)
            log_path.relative_to(cell_directory)
        except (OSError, ValueError):
            raise ValueError(f"acceptance log escapes cell directory: {path}") from None
        if not log_path.is_file():
            raise ValueError(f"missing acceptance log: {log_path}")
        if hashlib.sha256(log_path.read_bytes()).hexdigest() != cell["log_sha256"]:
            raise ValueError(f"acceptance log digest mismatch: {log_path}")
        evidence_path = path.parent / "evidence.json"
        if not evidence_path.is_file():
            raise ValueError(f"missing measured evidence: {evidence_path}")
        expected_evidence = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
        if cell["measurements"].get("evidence_sha256") != expected_evidence:
            raise ValueError(f"acceptance evidence digest mismatch: {evidence_path}")
    if observed != CELLS:
        raise ValueError(f"acceptance roster mismatch: missing={CELLS-observed}, extra={observed-CELLS}")
    print(f"Accepted {len(observed)} exact Revision 5 cells for {commit} ({digest})")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    arguments = parser.parse_args()
    repository = Path(__file__).resolve().parents[2]
    validate_cells(arguments.root, arguments.commit, repository)


if __name__ == "__main__":
    main()
