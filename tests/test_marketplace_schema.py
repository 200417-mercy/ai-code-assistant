"""Tests for the marketplace catalog JSON Schema and validation helper (#373)."""

import json
from pathlib import Path

import pytest

from app.services.marketplace_schema import (
    MARKETPLACE_SCHEMA_PATH,
    MarketplaceSchemaError,
    load_marketplace_schema,
    marketplace_entry_errors,
    validate_catalog_entry,
    validate_marketplace_catalog,
    validate_marketplace_entry,
)

HASH = "a" * 64


def _valid(**overrides):
    entry = {
        "id": "hello-world",
        "name": "Hello World",
        "version": "0.1.0",
        "description": "A friendly marketplace plugin",
        "author": "Acme Labs",
        "capabilities": ["PROJECT_READ"],
        "permissions": ["read:project:files"],
        "dependencies": ["base-plugin>=1.0.0"],
        "compatibility": ">=0.8.0",
        "artifact": {
            "url": "https://plugins.example.com/hello-world-0.1.0.zip",
            "sha256": f"sha256:{HASH}",
            "publisher_key_id": "acme-labs",
        },
    }
    entry.update(overrides)
    return entry


def _flat(**overrides):
    entry = _valid()
    artifact = entry.pop("artifact")
    entry["artifact_url"] = artifact["url"]
    entry["sha256"] = artifact["sha256"]
    entry["publisher_key_id"] = artifact["publisher_key_id"]
    entry.update(overrides)
    return entry


def _install_fixture():
    return {
        "id": "hello-world",
        "version": "0.1.0",
        "source": "https://plugins.example.com/hello-world-0.1.0.zip",
        "integrity": f"sha256:{HASH}",
        "manifest": {
            "id": "hello-world",
            "name": "Hello World",
            "version": "0.1.0",
            "description": "description",
            "author": "Acme Labs",
            "entry_point": "plugins.hello:Hello",
            "capabilities": ["PROJECT_READ"],
        },
    }


class TestMarketplaceSchema:
    def test_schema_file_is_published(self):
        assert MARKETPLACE_SCHEMA_PATH.is_file()
        schema = json.loads(MARKETPLACE_SCHEMA_PATH.read_text(encoding="utf-8"))
        assert schema["$schema"] == "http://json-schema.org/draft-07/schema#"
        assert schema["type"] == "object"
        assert "id" in schema["properties"]

    def test_valid_nested_artifact_entry(self):
        validate_marketplace_entry(_valid())
        assert marketplace_entry_errors(_valid()) == []

    def test_valid_flat_artifact_entry(self):
        validate_marketplace_entry(_flat())

    def test_existing_install_catalog_shape_still_validates(self):
        validate_marketplace_entry(_install_fixture())
        validate_catalog_entry(_install_fixture())

    def test_catalog_document_and_list_validate(self):
        validate_marketplace_catalog({"version": 1, "plugins": [_valid(), _flat()]})
        validate_marketplace_catalog([_valid()])

    def test_non_object_is_rejected(self):
        with pytest.raises(MarketplaceSchemaError):
            validate_marketplace_entry([])

    @pytest.mark.parametrize(
        "field",
        ["name", "description", "author", "capabilities", "permissions", "dependencies"],
    )
    def test_missing_marketplace_fields_are_rejected(self, field):
        entry = _valid()
        entry.pop(field)

        with pytest.raises(MarketplaceSchemaError) as excinfo:
            validate_marketplace_entry(entry)

        assert any(field in error for error in excinfo.value.errors)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("capabilities", "PROJECT_READ"),
            ("permissions", {"read": True}),
            ("dependencies", "base-plugin"),
            ("artifact", "https://plugins.example.com/p.zip"),
        ],
    )
    def test_wrong_types_are_rejected(self, field, value):
        with pytest.raises(MarketplaceSchemaError):
            validate_marketplace_entry(_valid(**{field: value}))

    @pytest.mark.parametrize(
        "overrides",
        [
            {"id": "HelloWorld"},
            {"id": "123-plugin"},
            {"version": "not-a-version"},
            {"version": "1.0"},
            {
                "artifact": {
                    "url": "http://plugins.example.com/p.zip",
                    "sha256": HASH,
                    "publisher_key_id": "acme",
                }
            },
            {
                "artifact": {
                    "url": "https://plugins.example.com/p.zip",
                    "sha256": "not-a-hash",
                    "publisher_key_id": "acme",
                }
            },
            {
                "artifact": {
                    "url": "https://plugins.example.com/p.zip",
                    "sha256": HASH,
                    "publisher_key_id": "Acme Labs",
                }
            },
        ],
    )
    def test_invalid_ids_versions_urls_and_hashes_are_rejected(self, overrides):
        entry = _valid()
        entry.update(overrides)
        with pytest.raises(MarketplaceSchemaError):
            validate_marketplace_entry(entry)

    def test_unknown_capability_is_rejected(self):
        with pytest.raises(MarketplaceSchemaError) as excinfo:
            validate_marketplace_entry(_valid(capabilities=["UNKNOWN_CAPABILITY"]))

        assert any("capabilities[0]" in error for error in excinfo.value.errors)

    @pytest.mark.parametrize("field", ["capabilities", "permissions", "dependencies"])
    def test_duplicate_list_items_are_rejected(self, field):
        values = {
            "capabilities": ["PROJECT_READ", "PROJECT_READ"],
            "permissions": ["read:project", "read:project"],
            "dependencies": ["base>=1", "base>=1"],
        }
        with pytest.raises(MarketplaceSchemaError, match="not unique"):
            validate_marketplace_entry(_valid(**{field: values[field]}))

    def test_errors_are_path_qualified(self):
        entry = _valid()
        entry["artifact"]["sha256"] = "nope"

        errors = marketplace_entry_errors(entry)

        assert any(error.startswith("artifact.sha256:") for error in errors)

    def test_extra_fields_are_rejected(self):
        with pytest.raises(MarketplaceSchemaError, match="additional property"):
            validate_marketplace_entry(_valid(unexpected=True))

    def test_helper_loads_the_published_schema(self):
        schema = load_marketplace_schema()
        assert schema is load_marketplace_schema()
        assert schema is load_marketplace_schema(Path(MARKETPLACE_SCHEMA_PATH))
