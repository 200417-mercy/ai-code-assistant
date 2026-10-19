"""Tests for Phase 9 lockfile read API, rollback, and install audit (#396, #402, #388).

Covers:
- the owner-scoped lockfile read endpoint returning locked plugins and hashes;
- the plugin rollback endpoint restoring a previous version and auditing it;
- audit rows for successful and refused remote (catalog) install attempts.
"""

import json

from app.extensions import db
from app.models import (
    ActivityEvent,
    Plugin,
    PluginInstallation,
    PluginLockEntry,
    Workspace,
    WorkspaceMember,
)
from app.models.activity_event import (
    AUDIT_EVENT_TYPES,
    EVENT_PLUGIN_DENIED,
)
from app.services.plugin_lockfile import normalize_integrity

HASH_A = "a" * 64
HASH_B = "b" * 64


def _workspace(owner, name="WS"):
    ws = Workspace(user_id=owner.id, name=name)
    db.session.add(ws)
    db.session.commit()
    return ws


def _member(ws, user, role="contributor"):
    membership = WorkspaceMember(workspace_id=ws.id, user_id=user.id, role=role)
    db.session.add(membership)
    db.session.commit()
    return membership


def _plugin(
    plugin_id="catalog-plugin",
    version="0.1.0",
    entry_point="plugins.catalog:CatalogPlugin",
):
    plugin = Plugin(
        id=plugin_id,
        name="Catalog Plugin",
        version=version,
        description="A catalog plugin",
        author="Tester",
        entry_point=entry_point,
        capabilities=["PROJECT_READ"],
    )
    db.session.add(plugin)
    db.session.commit()
    return plugin


def _install(plugin_id, workspace_id, enabled=True):
    installation = PluginInstallation(
        plugin_id=plugin_id,
        workspace_id=workspace_id,
        enabled=enabled,
    )
    db.session.add(installation)
    db.session.commit()
    return installation


def _lock(workspace_id, owner_id, plugin_id="catalog-plugin", version="0.1.0", integrity=HASH_A):
    row = PluginLockEntry(
        workspace_id=workspace_id,
        owner_id=owner_id,
        plugin_id=plugin_id,
        version=version,
        source="https://plugins.example.com/p.zip",
        integrity=integrity,
        dependencies=["dep>=1.0"],
    )
    db.session.add(row)
    db.session.commit()
    return row


def _audit_events(workspace_id):
    return (
        ActivityEvent.query.filter(
            ActivityEvent.workspace_id == workspace_id,
            ActivityEvent.event_type.in_(AUDIT_EVENT_TYPES),
        )
        .order_by(ActivityEvent.id)
        .all()
    )


class TestLockfileReadApi:
    def _url(self, workspace_id):
        return f"/plugins/api/workspaces/{workspace_id}/lockfile"

    def test_owner_reads_lockfile(self, client, make_user, login):
        owner = make_user()
        login()
        ws = _workspace(owner)
        _lock(ws.id, owner.id, "zebra", integrity=HASH_A)
        _lock(ws.id, owner.id, "alpha", integrity=HASH_B)

        response = client.get(self._url(ws.id))

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["lockfile_version"] == 1
        assert [entry["plugin_id"] for entry in payload["plugins"]] == ["alpha", "zebra"]
        assert payload["plugins"][0]["integrity"] == normalize_integrity(HASH_B)
        # Stable, secret-free shape: no workspace/owner/timestamps.
        for entry in payload["plugins"]:
            assert set(entry) == {
                "plugin_id",
                "version",
                "source",
                "integrity",
                "dependencies",
            }

    def test_empty_lockfile_returns_empty_plugins(self, client, make_user, login):
        owner = make_user()
        login()
        ws = _workspace(owner)

        response = client.get(self._url(ws.id))

        assert response.status_code == 200
        assert response.get_json() == {"lockfile_version": 1, "plugins": []}

    def test_member_cannot_read_lockfile(self, client, make_user, login):
        owner = make_user(username="owner", email="owner@example.com")
        member = make_user(username="member", email="member@example.com")
        ws = _workspace(owner)
        _member(ws, member, role="contributor")
        _lock(ws.id, owner.id)

        login(email="member@example.com")
        response = client.get(self._url(ws.id))

        # Owner-scoped capability -> forbidden for a member (not a leak).
        assert response.status_code == 403

    def test_non_member_gets_404(self, client, make_user, login):
        owner = make_user(username="owner", email="owner@example.com")
        make_user(username="outsider", email="outsider@example.com")
        ws = _workspace(owner)
        _lock(ws.id, owner.id)

        login(email="outsider@example.com")
        response = client.get(self._url(ws.id))

        assert response.status_code == 404

    def test_requires_authentication(self, client):
        response = client.get(self._url(1))
        assert response.status_code in (302, 401)


class TestRollback:
    def _url(self, workspace_id, plugin_id="catalog-plugin"):
        return f"/plugins/api/workspaces/{workspace_id}/plugins/{plugin_id}/rollback"

    def test_rollback_restores_previous_version(self, client, make_user, login):
        owner = make_user()
        login()
        ws = _workspace(owner)
        installed = _plugin(version="0.2.0")
        _install(installed.id, ws.id)
        _lock(ws.id, owner.id, version="0.1.0")

        response = client.post(self._url(ws.id))

        assert response.status_code == 200
        assert response.get_json()["version"] == "0.1.0"
        assert db.session.get(Plugin, "catalog-plugin").version == "0.1.0"

    def test_rollback_is_audited(self, client, make_user, login):
        owner = make_user()
        login()
        ws = _workspace(owner)
        _plugin(version="0.2.0")
        _install("catalog-plugin", ws.id)
        _lock(ws.id, owner.id, version="0.1.0")

        client.post(self._url(ws.id))

        events = [
            e for e in _audit_events(ws.id) if e.event_metadata.get("capability") == "rollback"
        ]
        assert len(events) == 1
        assert events[0].event_metadata["outcome"] == "success"
        assert events[0].actor_id == owner.id

    def test_rollback_without_lockfile_history_fails(self, client, make_user, login):
        owner = make_user()
        login()
        ws = _workspace(owner)
        _plugin(version="0.2.0")
        _install("catalog-plugin", ws.id)

        response = client.post(self._url(ws.id))

        assert response.status_code == 404
        assert "lockfile" in response.get_json()["error"].lower()

    def test_rollback_missing_installation_fails(self, client, make_user, login):
        owner = make_user()
        login()
        ws = _workspace(owner)
        _plugin(version="0.2.0")
        _lock(ws.id, owner.id, version="0.1.0")

        response = client.post(self._url(ws.id))

        assert response.status_code == 404

    def test_member_cannot_rollback(self, client, make_user, login):
        owner = make_user(username="owner", email="owner@example.com")
        member = make_user(username="member", email="member@example.com")
        ws = _workspace(owner)
        _member(ws, member)
        _plugin(version="0.2.0")
        _install("catalog-plugin", ws.id)
        _lock(ws.id, owner.id, version="0.1.0")

        login(email="member@example.com")
        response = client.post(self._url(ws.id))

        assert response.status_code == 403


class TestRemoteInstallAudit:
    def _install_url(self, workspace_id):
        return f"/plugins/api/workspaces/{workspace_id}/plugins/install-from-catalog"

    def _catalog_file(self, tmp_path, *, signed=True):
        manifest = {
            "id": "catalog-plugin",
            "name": "Catalog Plugin",
            "version": "0.1.0",
            "description": "A catalog plugin",
            "author": "Tester",
            "entry_point": "plugins.catalog:CatalogPlugin",
            "capabilities": ["PROJECT_READ"],
        }
        entry = {
            "id": "catalog-plugin",
            "version": "0.1.0",
            "source": "https://plugins.example.com/p.zip",
            "integrity": HASH_A,
            "manifest": manifest,
        }
        catalog_path = tmp_path / "catalog.json"
        catalog_path.write_text(json.dumps({"version": 1, "plugins": [entry]}), encoding="utf-8")
        return catalog_path

    def test_unknown_catalog_entry_is_audited(self, client, app, make_user, login, tmp_path):
        owner = make_user()
        login()
        ws = _workspace(owner)
        app.config["PLUGIN_CATALOG_FILE"] = str(self._catalog_file(tmp_path))

        response = client.post(self._install_url(ws.id), json={"plugin_id": "ghost"})

        assert response.status_code == 404
        denied = [e for e in _audit_events(ws.id) if e.event_type == EVENT_PLUGIN_DENIED]
        assert len(denied) == 1
        assert denied[0].event_metadata["outcome"] == "denied"
        assert "not found" in denied[0].event_metadata["reason"]

    def test_catalog_unavailable_is_audited(self, client, app, make_user, login):
        owner = make_user()
        login()
        ws = _workspace(owner)
        app.config["PLUGIN_CATALOG_FILE"] = ""

        response = client.post(self._install_url(ws.id), json={"plugin_id": "catalog-plugin"})

        assert response.status_code == 404
        denied = [e for e in _audit_events(ws.id) if e.event_type == EVENT_PLUGIN_DENIED]
        assert len(denied) == 1
        assert "unavailable" in denied[0].event_metadata["reason"]

    def test_successful_catalog_install_is_audited(self, client, app, make_user, login, tmp_path):
        owner = make_user()
        login()
        ws = _workspace(owner)
        app.config["PLUGIN_CATALOG_FILE"] = str(self._catalog_file(tmp_path))
        app.config["PLUGIN_TRUST_POLICY"] = "if-present"

        response = client.post(self._install_url(ws.id), json={"plugin_id": "catalog-plugin"})

        assert response.status_code == 201
        events = [
            e
            for e in _audit_events(ws.id)
            if e.event_metadata.get("capability") == "remote_install"
        ]
        assert len(events) == 1
        assert events[0].event_metadata["outcome"] == "success"
        assert events[0].event_metadata["plugin_id"] == "catalog-plugin"


class TestLockfileCli:
    def test_lockfile_json(self, app, make_user, login, client):
        owner = make_user()
        login()
        ws = _workspace(owner)
        _lock(ws.id, owner.id, "catalog-plugin", version="0.1.0", integrity=HASH_A)

        result = app.test_cli_runner().invoke(args=["plugins", "lockfile", str(ws.id), "--json"])

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["lockfile_version"] == 1
        assert payload["plugins"][0]["plugin_id"] == "catalog-plugin"
        assert payload["plugins"][0]["integrity"] == normalize_integrity(HASH_A)

    def test_lockfile_human_output(self, app, make_user, login, client):
        owner = make_user()
        login()
        ws = _workspace(owner)
        _lock(ws.id, owner.id, "catalog-plugin", version="0.1.0", integrity=HASH_A)

        result = app.test_cli_runner().invoke(args=["plugins", "lockfile", str(ws.id)])

        assert result.exit_code == 0
        assert "catalog-plugin" in result.output
        assert "0.1.0" in result.output

    def test_lockfile_empty_workspace(self, app, make_user, login, client):
        owner = make_user()
        login()
        ws = _workspace(owner)

        result = app.test_cli_runner().invoke(args=["plugins", "lockfile", str(ws.id), "--json"])

        assert result.exit_code == 0
        assert json.loads(result.output) == {"lockfile_version": 1, "plugins": []}
