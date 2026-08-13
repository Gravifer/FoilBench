"""Require an exact, current acceptance-cell roster before aggregation."""

import argparse
import hashlib
import json
from pathlib import Path

LANGUAGES = ("python", "julia", "typescript")
GATES = ("startup", "preview", "warm-switch", "scheduled")
CONFIGURATION = (
    "spec/conformance/fullsize-acceptance.json",
    "spec/conformance/solver-validity.json",
    "spec/contract-version.json",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    arguments = parser.parse_args()
    repository = Path(__file__).resolve().parents[2]
    hashes = [hashlib.sha256((repository / path).read_bytes()).hexdigest() for path in CONFIGURATION]
    digest = hashlib.sha256("\n".join(hashes).encode()).hexdigest()
    observed: set[tuple[str, str]] = set()
    for path in arguments.root.rglob("cell.json"):
        cell = json.loads(path.read_text(encoding="utf-8"))
        key = (cell["producer"], cell["gate"])
        if key in observed:
            raise ValueError(f"duplicate acceptance cell: {key}")
        observed.add(key)
        if cell["commit"] != arguments.commit or cell["configuration_digest"] != digest:
            raise ValueError(f"stale acceptance cell: {path}")
        if cell["status"] != "passed":
            raise ValueError(f"failed acceptance cell: {path}")
    expected = {(language, gate) for language in LANGUAGES for gate in GATES}
    if observed != expected:
        raise ValueError(f"acceptance roster mismatch: missing={expected-observed}, extra={observed-expected}")
    print(f"Accepted {len(observed)} exact cells for {arguments.commit} ({digest})")


if __name__ == "__main__":
    main()

