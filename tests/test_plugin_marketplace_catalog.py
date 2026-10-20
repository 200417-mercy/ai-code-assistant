"""Tests for the member-readable marketplace catalog and its UI (#364)."""

import json

from app.extensions import db
from app.models import Plugin, PluginInstallation, PluginLockEntry, Workspace, WorkspaceMember
from app.services.plugin_lockfile import normalize_integrity

HASH = "a" * 64


def _manifest(plugin_id, version="0.1.0", **overrides):
    manifest = {
        "id": plugin_id,
        "name": f"Marketplace {plugin_id}",
        "version": version,
        "description": f"{plugin_id} description",
        "author": "Acme Labs",
        "entry_point": "plugins.test:TestPlugin",
        "capabilities": ["PROJECT_READ"],
        "permissions": ["read:project:files"],
        "dependencies": ["base-plugin>=1.0.0"],
        "compatibility": ">=0.0.1",
    }
    manifest.update(overrides)
    return manifest


def _entry(plugin_id, version="0.1.0", **overrides):
    manifest = _manifest(plugin_id, version, **overrides.pop("manifest", {}))
    return {
        "id": plugin_id,
        "version": version,
        "source": f"https://plugins.example.com/{plugin_id}-{version}.zip",
        "integrity": f"sha256:{HASH}",
        "manifest": manifest,
        **overrides,
    }


def _write_catalog(tmp_path, entries):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"version": 1, "plugins": entries}), encoding="utf-8")
    return path


def _workspace(owner, name="Catalog WS"):
    workspace = Workspace(user_id=owner.id, name=name)
    db.session.add(workspace)
    db.session.commit()
    return workspace


def _member(workspace, user):
    membership = WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="contributor")
    db.session.add(membership)
    db.session.commit()
    return membership


class TestCatalogBrowseApi:
    def test_member_can_browse_with_metadata_and_pagination(
        self, app, client, make_user, login, tmp_path
    ):
        owner = make_user(username="owner", email="owner@example.com")
        member = make_user(username="member", email="member@example.com")
        workspace = _workspace(owner)
        _member(workspace, member)
        app.config["PLUGIN_CATALOG_FILE"] = str(
            _write_catalog(tmp_path, [_entry("beta"), _entry("alpha")])
        )

        login(email="member@example.com")
        response = client.get(f"/plugins/api/workspaces/{workspace.id}/catalog?limit=1")

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
        assert len(payload["plugins"]) == 1
        entry = payload["plugins"][0]
        assert entry["id"] == "alpha"
        assert entry["name"] == "Marketplace alpha"
        assert entry["version"] == "0.1.0"
        assert entry["author"] == "Acme Labs"
        assert entry["capabilities"] == ["PROJECT_READ"]
        assert entry["permissions"] == ["read:project:files"]
        assert entry["dependencies"] == ["base-plugin>=1.0.0"]
        assert entry["compatibility"] == ">=0.0.1"
        assert entry["compatible_with_app"] is True
        assert entry["trust_state"] == "Unverified"
        assert entry["source"].startswith("https://")
        assert entry["integrity"] == normalize_integrity(HASH)
        assert entry["artifact"]["sha256"] == normalize_integrity(HASH)
        assert entry["installed"] is False
        assert entry["actions"] == {"install": False, "update": False, "rollback": False}

        second = client.get(f"/plugins/api/workspaces/{workspace.id}/catalog?page=2&limit=1")
        assert second.status_code == 200
        assert second.get_json()["plugins"][0]["id"] == "beta"

    def test_owner_sees_management_actions(self, app, client, make_user, login, tmp_path):
        owner = make_user()
        login()
        workspace = _workspace(owner)
        app.config["PLUGIN_CATALOG_FILE"] = str(_write_catalog(tmp_path, [_entry("alpha")]))

        payload = client.get(f"/plugins/api/workspaces/{workspace.id}/catalog").get_json()

        entry = payload["plugins"][0]
        assert entry["role"] == "owner"
        assert entry["can_manage"] is True
        assert entry["actions"]["install"] is True

    def test_installed_update_and_rollback_state(self, app, client, make_user, login, tmp_path):
        owner = make_user()
        login()
        workspace = _workspace(owner)
        app.config["PLUGIN_CATALOG_FILE"] = str(
            _write_catalog(
                tmp_path,
                [
                    _entry("demo", "0.1.0"),
                    _entry("demo", "0.2.0"),
                    _entry("demo", "0.3.0"),
                ],
            )
        )
        plugin = Plugin(
            id="demo",
            name="Marketplace demo",
            version="0.2.0",
            description="demo",
            author="Acme Labs",
            entry_point="plugins.test:TestPlugin",
            capabilities=["PROJECT_READ"],
            compatibility=">=0.0.1",
        )
        db.session.add(plugin)
        db.session.commit()
        db.session.add(
            PluginInstallation(plugin_id="demo", workspace_id=workspace.id, enabled=True)
        )
        db.session.add(
            PluginLockEntry(
                workspace_id=workspace.id,
                owner_id=owner.id,
                plugin_id="demo",
                version="0.1.0",
                source="https://plugins.example.com/demo-0.1.0.zip",
                integrity=normalize_integrity(HASH),
                dependencies=["base-plugin>=1.0.0"],
            )
        )
        db.session.commit()

        payload = client.get(f"/plugins/api/workspaces/{workspace.id}/catalog").get_json()
        by_version = {entry["version"]: entry for entry in payload["plugins"]}

        newest = by_version["0.3.0"]
        assert newest["is_latest"] is True
        assert newest["installed"] is True
        assert newest["installed_version"] == "0.2.0"
        assert newest["enabled"] is True
        assert newest["update_available"] is True
        assert newest["update_version"] == "0.3.0"
        assert newest["rollback_available"] is True
        assert newest["rollback_version"] == "0.1.0"
        assert newest["actions"] == {"install": False, "update": True, "rollback": True}
        assert by_version["0.1.0"]["is_latest"] is False

    def test_member_actions_are_read_only_even_for_installed_plugins(
        self, app, client, make_user, login, tmp_path
    ):
        owner = make_user(username="owner", email="owner@example.com")
        member = make_user(username="member", email="member@example.com")
        workspace = _workspace(owner)
        _member(workspace, member)
        app.config["PLUGIN_CATALOG_FILE"] = str(
            _write_catalog(tmp_path, [_entry("demo", "0.1.0"), _entry("demo", "0.2.0")])
        )
        plugin = Plugin(
            id="demo",
            name="Marketplace demo",
            version="0.1.0",
            entry_point="plugins.test:TestPlugin",
            capabilities=["PROJECT_READ"],
        )
        db.session.add(plugin)
        db.session.commit()
        db.session.add(PluginInstallation(plugin_id="demo", workspace_id=workspace.id))
        db.session.commit()

        login(email="member@example.com")
        payload = client.get(f"/plugins/api/workspaces/{workspace.id}/catalog").get_json()

        newest = next(entry for entry in payload["plugins"] if entry["is_latest"])
        assert newest["can_manage"] is False
        assert newest["actions"] == {"install": False, "update": False, "rollback": False}

    def test_non_member_gets_404(self, app, client, make_user, login, tmp_path):
        owner = make_user(username="owner", email="owner@example.com")
        make_user(username="outsider", email="outsider@example.com")
        workspace = _workspace(owner)
        app.config["PLUGIN_CATALOG_FILE"] = str(_write_catalog(tmp_path, [_entry("alpha")]))

        login(email="outsider@example.com")
        response = client.get(f"/plugins/api/workspaces/{workspace.id}/catalog")

        assert response.status_code == 404

    def test_unavailable_catalog_is_404(self, app, client, make_user, login):
        owner = make_user()
        login()
        workspace = _workspace(owner)
        app.config["PLUGIN_CATALOG_FILE"] = ""

        response = client.get(f"/plugins/api/workspaces/{workspace.id}/catalog")

        assert response.status_code == 404
        assert response.get_json()["code"] == "catalog_unavailable"

    def test_requires_authentication(self, client):
        response = client.get("/plugins/api/workspaces/1/catalog")
        assert response.status_code in (302, 401)

    def test_marketplace_alias_serves_the_same_payload(
        self, app, client, make_user, login, tmp_path
    ):
        owner = make_user()
        login()
        workspace = _workspace(owner)
        app.config["PLUGIN_CATALOG_FILE"] = str(_write_catalog(tmp_path, [_entry("alpha")]))

        canonical = client.get(f"/plugins/api/workspaces/{workspace.id}/catalog").get_json()
        alias = client.get(f"/plugins/api/workspaces/{workspace.id}/marketplace").get_json()

        assert alias["plugins"] == canonical["plugins"]
        assert alias["pagination"] == canonical["pagination"]


class TestMarketplaceUiTemplate:
    def test_index_includes_marketplace_browser(self, client, make_user, login):
        make_user()
        login()

        response = client.get("/plugins/")
        html = response.get_data(as_text=True)

        assert response.status_code == 200
        assert 'id="plugin-marketplace-section"' in html
        assert 'id="plugin-catalog"' in html
        assert 'id="plugin-catalog-pagination"' in html
        assert "js/plugin_catalog.js" in html
        assert "js/plugin_marketplace.js" in html
