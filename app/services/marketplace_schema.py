"""Validation helpers for the marketplace catalog JSON Schema (#373).

``plugins/marketplace.schema.json`` describes a normalized marketplace entry
and also accepts the installer catalog shape used by
:mod:`app.services.plugin_catalog`, so the same reusable validator works for
both a browsable catalog record and an operator's install fixture.

Validation is dependency-free: it reuses the draft-07 subset interpreter in
:mod:`app.services.schema_validation`. Errors are path-qualified, for example
``artifact.sha256: does not match pattern ...``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.services.schema_validation import SchemaValidationError, schema_errors

#: Published schema for marketplace catalog entries.
MARKETPLACE_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "plugins" / "marketplace.schema.json"
)


class MarketplaceSchemaError(SchemaValidationError):
    """Raised when a marketplace catalog entry violates the published schema."""


@lru_cache(maxsize=4)
def _load_schema(path: str) -> Mapping[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        schema = json.load(handle)
    if not isinstance(schema, Mapping):
        raise MarketplaceSchemaError(["<schema>: marketplace schema must be an object"])
    return schema


def load_marketplace_schema(path: str | Path | None = None) -> Mapping[str, Any]:
    """Load and return the marketplace schema (cached per path)."""
    return _load_schema(str(Path(path) if path is not None else MARKETPLACE_SCHEMA_PATH))


def marketplace_entry_errors(
    entry: Any,
    *,
    schema: Mapping[str, Any] | None = None,
) -> list[str]:
    """Return path-qualified schema errors for one marketplace entry."""
    return schema_errors(entry, schema if schema is not None else load_marketplace_schema())


def validate_marketplace_entry(
    entry: Any,
    *,
    schema: Mapping[str, Any] | None = None,
) -> None:
    """Validate one marketplace catalog entry.

    Raises:
        MarketplaceSchemaError: With path-qualified messages when invalid.
    """
    errors = marketplace_entry_errors(entry, schema=schema)
    if errors:
        raise MarketplaceSchemaError(errors)


#: Backwards-friendly aliases for callers that call the record a catalog entry.
validate_catalog_entry = validate_marketplace_entry
validate_catalog_object = validate_marketplace_entry


def validate_marketplace_catalog(
    catalog: Any,
    *,
    schema: Mapping[str, Any] | None = None,
) -> None:
    """Validate a catalog document or list of marketplace entries.

    Accepts either ``{"plugins": [...]}`` (the installer document) or a bare
    list. The first invalid entry raises :class:`MarketplaceSchemaError` with
    its index in the path.
    """
    if isinstance(catalog, Mapping):
        entries = catalog.get("plugins")
        if not isinstance(entries, list):
            raise MarketplaceSchemaError(["plugins: expected type list"])
    elif isinstance(catalog, list):
        entries = catalog
    else:
        raise MarketplaceSchemaError(["<root>: expected a catalog object or list"])

    resolved = schema if schema is not None else load_marketplace_schema()
    for index, entry in enumerate(entries):
        errors = schema_errors(entry, resolved, f"plugins[{index}]")
        if errors:
            raise MarketplaceSchemaError(errors)


__all__ = [
    "MARKETPLACE_SCHEMA_PATH",
    "MarketplaceSchemaError",
    "load_marketplace_schema",
    "marketplace_entry_errors",
    "validate_catalog_entry",
    "validate_catalog_object",
    "validate_marketplace_catalog",
    "validate_marketplace_entry",
]
