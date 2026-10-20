"""Tests for the read-only marketplace catalog service and API (#360)."""

from __future__ import annotations

import json

import pytest

from app.models import Workspace, WorkspaceMember
from app.services.marketplace import (
    MarketplaceDisabledError,
    MarketplaceEntryNotFoundError,
    MarketplaceError,
    MarketplaceUnavailableError,
    filter_entries,
    load_marketplace,
)

HASH = "a" * 64


def _manifest(plugin_id: str, version: str = "0.1.0", **overrides):
    manifest = {
        "id": plugin_id,
        "name": f"Plugin {plugin_id}",
        "version": version,
        "description": f"{plugin_id} description",
        "author": "Acme Labs",
        "entry_point": "plugins.test:TestPlugin",
        "capabilities": ["PROJECT_READ"],
        "permissions": ["read:project:files"],
        "dependencies": ["base-plugin>=1.0.0"],
        "compatibility": ">=0.8.0",
    }
    manifest.update(overrides)
    return manifest


def _entry(plugin_id: str, version: str = "0.1.0", **overrides):
    return {
        "id": plugin_id,
        "version": version,
        "source": f"https://plugins.example.com/{plugin_id}-{version}.zip",
        "integrity": f"sha256:{HASH}",
        "manifest": _manifest(plugin_id, version),
        **overrides,
    }


def _catalog(tmp_path, entries, name="marketplace.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"version": 1, "plugins": entries}), encoding="utf-8")
    return path


def _config(path, **overrides):
    config = {
        "PLUGIN_MARKETPLACE_ENABLED": True,
        "PLUGIN_MARKETPLACE_CATALOG": str(path),
        "PLUGIN_MARKETPLACE_URL": "",
        "PLUGIN_NETWORK_ALLOWLIST": "",
        "PLUGIN_NETWORK_HTTPS_ONLY": True,
        "PLUGIN_NETWORK_ALLOW_PRIVATE": False,
        "PLUGIN_NETWORK_TIMEOUT": 5,
        "PLUGIN_NETWORK_MAX_BYTES": 1024 * 1024,
        "PLUGIN_NETWORK_STRICT_DNS": False,
    }
    config.update(overrides)
    return config


class TestMarketplaceService:
    def test_loads_and_flattens_valid_entries(self, tmp_path):
        path = _catalog(tmp_path, [_entry("beta"), _entry("alpha", "0.2.0")])

        catalog = load_marketplace(_config(path))

        assert len(catalog) == 2
        entry = catalog.get("alpha")
        assert entry.to_dict() == {
            "id": "alpha",
            "name": "Plugin alpha",
            "version": "0.2.0",
            "description": "alpha description",
            "author": "Acme Labs",
            "capabilities": ["PROJECT_READ"],
            "permissions": ["read:project:files"],
            "dependencies": ["base-plugin>=1.0.0"],
            "compatibility": ">=0.8.0",
            "integrity": f"sha256:{HASH}",
            "source": "https://plugins.example.com/alpha-0.2.0.zip",
        }

    def test_newest_version_is_selected(self, tmp_path):
        path = _catalog(tmp_path, [_entry("alpha", "0.9.0"), _entry("alpha", "0.10.0")])
        catalog = load_marketplace(_config(path))
        assert catalog.get("alpha").version == "0.10.0"
        assert catalog.get("alpha", "0.9.0").version == "0.9.0"

    def test_empty_default_catalog_is_offline_safe(self):
        catalog = load_marketplace(
            {"PLUGIN_MARKETPLACE_ENABLED": True, "PLUGIN_MARKETPLACE_CATALOG": ""}
        )
        assert len(catalog) == 0

    def test_rejects_manifest_violating_plugin_schema(self, tmp_path):
        entry = _entry("alpha")
        entry["manifest"].pop("entry_point")
        path = _catalog(tmp_path, [entry])

        with pytest.raises(MarketplaceError, match="entry_point"):
            load_marketplace(_config(path))

    def test_rejects_malformed_catalog_documents(self, tmp_path):
        bad_json = tmp_path / "bad.json"
        bad_json.write_text("{not json", encoding="utf-8")
        with pytest.raises(MarketplaceError):
            load_marketplace(_config(bad_json))

        wrong_version = tmp_path / "version.json"
        wrong_version.write_text(json.dumps({"version": 99, "plugins": []}), encoding="utf-8")
        with pytest.raises(MarketplaceError):
            load_marketplace(_config(wrong_version))

        duplicate = _catalog(tmp_path, [_entry("alpha"), _entry("alpha")], name="dup.json")
        with pytest.raises(MarketplaceError, match="duplicate"):
            load_marketplace(_config(duplicate))

    def test_missing_catalog_is_unavailable(self, tmp_path):
        with pytest.raises(MarketplaceUnavailableError):
            load_marketplace(_config(tmp_path / "missing.json"))

    def test_disabled_marketplace_fails_closed(self, tmp_path):
        path = _catalog(tmp_path, [_entry("alpha")])
        with pytest.raises(MarketplaceDisabledError):
            load_marketplace(_config(path, PLUGIN_MARKETPLACE_ENABLED=False))

    def test_unknown_entry_raises(self, tmp_path):
        catalog = load_marketplace(_config(_catalog(tmp_path, [_entry("alpha")])))
        with pytest.raises(MarketplaceEntryNotFoundError):
            catalog.get("nope")

    def test_filters_by_query_and_capability(self, tmp_path):
        path = _catalog(
            tmp_path,
            [
                _entry("alpha"),
                _entry("beta", manifest=_manifest("beta", capabilities=["AI_ACCESS"])),
            ],
        )
        catalog = load_marketplace(_config(path))

        assert [entry.plugin_id for entry in filter_entries(catalog, query="ALPHA")] == ["alpha"]
        assert [entry.plugin_id for entry in filter_entries(catalog, capability="ai_access")] == [
            "beta"
        ]
        assert filter_entries(catalog, capability="STELLAR_WRITE") == []


class _Response:
    def __init__(self, payload, status_code=200, headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.content = json.dumps(payload).encode("utf-8")


class TestRemoteMarketplace:
    def test_remote_catalog_requires_https(self):
        config = _config(
            "",
            PLUGIN_MARKETPLACE_CATALOG="",
            PLUGIN_MARKETPLACE_URL="http://plugins.example.com/catalog.json",
            PLUGIN_NETWORK_ALLOWLIST="plugins.example.com",
        )
        with pytest.raises(MarketplaceUnavailableError, match="https"):
            load_marketplace(config)

    def test_remote_catalog_refuses_host_outside_allowlist(self):
        calls = []

        def transport(*args, **kwargs):  # pragma: no cover - must not be called
            calls.append(args)
            raise AssertionError("transport must not run for a non-allowlisted host")

        config = _config(
            "",
            PLUGIN_MARKETPLACE_CATALOG="",
            PLUGIN_MARKETPLACE_URL="https://evil.example.com/catalog.json",
            PLUGIN_NETWORK_ALLOWLIST="plugins.example.com",
        )
        with pytest.raises(MarketplaceUnavailableError, match="allowlist"):
            load_marketplace(
                config,
                resolver=lambda host: ["93.184.216.34"],
                transport=transport,
            )
        assert calls == []

    def test_remote_catalog_uses_bounded_transport(self):
        captured = {}

        def transport(url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return _Response({"version": 1, "plugins": [_entry("alpha")]})

        config = _config(
            "",
            PLUGIN_MARKETPLACE_CATALOG="",
            PLUGIN_MARKETPLACE_URL="https://plugins.example.com/catalog.json",
            PLUGIN_NETWORK_ALLOWLIST="plugins.example.com",
        )
        catalog = load_marketplace(
            config,
            resolver=lambda host: ["93.184.216.34"],
            transport=transport,
        )

        assert len(catalog) == 1
        assert captured["url"] == "https://plugins.example.com/catalog.json"
        assert captured["kwargs"]["max_bytes"] > 0


class TestMarketplaceApi:
    def test_requires_authentication(self, client):
        response = client.get("/plugins/api/marketplace")
        assert response.status_code in (302, 401)

    def test_lists_filters_and_paginates(self, app, client, make_user, login, tmp_path):
        make_user(username="member", email="member@example.com")
        app.config.update(_config(_catalog(tmp_path, [_entry("beta"), _entry("alpha")])))
        login(email="member@example.com")

        response = client.get("/plugins/api/marketplace?limit=1")
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["pagination"] == {
            "page": 1,
            "limit": 1,
            "max_limit": 100,
            "total": 2,
            "pages": 2,
            "has_next": True,
            "has_prev": False,
        }
        assert payload["entries"] == payload["plugins"]
        assert payload["entries"][0]["id"] == "alpha"

        filtered = client.get("/plugins/api/marketplace?q=beta").get_json()
        assert [entry["id"] for entry in filtered["entries"]] == ["beta"]
        assert filtered["filters"] == {"q": "beta", "capability": None}

    def test_member_can_scope_to_workspace(self, app, client, db, make_user, login, tmp_path):
        owner = make_user(username="owner", email="owner@example.com")
        member = make_user(username="member", email="member@example.com")
        workspace = Workspace(user_id=owner.id, name="Marketplace WS")
        db.session.add(workspace)
        db.session.commit()
        db.session.add(
            WorkspaceMember(workspace_id=workspace.id, user_id=member.id, role="contributor")
        )
        db.session.commit()
        app.config.update(_config(_catalog(tmp_path, [_entry("alpha")])))
        login(email="member@example.com")

        payload = client.get(f"/plugins/api/marketplace?workspace_id={workspace.id}").get_json()
        assert payload["workspace"] == {"id": workspace.id, "role": "contributor"}

    def test_non_member_workspace_is_not_revealed(
        self, app, client, db, make_user, login, tmp_path
    ):
        owner = make_user(username="owner", email="owner@example.com")
        make_user(username="member", email="member@example.com")
        workspace = Workspace(user_id=owner.id, name="Private WS")
        db.session.add(workspace)
        db.session.commit()
        app.config.update(_config(_catalog(tmp_path, [_entry("alpha")])))
        login(email="member@example.com")

        response = client.get(f"/plugins/api/marketplace?workspace_id={workspace.id}")
        assert response.status_code == 404

    def test_reads_one_entry_and_unknown_is_404(self, app, client, make_user, login, tmp_path):
        make_user(username="member", email="member@example.com")
        app.config.update(_config(_catalog(tmp_path, [_entry("alpha")])))
        login(email="member@example.com")

        response = client.get("/plugins/api/marketplace/alpha")
        assert response.status_code == 200
        assert response.get_json()["entry"]["id"] == "alpha"

        assert client.get("/plugins/api/marketplace/nope").status_code == 404

    def test_disabled_and_invalid_catalogs_have_distinct_errors(
        self, app, client, make_user, login, tmp_path
    ):
        make_user(username="member", email="member@example.com")
        login(email="member@example.com")

        app.config.update(_config(_catalog(tmp_path, []), PLUGIN_MARKETPLACE_ENABLED=False))
        disabled = client.get("/plugins/api/marketplace")
        assert disabled.status_code == 404
        assert disabled.get_json()["code"] == "disabled"

        broken = tmp_path / "broken.json"
        broken.write_text("{", encoding="utf-8")
        app.config.update(_config(broken, PLUGIN_MARKETPLACE_ENABLED=True))
        invalid = client.get("/plugins/api/marketplace")
        assert invalid.status_code == 400
        assert invalid.get_json()["code"] == "invalid"
