"""Read-only marketplace catalog (Phase 9, issue #360).

The marketplace advertises installable plugins without installing anything.
A catalog is either:

* a local JSON file (``PLUGIN_MARKETPLACE_CATALOG``, default
  ``plugins/marketplace.json``), which works fully offline; or
* an optional remote HTTPS document (``PLUGIN_MARKETPLACE_URL``) fetched only
  through :mod:`app.services.plugin_network` (https-only, allowlisted host,
  bounded timeout and size, no redirects).

Every entry uses the installer catalog shape understood by
:mod:`app.services.plugin_catalog`::

    {
      "version": 1,
      "plugins": [
        {
          "id": "hello-world",
          "version": "0.1.0",
          "source": "https://plugins.example.com/hello-world-0.1.0.zip",
          "integrity": "sha256:...",
          "manifest": { ...plugins/plugin.schema.json... },
          "signature": {"publisher": "acme", "algorithm": "ed25519", "value": "..."}
        }
      ]
    }

The embedded manifest is validated against ``plugins/plugin.schema.json``. The
catalog is **untrusted data**: it is never executed and never used to grant a
capability. Installation still goes through the existing signed,
allowlisted, integrity-checked install path.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from packaging.version import Version

from app.config import BASE_DIR
from app.services.plugin_catalog import (
    CatalogEntry,
    CatalogError,
    CatalogUnavailableError,
    load_catalog_file,
    load_remote_catalog,
)
from app.services.schema_validation import schema_errors

#: Default offline catalog shipped with the application (empty by default).
DEFAULT_CATALOG_PATH = BASE_DIR / "plugins" / "marketplace.json"

#: Manifest schema every advertised entry is validated against.
MANIFEST_SCHEMA_PATH = BASE_DIR / "plugins" / "plugin.schema.json"


class MarketplaceError(ValueError):
    """The marketplace catalog is unavailable or invalid."""


class MarketplaceDisabledError(MarketplaceError):
    """The marketplace is disabled by configuration."""


class MarketplaceUnavailableError(MarketplaceError):
    """The configured catalog cannot be read."""


class MarketplaceEntryNotFoundError(MarketplaceError):
    """The requested plugin is not advertised."""


@dataclass(frozen=True)
class MarketplaceEntry:
    """One validated, flattened marketplace entry."""

    plugin_id: str
    name: str
    version: str
    description: str
    author: str
    capabilities: tuple[str, ...]
    permissions: tuple[str, ...]
    dependencies: tuple[str, ...]
    compatibility: str | None
    integrity: str
    source: str
    signature: Mapping[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the public entry shape (never includes executable content)."""
        data: dict[str, Any] = {
            "id": self.plugin_id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "capabilities": list(self.capabilities),
            "permissions": list(self.permissions),
            "dependencies": list(self.dependencies),
            "compatibility": self.compatibility,
            "integrity": self.integrity,
            "source": self.source,
        }
        if self.signature is not None:
            data["signature"] = dict(self.signature)
        return data


class MarketplaceCatalog:
    """An immutable collection of validated marketplace entries."""

    def __init__(self, entries: list[MarketplaceEntry]):
        self._entries = tuple(
            sorted(entries, key=lambda entry: (entry.plugin_id, Version(entry.version)))
        )
        by_id: dict[str, list[MarketplaceEntry]] = {}
        for entry in self._entries:
            by_id.setdefault(entry.plugin_id, []).append(entry)
        self._by_id = {key: tuple(value) for key, value in by_id.items()}

    def __len__(self) -> int:
        return len(self._entries)

    def list_entries(self) -> tuple[MarketplaceEntry, ...]:
        """Return every entry, sorted by plugin id then version."""
        return self._entries

    def get(self, plugin_id: str, version: str | None = None) -> MarketplaceEntry:
        """Return one entry, or the newest version when ``version`` is omitted."""
        matches = self._by_id.get(plugin_id, ())
        if not matches:
            raise MarketplaceEntryNotFoundError(f"Plugin {plugin_id!r} is not in the marketplace.")
        if version is None:
            return matches[-1]
        for entry in matches:
            if entry.version == version:
                return entry
        raise MarketplaceEntryNotFoundError(
            f"Plugin {plugin_id!r} version {version!r} is not in the marketplace."
        )


@lru_cache(maxsize=4)
def _load_manifest_schema(path: str) -> Mapping[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        schema = json.load(handle)
    if not isinstance(schema, Mapping):
        raise MarketplaceError("plugins/plugin.schema.json must be a JSON object")
    return schema


def _flatten(entry: CatalogEntry, index: int) -> MarketplaceEntry:
    """Validate an entry's manifest and flatten it for the read API."""
    schema = _load_manifest_schema(str(MANIFEST_SCHEMA_PATH))
    errors = schema_errors(entry.manifest, schema, f"plugins[{index}].manifest")
    if errors:
        raise MarketplaceError("; ".join(errors))

    manifest = entry.manifest
    return MarketplaceEntry(
        plugin_id=entry.plugin_id,
        name=str(manifest["name"]),
        version=entry.version,
        description=str(manifest["description"]),
        author=str(manifest["author"]),
        capabilities=tuple(str(item) for item in manifest.get("capabilities", ())),
        permissions=tuple(str(item) for item in manifest.get("permissions", ())),
        dependencies=tuple(str(item) for item in manifest.get("dependencies", ())),
        compatibility=(str(manifest["compatibility"]) if manifest.get("compatibility") else None),
        integrity=entry.integrity,
        source=entry.source,
        signature=entry.signature,
    )


def _catalog_source(config: Mapping[str, Any]) -> tuple[str, str]:
    """Return ``(kind, value)`` for the configured catalog source."""
    configured = config.get("PLUGIN_MARKETPLACE_CATALOG")
    if isinstance(configured, str) and configured.strip():
        return "file", configured.strip()
    url = config.get("PLUGIN_MARKETPLACE_URL")
    if isinstance(url, str) and url.strip():
        return "remote", url.strip()
    return "file", str(DEFAULT_CATALOG_PATH)


def load_marketplace(
    config: Mapping[str, Any],
    *,
    resolver: Any = None,
    transport: Any = None,
) -> MarketplaceCatalog:
    """Load and validate the configured marketplace catalog.

    Raises:
        MarketplaceDisabledError: When ``PLUGIN_MARKETPLACE_ENABLED`` is false.
        MarketplaceUnavailableError: When the catalog cannot be read or fetched.
        MarketplaceError: When the catalog or one of its manifests is invalid.
    """
    if not config.get("PLUGIN_MARKETPLACE_ENABLED", True):
        raise MarketplaceDisabledError("The plugin marketplace is disabled.")

    kind, value = _catalog_source(config)
    try:
        if kind == "remote":
            catalog = load_remote_catalog(config, resolver=resolver, transport=transport)
        else:
            catalog = load_catalog_file(value)
    except CatalogUnavailableError as exc:
        raise MarketplaceUnavailableError(str(exc)) from exc
    except CatalogError as exc:
        raise MarketplaceError(str(exc)) from exc

    entries = [_flatten(entry, index) for index, entry in enumerate(catalog.list_entries())]
    return MarketplaceCatalog(entries)


def filter_entries(
    catalog: MarketplaceCatalog,
    *,
    query: str | None = None,
    capability: str | None = None,
) -> list[MarketplaceEntry]:
    """Filter entries by free-text query and/or required capability.

    Filtering is case-insensitive and only inspects advertised metadata.
    """
    needle = (query or "").strip().lower()
    wanted = (capability or "").strip().upper()
    matches = []
    for entry in catalog.list_entries():
        if wanted and wanted not in {item.upper() for item in entry.capabilities}:
            continue
        if needle:
            haystack = " ".join(
                [entry.plugin_id, entry.name, entry.description, entry.author]
            ).lower()
            if needle not in haystack:
                continue
        matches.append(entry)
    return matches


__all__ = [
    "DEFAULT_CATALOG_PATH",
    "MANIFEST_SCHEMA_PATH",
    "MarketplaceCatalog",
    "MarketplaceDisabledError",
    "MarketplaceEntry",
    "MarketplaceEntryNotFoundError",
    "MarketplaceError",
    "MarketplaceUnavailableError",
    "filter_entries",
    "load_marketplace",
]
