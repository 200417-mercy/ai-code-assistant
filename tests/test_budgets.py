"""Budget CRUD and authorization tests (#461, #468).

Covers every role: user owner, other user, workspace owner, workspace member,
non-member, and site administrator. Inaccessible and missing budgets must be
indistinguishable.
"""

from app.extensions import db
from app.models import User, Workspace, WorkspaceMember
from app.models.workspace_member import ROLE_CONTRIBUTOR, ROLE_VIEWER, STATUS_ACTIVE

BUDGETS_URL = "/billing/api/budgets"


def _user(username, email, *, is_admin=False):
    user = User(username=username, email=email, is_admin=is_admin)
    user.set_password("supersecret123")
    db.session.add(user)
    db.session.commit()
    return user


def _workspace(owner, name="Budget workspace"):
    workspace = Workspace(user_id=owner.id, name=name)
    db.session.add(workspace)
    db.session.commit()
    return workspace


def _member(workspace, user, role=ROLE_VIEWER):
    membership = WorkspaceMember(
        workspace_id=workspace.id,
        user_id=user.id,
        role=role,
        status=STATUS_ACTIVE,
    )
    db.session.add(membership)
    db.session.commit()
    return membership


def _create(client, **overrides):
    payload = {
        "name": "Monthly API budget",
        "limit_amount": 100.0,
        "period": "monthly",
        "currency": "USD",
    }
    payload.update(overrides)
    return client.post(BUDGETS_URL, json=payload)


def _error(response):
    return response.get_json()["error"]


class TestBudgetCrud:
    def test_create_read_update_delete_user_budget(self, client, make_user, login):
        user = make_user(username="owner", email="owner@example.com")
        login(email="owner@example.com")

        created = _create(client)
        assert created.status_code == 201
        body = created.get_json()
        assert body["scope"] == "user"
        assert body["user_id"] == user.id
        assert body["workspace_id"] is None
        assert body["limit_amount"] == 100.0
        budget_id = body["id"]

        listing = client.get(BUDGETS_URL)
        assert listing.status_code == 200
        assert [item["id"] for item in listing.get_json()["budgets"]] == [budget_id]

        fetched = client.get(f"{BUDGETS_URL}/{budget_id}")
        assert fetched.status_code == 200
        assert fetched.get_json()["name"] == "Monthly API budget"

        updated = client.patch(
            f"{BUDGETS_URL}/{budget_id}",
            json={"limit_amount": 250.5, "period": "weekly", "name": "Weekly"},
        )
        assert updated.status_code == 200
        assert updated.get_json()["limit_amount"] == 250.5
        assert updated.get_json()["period"] == "weekly"
        assert updated.get_json()["name"] == "Weekly"

        deleted = client.delete(f"{BUDGETS_URL}/{budget_id}")
        assert deleted.status_code == 204
        assert client.get(f"{BUDGETS_URL}/{budget_id}").status_code == 404

    def test_create_workspace_budget(self, client, make_user, login):
        owner = make_user(username="owner", email="owner@example.com")
        workspace = _workspace(owner)
        login(email="owner@example.com")

        response = _create(client, workspace_id=workspace.id, limit_amount=500)
        assert response.status_code == 201
        assert response.get_json()["scope"] == "workspace"
        assert response.get_json()["workspace_id"] == workspace.id

    def test_validation_errors_are_explicit(self, client, make_user, login):
        make_user(username="owner", email="owner@example.com")
        login(email="owner@example.com")

        cases = [
            ({"limit_amount": None}, "limit_amount"),
            ({"limit_amount": 0}, "greater than zero"),
            ({"limit_amount": -1}, "greater than zero"),
            ({"limit_amount": "not-a-number"}, "number"),
            ({"period": "fortnightly"}, "period"),
            ({"currency": "US"}, "currency"),
            ({"name": "   "}, "name"),
        ]
        for overrides, expected in cases:
            response = _create(client, **overrides)
            assert response.status_code == 400, overrides
            assert expected in _error(response)

    def test_scope_and_owner_cannot_be_changed(self, client, make_user, login):
        user = make_user(username="owner", email="owner@example.com")
        other = make_user(username="other", email="other@example.com")
        login(email="owner@example.com")
        budget_id = _create(client).get_json()["id"]

        response = client.patch(f"{BUDGETS_URL}/{budget_id}", json={"workspace_id": 1})
        assert response.status_code == 400
        assert "scope" in _error(response)

        response = client.patch(f"{BUDGETS_URL}/{budget_id}", json={"user_id": other.id})
        assert response.status_code == 400
        assert "owner" in _error(response)

        assert user.id != other.id


class TestUserBudgetAuthorization:
    def test_other_user_gets_uniform_404(self, client, make_user, login):
        owner = make_user(username="owner", email="owner@example.com")
        make_user(username="other", email="other@example.com")
        login(email="owner@example.com")
        budget_id = _create(client).get_json()["id"]

        login(email="other@example.com")
        for method, url, kwargs in [
            ("get", f"{BUDGETS_URL}/{budget_id}", {}),
            ("patch", f"{BUDGETS_URL}/{budget_id}", {"json": {"limit_amount": 1}}),
            ("delete", f"{BUDGETS_URL}/{budget_id}", {}),
        ]:
            response = getattr(client, method)(url, **kwargs)
            assert response.status_code == 404
            assert response.get_json() == {
                "error": "Budget not found.",
                "kind": "not_found",
            }
        assert owner.id

    def test_site_admin_cannot_read_another_users_user_budget(self, client, make_user, login):
        make_user(username="owner", email="owner@example.com")
        admin = make_user(username="admin", email="admin@example.com")
        admin.is_admin = True
        db.session.commit()
        login(email="owner@example.com")
        budget_id = _create(client).get_json()["id"]

        login(email="admin@example.com")
        assert client.get(f"{BUDGETS_URL}/{budget_id}").status_code == 404
        assert client.get(BUDGETS_URL).get_json()["budgets"] == []


class TestWorkspaceBudgetAuthorization:
    def test_workspace_owner_can_manage_workspace_budget(self, client, make_user, login):
        owner = make_user(username="owner", email="owner@example.com")
        workspace = _workspace(owner)
        login(email="owner@example.com")
        budget_id = _create(client, workspace_id=workspace.id).get_json()["id"]

        assert client.get(f"{BUDGETS_URL}/{budget_id}").status_code == 200
        assert (
            client.patch(f"{BUDGETS_URL}/{budget_id}", json={"limit_amount": 42}).status_code == 200
        )
        assert client.delete(f"{BUDGETS_URL}/{budget_id}").status_code == 204

    def test_workspace_member_gets_uniform_404(self, client, make_user, login):
        owner = make_user(username="owner", email="owner@example.com")
        member = make_user(username="member", email="member@example.com")
        workspace = _workspace(owner)
        _member(workspace, member, ROLE_CONTRIBUTOR)
        login(email="owner@example.com")
        budget_id = _create(client, workspace_id=workspace.id).get_json()["id"]

        login(email="member@example.com")
        for method, url, kwargs in [
            ("get", f"{BUDGETS_URL}/{budget_id}", {}),
            ("patch", f"{BUDGETS_URL}/{budget_id}", {"json": {"limit_amount": 1}}),
            ("delete", f"{BUDGETS_URL}/{budget_id}", {}),
        ]:
            response = getattr(client, method)(url, **kwargs)
            assert response.status_code == 404
            assert response.get_json() == {
                "error": "Budget not found.",
                "kind": "not_found",
            }

        # A member cannot create a budget in a workspace they do not own.
        assert _create(client, workspace_id=workspace.id).status_code == 404
        # The list remains scoped to the member's own (empty) user budgets.
        assert client.get(BUDGETS_URL).get_json()["budgets"] == []

    def test_non_member_gets_uniform_404(self, client, make_user, login):
        owner = make_user(username="owner", email="owner@example.com")
        make_user(username="outsider", email="outsider@example.com")
        workspace = _workspace(owner)
        login(email="owner@example.com")
        budget_id = _create(client, workspace_id=workspace.id).get_json()["id"]

        login(email="outsider@example.com")
        assert client.get(f"{BUDGETS_URL}/{budget_id}").status_code == 404
        assert _create(client, workspace_id=workspace.id).status_code == 404

    def test_site_admin_can_manage_workspace_budgets(self, client, make_user, login):
        owner = make_user(username="owner", email="owner@example.com")
        admin = make_user(username="admin", email="admin@example.com")
        admin.is_admin = True
        db.session.commit()
        workspace = _workspace(owner)
        login(email="owner@example.com")
        budget_id = _create(client, workspace_id=workspace.id).get_json()["id"]

        login(email="admin@example.com")
        listing = client.get(BUDGETS_URL)
        assert [item["id"] for item in listing.get_json()["budgets"]] == [budget_id]
        assert client.get(f"{BUDGETS_URL}/{budget_id}").status_code == 200
        assert (
            client.patch(f"{BUDGETS_URL}/{budget_id}", json={"limit_amount": 900}).status_code
            == 200
        )
        assert _create(client, workspace_id=workspace.id).status_code == 201
        assert client.delete(f"{BUDGETS_URL}/{budget_id}").status_code == 204

    def test_list_only_returns_accessible_workspace_budgets(self, client, make_user, login):
        owner = make_user(username="owner", email="owner@example.com")
        other_owner = make_user(username="other", email="other@example.com")
        member = make_user(username="member", email="member@example.com")

        first = _workspace(owner, name="First")
        second = _workspace(other_owner, name="Second")
        _member(first, member, ROLE_VIEWER)

        login(email="owner@example.com")
        first_id = _create(client, workspace_id=first.id).get_json()["id"]

        login(email="other@example.com")
        second_id = _create(client, workspace_id=second.id).get_json()["id"]

        login(email="member@example.com")
        assert client.get(BUDGETS_URL).get_json()["budgets"] == []

        login(email="owner@example.com")
        ids = {item["id"] for item in client.get(BUDGETS_URL).get_json()["budgets"]}
        assert ids == {first_id}
        assert second_id not in ids
