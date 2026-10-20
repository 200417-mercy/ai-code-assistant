"""Small dependency-free JSON Schema (draft-07 subset) validator.

The project publishes JSON Schemas (``plugins/plugin.schema.json`` and
``plugins/marketplace.schema.json``) but does not depend on ``jsonschema`` at
runtime. This module implements exactly the subset those documents use:
``type``, ``required``, ``properties``, ``additionalProperties``, ``pattern``,
``enum``, ``const``, ``minLength``/``maxLength``, ``items``, ``minItems``,
``uniqueItems``, local ``$ref``, and ``allOf``/``anyOf``/``oneOf``.

Errors are path-qualified (for example ``artifact.sha256: does not match
pattern ...``) so callers can point users at the exact field. Unknown keywords
are ignored, which keeps the validator forward-compatible with harmless schema
annotations.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any


class SchemaValidationError(ValueError):
    """Raised when a value does not satisfy a JSON Schema."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = tuple(errors)
        super().__init__("; ".join(self.errors))


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def _resolve_ref(ref: str, root: Mapping[str, Any]) -> Mapping[str, Any]:
    """Resolve a local JSON pointer (``#/definitions/name``) against ``root``."""
    if not ref.startswith("#/"):
        raise SchemaValidationError([f"<schema>: unsupported $ref {ref!r}"])
    target: Any = root
    for token in ref[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(target, Mapping) or token not in target:
            raise SchemaValidationError([f"<schema>: unresolved $ref {ref!r}"])
        target = target[token]
    if not isinstance(target, Mapping):
        raise SchemaValidationError([f"<schema>: $ref {ref!r} is not an object schema"])
    return target


def schema_errors(
    value: Any,
    schema: Mapping[str, Any],
    path: str = "",
    *,
    root: Mapping[str, Any] | None = None,
) -> list[str]:
    """Return every schema violation for ``value`` (empty when valid)."""
    root = root if root is not None else schema
    if not isinstance(schema, Mapping):
        return []
    if "$ref" in schema:
        return schema_errors(value, _resolve_ref(schema["$ref"], root), path, root=root)

    errors: list[str] = []
    label = path or "<root>"

    expected_type = schema.get("type")
    if expected_type is not None:
        allowed = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(_type_matches(value, item) for item in allowed):
            return [f"{label}: expected type {expected_type}"]

    for keyword in ("allOf", "anyOf", "oneOf"):
        schemas = schema.get(keyword)
        if not isinstance(schemas, list) or not schemas:
            continue
        branches = [schema_errors(value, branch, path, root=root) for branch in schemas]
        if keyword == "allOf":
            for branch_errors in branches:
                errors.extend(branch_errors)
            continue
        matched = sum(1 for branch_errors in branches if not branch_errors)
        if keyword == "anyOf" and matched == 0:
            errors.append(f"{label}: does not match any allowed schema")
            # Keep branch errors so a caller sees the exact missing/malformed
            # field (for example ``<root>: missing required property 'name'``).
            for branch_errors in branches:
                errors.extend(branch_errors)
        elif keyword == "oneOf" and matched != 1:
            errors.append(f"{label}: must match exactly one allowed schema")

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{label}: {value!r} is not one of {schema['enum']}")
    if "const" in schema and value != schema["const"]:
        errors.append(f"{label}: expected const {schema['const']!r}")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{label}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{label}: longer than maxLength {schema['maxLength']}")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            errors.append(f"{label}: does not match pattern {schema['pattern']!r}")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{label}: fewer than minItems {schema['minItems']}")
        if schema.get("uniqueItems"):
            serialized = [json.dumps(item, sort_keys=True, default=str) for item in value]
            if len(set(serialized)) != len(serialized):
                errors.append(f"{label}: items are not unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                errors.extend(schema_errors(item, item_schema, f"{label}[{index}]", root=root))

    if isinstance(value, dict):
        for required in schema.get("required", []):
            if required not in value:
                errors.append(f"{label}: missing required property {required!r}")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else key
            if key in properties:
                errors.extend(schema_errors(item, properties[key], child_path, root=root))
            elif additional is False:
                errors.append(f"{child_path}: additional property not allowed")
            elif isinstance(additional, Mapping):
                errors.extend(schema_errors(item, additional, child_path, root=root))

    return errors


def validate_schema(value: Any, schema: Mapping[str, Any]) -> None:
    """Validate ``value`` against ``schema``.

    Raises:
        SchemaValidationError: With path-qualified messages when invalid.
    """
    errors = schema_errors(value, schema)
    if errors:
        raise SchemaValidationError(errors)


__all__ = [
    "SchemaValidationError",
    "schema_errors",
    "validate_schema",
]
