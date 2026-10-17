"""API tests for per-workspace price overrides (#442)."""

from app.extensions import db
from app.models import Workspace, WorkspaceMember, WorkspacePriceOverride


def _workspace(db, owner):
    workspace = Workspace(user_id=owner.id, name="Pricing WS")
    db.session.add(workspace)
    db.session.commit()
    return workspace


def _member(db, workspace, user, role="contributor"):
    db.session.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=role))
    db.session.commit()


def _url(workspace_id, suffix=""):
    return f"/workspaces/api/workspaces/{workspace_id}/pricing{suffix}"


class TestWorkspacePricingApi:
    def test_owner_can_read_effective_prices_and_overrides(self, app, client, make_user, login):
        owner = make_user(username="owner", email="owner@example.com")
        login(email="owner@example.com")
        workspace = _workspace(db, owner)

        response = client.get(_url(workspace.id))

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["workspace_id"] == workspace.id
        assert payload["overrides"] == []
        assert "openai" in payload["effective"]
        assert payload["effective"]["openai"][0]["disclaimer"]

    def test_owner_can_create_update_and_delete_an_override(self, app, client, make_user, login):
        owner = make_user(username="owner", email="owner@example.com")
        login(email="owner@example.com")
        workspace = _workspace(db, owner)

        created = client.put(
            _url(workspace.id, "/overrides"),
            json={
                "provider": "openai",
                "model": "gpt-4o",
                "input_per_1k_usd": 5.0,
                "output_per_1k_usd": 7.0,
            },
        )
        assert created.status_code == 200
        payload = created.get_json()
        assert len(payload["overrides"]) == 1
        assert payload["overrides"][0]["input_per_1k_usd"] == 5.0
        entry = next(item for item in payload["effective"]["openai"] if item["model"] == "gpt-4o")
        assert entry["input_per_1k_usd"] == 5.0
        assert entry["output_per_1k_usd"] == 7.0

        updated = client.put(
            _url(workspace.id, "/overrides"),
            json={
                "provider": "openai",
                "model": "gpt-4o",
                "input_per_1k_usd": 6.0,
                "output_per_1k_usd": 8.0,
            },
        )
        assert updated.status_code == 200
        assert WorkspacePriceOverride.query.count() == 1
        assert updated.get_json()["overrides"][0]["input_per_1k_usd"] == 6.0

        deleted = client.delete(_url(workspace.id, "/overrides/openai/gpt-4o"))
        assert deleted.status_code == 200
        assert deleted.get_json()["overrides"] == []
        assert WorkspacePriceOverride.query.count() == 0

    def test_owner_can_delete_using_a_json_body(self, app, client, make_user, login):
        owner = make_user(username="owner", email="owner@example.com")
        login(email="owner@example.com")
        workspace = _workspace(db, owner)
        assert (
            client.put(
                _url(workspace.id, "/overrides"),
                json={
                    "provider": "openai",
                    "model": "gpt-4o",
                    "input_per_1k_usd": 1,
                    "output_per_1k_usd": 2,
                },
            ).status_code
            == 200
        )

        response = client.delete(
            _url(workspace.id, "/overrides"),
            json={"provider": "openai", "model": "gpt-4o"},
        )

        assert response.status_code == 200
        assert WorkspacePriceOverride.query.count() == 0

    def test_owner_can_set_multiple_overrides_at_once(self, app, client, make_user, login):
        owner = make_user(username="owner", email="owner@example.com")
        login(email="owner@example.com")
        workspace = _workspace(db, owner)

        response = client.put(
            _url(workspace.id, "/overrides"),
            json={
                "overrides": [
                    {
                        "provider": "openai",
                        "model": "gpt-4o",
                        "input_per_1k_usd": 1,
                        "output_per_1k_usd": 2,
                    },
                    {
                        "provider": "openai",
                        "model": "gpt-4o-mini",
                        "input_per_1k_usd": 0.1,
                        "output_per_1k_usd": 0.2,
                    },
                ]
            },
        )

        assert response.status_code == 200
        assert len(response.get_json()["overrides"]) == 2

    def test_member_cannot_read_or_write_overrides(self, app, client, make_user, login):
        owner = make_user(username="owner", email="owner@example.com")
        member = make_user(username="member", email="member@example.com")
        login(email="member@example.com")
        workspace = _workspace(db, owner)
        _member(db, workspace, member)

        assert client.get(_url(workspace.id)).status_code == 403
        assert (
            client.put(
                _url(workspace.id, "/overrides"),
                json={
                    "provider": "openai",
                    "model": "gpt-4o",
                    "input_per_1k_usd": 1,
                    "output_per_1k_usd": 2,
                },
            ).status_code
            == 403
        )
        assert WorkspacePriceOverride.query.count() == 0

    def test_non_member_gets_404(self, app, client, make_user, login):
        owner = make_user(username="owner", email="owner@example.com")
        make_user(username="outsider", email="outsider@example.com")
        login(email="outsider@example.com")
        workspace = _workspace(db, owner)

        assert client.get(_url(workspace.id)).status_code == 404
        assert (
            client.put(
                _url(workspace.id, "/overrides"),
                json={
                    "provider": "openai",
                    "model": "gpt-4o",
                    "input_per_1k_usd": 1,
                    "output_per_1k_usd": 2,
                },
            ).status_code
            == 404
        )

    def test_invalid_payloads_are_rejected(self, app, client, make_user, login):
        owner = make_user(username="owner", email="owner@example.com")
        login(email="owner@example.com")
        workspace = _workspace(db, owner)

        for body in (
            {},
            {"provider": "openai", "model": "gpt-4o", "input_per_1k_usd": -1},
            {"provider": "openai", "model": "gpt-4o", "output_per_1k_usd": "nope"},
            {"overrides": []},
            {"overrides": ["not-an-object"]},
        ):
            response = client.put(_url(workspace.id, "/overrides"), json=body)
            assert response.status_code == 400

        missing = client.delete(
            _url(workspace.id, "/overrides"),
            json={"provider": "openai", "model": "ghost"},
        )
        assert missing.status_code == 404
