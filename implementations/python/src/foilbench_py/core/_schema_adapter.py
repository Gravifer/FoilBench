# pyright: reportUnknownMemberType=false
"""Narrow adapter around jsonschema's dynamic validator API."""

from jsonschema import Draft202012Validator


def validate_json(instance: object, schema: dict[str, object]) -> None:
    Draft202012Validator(schema).validate(instance)
