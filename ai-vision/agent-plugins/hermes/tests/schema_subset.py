"""Small Draft-07 subset validator used only by repository tests."""

from __future__ import annotations

from typing import Any, Mapping


class SchemaValidationError(ValueError):
    pass


def validate(
    instance: Any, schema: Mapping[str, Any], root: Mapping[str, Any], path: str = "$"
) -> None:
    reference = schema.get("$ref")
    if reference:
        if not isinstance(reference, str) or not reference.startswith("#/"):
            raise SchemaValidationError(f"{path}: unsupported reference {reference!r}")
        target: Any = root
        for part in reference[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        validate(instance, target, root, path)
        return

    expected_type = schema.get("type")
    if expected_type == "object":
        if not isinstance(instance, dict):
            raise SchemaValidationError(f"{path}: expected object")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, value in instance.items():
            child_path = f"{path}.{key}"
            if key in properties:
                validate(value, properties[key], root, child_path)
            elif additional is False:
                raise SchemaValidationError(
                    f"{child_path}: additional property is not allowed"
                )
            elif isinstance(additional, dict):
                validate(value, additional, root, child_path)
    elif expected_type == "string":
        if not isinstance(instance, str):
            raise SchemaValidationError(f"{path}: expected string")
        minimum_length = schema.get("minLength")
        if isinstance(minimum_length, int) and len(instance) < minimum_length:
            raise SchemaValidationError(
                f"{path}: string is shorter than {minimum_length}"
            )
    elif expected_type == "boolean":
        if not isinstance(instance, bool):
            raise SchemaValidationError(f"{path}: expected boolean")
    elif expected_type == "integer":
        if not isinstance(instance, int) or isinstance(instance, bool):
            raise SchemaValidationError(f"{path}: expected integer")
        minimum = schema.get("minimum")
        if isinstance(minimum, int) and instance < minimum:
            raise SchemaValidationError(f"{path}: must be at least {minimum}")

    allowed = schema.get("enum")
    if isinstance(allowed, list) and instance not in allowed:
        raise SchemaValidationError(f"{path}: {instance!r} is not one of {allowed!r}")
