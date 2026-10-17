"""Tests for the marketplace plugin detail page (#417).

The detail page must render trust, compatibility, dependencies, capabilities and
the artifact hash. Owners see install/update/lifecycle controls; members get a
read-only view. Authorization is also enforced by the API, not just the UI.
"""

from app.extensions import db
from app.models import (
    CapabilityGrant,
    Plugin,
    PluginInstallation,
    PluginLockEntry,
    Workspace,
    WorkspaceMember,
)


def _plugin(plugin_id="detail-plugin", **overrides):
    values = {
        "id": plugin_id,
        "name": "Detail Plugin",
        "version": "1.2.3",
        "description": "Shows every marketplace detail field.",
        "author": "Detail Author",
        "entry_point": "plugins.detail:DetailPlugin",
        "capabilities": ["PROJECT_READ", "HTTP_REQUEST"],
        "permissions": ["network"],
        "dependencies": ["base-plugin>=1.0.0"],
        "compatibility": ">=0.1.0",
        "trust_state": "Trusted",
        "trust_publisher": "acme-labs",
        "configuration": {"endpoint": "https://api.example.com"},
    }
    values.update(overrides)
    return Plugin(**values)


def _workspace(db, owner, name="Detail WS"):
    workspace = Workspace(user_id=owner.id, name=name)
    db.session.add(workspace)
    db.session.commit()
    return workspace


def _member(db, workspace, user, role="contributor"):
    membership = WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=role)
    db.session.add(membership)
    db.session.commit()
    return membership


def _install(plugin, workspace, user):
    installation = PluginInstallation(
        plugin_id=plugin.id,
        workspace_id=workspace.id,
        enabled=True,
        config=plugin.configuration or {},
        installed_by_id=user.id,
    )
    db.session.add(installation)
    db.session.commit()
    return installation


def _lock(plugin, workspace, owner):
    entry = PluginLockEntry(
        workspace_id=workspace.id,
        owner_id=owner.id,
        plugin_id=plugin.id,
        version=plugin.version,
        source="https://plugins.example.com/detail-plugin-1.2.3.zip",
        integrity="sha256:" + "a" * 64,
        dependencies=plugin.dependencies or [],
    )
    db.session.add(entry)
    db.session.commit()
    return entry


class TestPluginDetailPage:
    def test_owner_sees_all_metadata_and_manage_controls(self, app, client, make_user, login):
        owner = make_user(username="owner", email="owner@example.com")
        login(email="owner@example.com")
        workspace = _workspace(db, owner)
        plugin = _plugin()
        db.session.add(plugin)
        db.session.commit()
        _install(plugin, workspace, owner)
        _lock(plugin, workspace, owner)

        response = client.get(f"/plugins/workspaces/{workspace.id}/plugins/{plugin.id}")
        html = response.get_data(as_text=True)

        assert response.status_code == 200
        for value in (
            "Detail Plugin",
            "1.2.3",
            "Detail Author",
            "plugins.detail:DetailPlugin",
            "Trusted",
            "acme-labs",
            "&gt;=0.1.0",
            "base-plugin&gt;=1.0.0",
            "PROJECT_READ",
            "HTTP_REQUEST",
            "sha256:" + "a" * 64,
            "https://plugins.example.com/detail-plugin-1.2.3.zip",
            "plugin-detail-update-toggle",
            "Disable",
            "Uninstall",
        ):
            assert value in html
        assert "read-only access" not in html
        assert 'id="plugin-detail-install"' not in html

    def test_member_sees_read_only_view(self, app, client, make_user, login):
        owner = make_user(username="owner", email="owner@example.com")
        member = make_user(username="member", email="member@example.com")
        login(email="member@example.com")
        workspace = _workspace(db, owner)
        plugin = _plugin()
        db.session.add(plugin)
        db.session.commit()
        _install(plugin, workspace, owner)
        _member(db, workspace, member)

        response = client.get(f"/plugins/workspaces/{workspace.id}/plugins/{plugin.id}")
        html = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "Detail Plugin" in html
        assert "read-only access" in html
        for control in (
            'id="plugin-controls"',
            'id="plugin-detail-update-toggle"',
            'id="plugin-detail-disable"',
            'id="plugin-detail-uninstall"',
        ):
            assert control not in html

    def test_non_member_gets_404(self, app, client, make_user, login):
        owner = make_user(username="owner", email="owner@example.com")
        make_user(username="outsider", email="outsider@example.com")
        login(email="outsider@example.com")
        workspace = _workspace(db, owner)
        plugin = _plugin()
        db.session.add(plugin)
        db.session.commit()

        response = client.get(f"/plugins/workspaces/{workspace.id}/plugins/{plugin.id}")

        assert response.status_code == 404

    def test_unknown_plugin_gets_404(self, app, client, make_user, login):
        owner = make_user(username="owner", email="owner@example.com")
        login(email="owner@example.com")
        workspace = _workspace(db, owner)

        assert client.get(f"/plugins/workspaces/{workspace.id}/plugins/ghost").status_code == 404

    def test_local_install_reports_no_artifact_hash(self, app, client, make_user, login):
        owner = make_user(username="owner", email="owner@example.com")
        login(email="owner@example.com")
        workspace = _workspace(db, owner)
        plugin = _plugin(trust_state="Unverified", trust_publisher=None)
        db.session.add(plugin)
        db.session.commit()
        _install(plugin, workspace, owner)

        html = client.get(f"/plugins/workspaces/{workspace.id}/plugins/{plugin.id}").get_data(
            as_text=True
        )

        assert "No artifact recorded" in html


class TestPluginDetailApi:
    def test_owner_serialization_includes_actions_and_artifact_hash(
        self, app, client, make_user, login
    ):
        owner = make_user(username="owner", email="owner@example.com")
        login(email="owner@example.com")
        workspace = _workspace(db, owner)
        plugin = _plugin()
        db.session.add(plugin)
        db.session.commit()
        _install(plugin, workspace, owner)
        _lock(plugin, workspace, owner)

        payload = client.get(
            f"/plugins/api/workspaces/{workspace.id}/plugins/{plugin.id}"
        ).get_json()

        assert payload["can_manage"] is True
        assert payload["role"] == "owner"
        assert payload["artifact_hash"] == "sha256:" + "a" * 64
        assert payload["artifact_sha256"] == payload["artifact_hash"]
        assert payload["integrity"] == payload["artifact_hash"]
        assert payload["source"].startswith("https://plugins.example.com/")
        assert payload["dependencies"] == ["base-plugin>=1.0.0"]
        assert payload["compatibility"] == ">=0.1.0"
        assert payload["trust_state"] == "Trusted"
        assert payload["actions"]["update"] is True
        assert payload["actions"]["disable"] is True
        assert payload["actions"]["install"] is False

    def test_member_serialization_is_read_only(self, app, client, make_user, login):
        owner = make_user(username="owner", email="owner@example.com")
        member = make_user(username="member", email="member@example.com")
        login(email="member@example.com")
        workspace = _workspace(db, owner)
        plugin = _plugin()
        db.session.add(plugin)
        db.session.commit()
        _install(plugin, workspace, owner)
        _member(db, workspace, member)

        payload = client.get(
            f"/plugins/api/workspaces/{workspace.id}/plugins/{plugin.id}"
        ).get_json()

        assert payload["can_manage"] is False
        assert payload["role"] == "contributor"
        assert all(action is False for action in payload["actions"].values())
        assert payload["can_install"] is False
        assert payload["can_update"] is False


class TestInstallRegisteredPlugin:
    def test_owner_can_install_a_registered_plugin_without_grants(
        self, app, client, make_user, login
    ):
        owner = make_user(username="owner", email="owner@example.com")
        login(email="owner@example.com")
        workspace = _workspace(db, owner)
        plugin = _plugin()
        db.session.add(plugin)
        db.session.commit()

        response = client.post(
            f"/plugins/api/workspaces/{workspace.id}/plugins/{plugin.id}/install"
        )

        assert response.status_code == 201
        payload = response.get_json()
        assert payload["installed"] is True
        assert payload["enabled"] is True
        assert payload["can_update"] is True
        assert (
            PluginInstallation.query.filter_by(
                plugin_id=plugin.id, workspace_id=workspace.id
            ).count()
            == 1
        )
        assert CapabilityGrant.query.filter_by(plugin_id=plugin.id).count() == 0

    def test_duplicate_install_returns_409(self, app, client, make_user, login):
        owner = make_user(username="owner", email="owner@example.com")
        login(email="owner@example.com")
        workspace = _workspace(db, owner)
        plugin = _plugin()
        db.session.add(plugin)
        db.session.commit()
        path = f"/plugins/api/workspaces/{workspace.id}/plugins/{plugin.id}/install"

        assert client.post(path).status_code == 201
        assert client.post(path).status_code == 409

    def test_member_cannot_install(self, app, client, make_user, login):
        owner = make_user(username="owner", email="owner@example.com")
        member = make_user(username="member", email="member@example.com")
        login(email="member@example.com")
        workspace = _workspace(db, owner)
        plugin = _plugin()
        db.session.add(plugin)
        db.session.commit()
        _member(db, workspace, member)

        response = client.post(
            f"/plugins/api/workspaces/{workspace.id}/plugins/{plugin.id}/install"
        )

        assert response.status_code == 403

    def test_incompatible_plugin_is_refused(self, app, client, make_user, login):
        owner = make_user(username="owner", email="owner@example.com")
        login(email="owner@example.com")
        workspace = _workspace(db, owner)
        plugin = _plugin(compatibility=">=99.0.0")
        db.session.add(plugin)
        db.session.commit()

        response = client.post(
            f"/plugins/api/workspaces/{workspace.id}/plugins/{plugin.id}/install"
        )

        assert response.status_code == 400
        assert ">=99.0.0" in response.get_json()["error"]
        assert PluginInstallation.query.filter_by(plugin_id=plugin.id).count() == 0

    def test_unknown_plugin_returns_404(self, app, client, make_user, login):
        owner = make_user(username="owner", email="owner@example.com")
        login(email="owner@example.com")
        workspace = _workspace(db, owner)

        response = client.post(f"/plugins/api/workspaces/{workspace.id}/plugins/ghost/install")

        assert response.status_code == 404
