"""Validate the FoilBench contract manifest and repository specification layout."""

import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import cast

from jsonschema import Draft202012Validator

CONTRACT_FILENAMES = frozenset(
    {
        "benchmark-methodology.md",
        "canonical-state.md",
        "chaotic-wake-contract.md",
        "interactive-viewer-contract.md",
        "solver-contract.md",
        "solver-repertoire-contract.md",
        "solver-validity-contract.md",
    }
)
SCHEMA_FILENAMES = frozenset(
    {
        "benchmark-matrix.schema.json",
        "canonical-manifest.schema.json",
        "chaotic-wake-cases.schema.json",
        "chaotic-wake-result.schema.json",
        "drag-calibration-result.schema.json",
        "fullsize-acceptance.schema.json",
        "result.schema.json",
        "scenario.schema.json",
        "viewer-transcript.schema.json",
    }
)
TEXT_SUFFIXES = frozenset({".html", ".jl", ".json", ".md", ".ps1", ".py", ".toml", ".ts"})
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def _object(path: Path) -> dict[str, object]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return cast(dict[str, object], value)


def _string_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TypeError(f"contract-version.json field {field!r} must be a string array")
    return cast(list[str], value)


def _resolve_manifest_path(root: Path, relative: str) -> Path:
    logical = PurePosixPath(relative)
    if logical.is_absolute() or ".." in logical.parts:
        raise ValueError(f"manifest path must stay repository-relative: {relative}")
    path = (root / Path(*logical.parts)).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"manifest path escapes repository root: {relative}")
    if not path.exists():
        raise FileNotFoundError(f"manifest path does not exist: {relative}")
    return path


def _tracked_paths(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [root / Path(*PurePosixPath(item).parts) for item in result.stdout.decode().split("\0") if item]


def _validate_markdown_links(root: Path) -> None:
    for directory in (root / "spec", root / "docs"):
        for path in directory.rglob("*.md"):
            for match in MARKDOWN_LINK.finditer(path.read_text(encoding="utf-8")):
                target = match.group(1).strip().strip("<>")
                if not target or target.startswith(("#", "http://", "https://", "mailto:")):
                    continue
                target_path = target.split("#", 1)[0]
                if target_path and not (path.parent / target_path).resolve().exists():
                    raise FileNotFoundError(f"broken Markdown link in {path.relative_to(root)}: {target}")


def _validate_no_obsolete_references(root: Path, schema_names: frozenset[str]) -> None:
    old_contracts = CONTRACT_FILENAMES | {"chaotic-wake-experiment.md"}
    obsolete = tuple(
        [f"spec/{name}" for name in sorted(old_contracts)]
        + [f"spec/{name}" for name in sorted(schema_names)]
    )
    for path in _tracked_paths(root):
        if path.resolve() == Path(__file__).resolve() or path.suffix not in TEXT_SUFFIXES:
            continue
        if path.is_relative_to(root / "spec" / "schemas"):
            continue  # Stable schema $id URIs intentionally retain the old logical identifiers.
        text = path.read_text(encoding="utf-8")
        for reference in obsolete:
            if reference in text or reference.replace("/", "\\") in text:
                raise ValueError(f"obsolete specification path in {path.relative_to(root)}: {reference}")


def _validate_conformance_inventory(
    root: Path,
    manifest: dict[str, object],
    schemas: dict[str, dict[str, object]],
) -> None:
    relative = manifest.get("conformance_inventory")
    if not isinstance(relative, str):
        raise TypeError("contract-version.json must name conformance_inventory")
    inventory = _object(_resolve_manifest_path(root, relative))
    entries = inventory.get("entries")
    if not isinstance(entries, list):
        raise TypeError("conformance inventory entries must be an array")
    conformance = root / "spec" / "conformance"
    observed: set[str] = set()
    for raw in entries:
        if not isinstance(raw, dict):
            raise TypeError("each conformance inventory entry must be an object")
        path_value = raw.get("path")
        if not isinstance(path_value, str) or path_value in observed:
            raise ValueError(f"invalid or duplicate conformance path: {path_value!r}")
        observed.add(path_value)
        fixture = _resolve_manifest_path(root, f"spec/conformance/{path_value}")
        kind = raw.get("kind")
        if kind == "json":
            document = _object(fixture)
        elif kind == "artifact-directory":
            if not fixture.is_dir():
                raise ValueError(f"artifact fixture is not a directory: {path_value}")
            document = _object(fixture / "manifest.json")
        else:
            raise ValueError(f"unknown conformance fixture kind: {kind!r}")
        schema_path = raw.get("schema")
        if schema_path is not None:
            if not isinstance(schema_path, str) or schema_path not in schemas:
                raise ValueError(f"unknown fixture schema for {path_value}: {schema_path!r}")
            Draft202012Validator(schemas[schema_path]).validate(document)
        if not isinstance(raw.get("owner"), str) or not isinstance(raw.get("required"), bool):
            raise TypeError(f"fixture owner/required metadata is invalid: {path_value}")
    expected = {
        path.name for path in conformance.iterdir()
        if path.name not in {"README.md", "inventory.json"}
    }
    if observed != expected:
        raise ValueError(
            "conformance inventory mismatch: "
            f"missing={sorted(expected - observed)} extra={sorted(observed - expected)}"
        )


def _validate_revision5_proposal(root: Path) -> None:
    proposal_root = root / "spec" / "proposals" / "revision5"
    proposal = _object(proposal_root / "manifest.json")
    if proposal.get("status") != "proposed" or proposal.get("base_revision") != 4:
        raise ValueError("Revision 5 proposal must remain proposed against accepted Revision 4")
    documents = _string_list(proposal.get("documents"), "revision5 documents")
    expected = {path.name for path in proposal_root.glob("*.md") if path.name != "README.md"}
    if set(documents) != expected:
        raise ValueError("Revision 5 proposal document inventory is incomplete")
    for document in documents:
        path = proposal_root / document
        if not path.exists() or "status: proposed revision 5 normative component" not in "\n".join(
            path.read_text(encoding="utf-8").splitlines()[:5]
        ).lower():
            raise ValueError(f"invalid Revision 5 proposal document: {document}")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    spec = root / "spec"
    contracts = spec / "contracts"
    schemas = spec / "schemas"
    manifest = _object(spec / "contract-version.json")

    manifest_contracts = frozenset(_string_list(manifest.get("normative_documents"), "normative_documents"))
    expected_contracts = frozenset(f"spec/contracts/{name}" for name in CONTRACT_FILENAMES)
    if manifest_contracts != expected_contracts:
        raise ValueError(
            "normative document inventory mismatch: "
            f"missing={sorted(expected_contracts - manifest_contracts)} "
            f"extra={sorted(manifest_contracts - expected_contracts)}"
        )
    if frozenset(path.name for path in contracts.glob("*.md")) != CONTRACT_FILENAMES:
        raise ValueError("spec/contracts does not exactly match the normative document inventory")
    for relative in manifest_contracts:
        path = _resolve_manifest_path(root, relative)
        header = "\n".join(path.read_text(encoding="utf-8").splitlines()[:6]).lower()
        if "status: accepted normative component" not in header:
            raise ValueError(f"normative document lacks accepted status: {relative}")

    root_markdown = frozenset(path.name for path in spec.glob("*.md"))
    if root_markdown != {"README.md"}:
        raise ValueError(f"only spec/README.md may live at the specification root: {sorted(root_markdown)}")
    for path in spec.rglob("*.md"):
        header = "\n".join(path.read_text(encoding="utf-8").splitlines()[:8]).lower()
        if "status:" in header and ("superseded" in header or "superceded" in header):
            raise ValueError(f"superseded Markdown remains in spec: {path.relative_to(root)}")

    schema_names = frozenset(path.name for path in schemas.glob("*.schema.json"))
    if schema_names != SCHEMA_FILENAMES:
        raise ValueError(
            "spec/schemas inventory mismatch: "
            f"missing={sorted(SCHEMA_FILENAMES - schema_names)} "
            f"extra={sorted(schema_names - SCHEMA_FILENAMES)}"
        )
    manifest_schemas = frozenset(_string_list(manifest.get("schemas"), "schemas"))
    expected_schemas = frozenset(f"spec/schemas/{name}" for name in SCHEMA_FILENAMES)
    if manifest_schemas != expected_schemas:
        raise ValueError(
            "schema inventory mismatch: "
            f"missing={sorted(expected_schemas - manifest_schemas)} "
            f"extra={sorted(manifest_schemas - expected_schemas)}"
        )
    loaded_schemas: dict[str, dict[str, object]] = {}
    for relative in manifest_schemas:
        path = _resolve_manifest_path(root, relative)
        schema = _object(path)
        loaded_schemas[relative] = schema
        Draft202012Validator.check_schema(schema)
        identifier = schema.get("$id")
        if identifier is not None and identifier != f"https://foilbench.local/spec/{path.name}":
            raise ValueError(f"schema $id changed from its stable logical identifier: {relative}")

    conformance_root = manifest.get("conformance_root")
    if not isinstance(conformance_root, str):
        raise TypeError("contract-version.json field 'conformance_root' must be a string")
    conformance = _resolve_manifest_path(root, conformance_root)
    if not conformance.is_dir() or conformance != spec / "conformance":
        raise ValueError("conformance_root must identify spec/conformance")

    _validate_conformance_inventory(root, manifest, loaded_schemas)
    _validate_revision5_proposal(root)

    _validate_markdown_links(root)
    _validate_no_obsolete_references(root, schema_names)
    print(
        f"FoilBench specification layout is valid: "
        f"{len(manifest_contracts)} contracts, {len(manifest_schemas)} schemas"
    )


if __name__ == "__main__":
    main()
