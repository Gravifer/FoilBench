"""Fail fast when representative-acceptance configuration drifts from its schemas."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator


def _validate(root: Path, document: str, schema: str) -> None:
    value = json.loads((root / document).read_text(encoding="utf-8"))
    schema_value = json.loads((root / schema).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema_value)
    Draft202012Validator(schema_value).validate(value)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    _validate(
        root,
        "spec/conformance/fullsize-acceptance.json",
        "spec/schemas/fullsize-acceptance.schema.json",
    )
    _validate(
        root,
        "spec/conformance/chaotic-wake-cases.json",
        "spec/schemas/chaotic-wake-cases.schema.json",
    )
    print("Revision 4 acceptance fixtures are schema-valid")


if __name__ == "__main__":
    main()
