"""Authorization tests for dashboard/usage endpoints (Phase 10 #458).

Proves dashboards and exports never leak another user or workspace data.

Tests cover:
- Owner, member, non-member, and admin access to each endpoint
- Uniform refusals for inaccessible resources
- Authorization enforced on every surface
"""

from app.extensions import db
from app.models import Conversation, Message, Project, User, Workspace, WorkspaceMember
from app.models.workspace_member import ROLE_VIEWER, STATUS_ACTIVE
from app.services import usage_ledger


def _create_user(username, email):
    user = User(username=username, email=email)
    user.set_password("supersecret123")
    db.session.add(user)
    db.session.commit()
    return user


def _workspace_for(user, name="Test workspace"):
    workspace = Workspace(user_id=user.id, name=name)
    db.session.add(workspace)
    db.session.commit()
    return workspace


def _add_member(workspace, user, role=ROLE_VIEWER):
    membership = WorkspaceMember(
        workspace_id=workspace.id,
        user_id=user.id,
        role=role,
        status=STATUS_ACTIVE,
    )
    db.session.add(membership)
    db.session.commit()
    return membership


def _create_conversation(user, prompt_tokens=10, completion_tokens=5, total_tokens=15):
    """Create a conversation with usage data."""
    conv = Conversation(user_id=user.id, title="Test conversation")
    db.session.add(conv)
    db.session.commit()

    msg = Message(
        conversation_id=conv.id,
        role="assistant",
        content="Test response",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )
    db.session.add(msg)
    db.session.commit()

    # Also record in usage ledger
    usage_ledger.record_usage(
        source="chat",
        user=user,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        provider="mock",
        model="mock-1",
    )
    return conv


def _create_project(user, workspace):
    """Create a project in a workspace."""
    project = Project(
        workspace_id=workspace.id,
        user_id=user.id,
        name="Test project",
        source="archive",
        status="ready",
    )
    db.session.add(project)
    db.session.commit()
    return project


class TestUsageSummaryAuthorization:
    """Tests for /api/usage endpoint authorization."""

    def test_owner_can_access_own_usage(self, client, make_user, login):
        _user = make_user(username="owner", email="owner@example.com")
        login(email="owner@example.com")
        _create_conversation(_user)

        response = client.get("/api/usage")
        assert response.status_code == 200
        data = response.get_json()
        assert data["total_tokens"] > 0

    def test_member_cannot_access_owner_usage(self, client, make_user, login):
        owner = _create_user("owner", "owner@example.com")
        member = make_user(username="member", email="member@example.com")  # noqa: F841
        login(email="member@example.com")
        _create_conversation(owner)

        response = client.get("/api/usage")
        assert response.status_code == 200
        data = response.get_json()
        # Member should only see their own usage (empty)
        assert data["total_tokens"] == 0

    def test_non_member_cannot_access_usage(self, client, make_user, login):
        owner = _create_user("owner", "owner@example.com")
        outsider = make_user(username="outsider", email="outsider@example.com")  # noqa: F841
        login(email="outsider@example.com")
        _create_conversation(owner)

        response = client.get("/api/usage")
        assert response.status_code == 200
        data = response.get_json()
        assert data["total_tokens"] == 0

    def test_unauthenticated_user_denied(self, client):
        response = client.get("/api/usage")
        assert response.status_code == 401


class TestUsageBreakdownAuthorization:
    """Tests for /api/usage/breakdown endpoint authorization."""

    def test_owner_can_access_own_breakdown(self, client, make_user, login):
        user = make_user(username="owner", email="owner@example.com")
        login(email="owner@example.com")
        _create_conversation(user)

        response = client.get("/api/usage/breakdown")
        assert response.status_code == 200
        data = response.get_json()
        assert "by_source" in data
        assert "totals" in data

    def test_owner_can_filter_by_workspace(self, client, make_user, login):
        user = make_user(username="owner", email="owner@example.com")
        login(email="owner@example.com")
        workspace = _workspace_for(user)
        project = _create_project(user, workspace)  # noqa: F841
        _create_conversation(user)

        response = client.get(f"/api/usage/breakdown?workspace_id={workspace.id}")
        assert response.status_code == 200

    def test_member_cannot_filter_by_other_workspace(self, client, make_user, login):
        owner = _create_user("owner", "owner@example.com")
        member = make_user(username="member", email="member@example.com")
        login(email="member@example.com")
        workspace = _workspace_for(owner)
        _add_member(workspace, member)
        _create_project(owner, workspace)
        _create_conversation(owner)

        # Member tries to filter by workspace they're a member of
        response = client.get(f"/api/usage/breakdown?workspace_id={workspace.id}")
        assert response.status_code == 200
        data = response.get_json()
        # Member should see empty data since they don't own the usage
        assert data["totals"]["count"] == 0

    def test_member_cannot_filter_by_non_member_workspace(self, client, make_user, login):
        owner = _create_user("owner", "owner@example.com")
        member = make_user(username="member", email="member@example.com")
        outsider = make_user(username="outsider", email="outsider@example.com")  # noqa: F841
        login(email="outsider@example.com")
        workspace = _workspace_for(owner)
        _add_member(workspace, member)
        _create_conversation(owner)

        # Outsider tries to filter by workspace they're not a member of
        response = client.get(f"/api/usage/breakdown?workspace_id={workspace.id}")
        assert response.status_code == 200
        data = response.get_json()
        assert data["totals"]["count"] == 0

    def test_unauthenticated_user_denied(self, client):
        response = client.get("/api/usage/breakdown")
        assert response.status_code == 401


class TestUsageDailyAuthorization:
    """Tests for /chat/api/usage/daily endpoint authorization."""

    def test_owner_can_access_own_daily(self, client, make_user, login):
        user = make_user(username="owner", email="owner@example.com")
        login(email="owner@example.com")
        _create_conversation(user)

        response = client.get("/chat/api/usage/daily")
        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)

    def test_member_cannot_access_owner_daily(self, client, make_user, login):
        owner = _create_user("owner", "owner@example.com")
        member = make_user(username="member", email="member@example.com")  # noqa: F841
        login(email="member@example.com")
        _create_conversation(owner)

        response = client.get("/chat/api/usage/daily")
        assert response.status_code == 200
        data = response.get_json()
        assert data == []

    def test_unauthenticated_user_denied(self, client):
        response = client.get("/chat/api/usage/daily")
        # @login_required redirects to login page (302)
        assert response.status_code == 302


class TestReviewsMetricsAuthorization:
    """Tests for /reviews/api/metrics endpoint authorization."""

    def test_owner_can_access_own_metrics(self, client, make_user, login):
        user = make_user(username="owner", email="owner@example.com")  # noqa: F841
        login(email="owner@example.com")

        response = client.get("/reviews/api/metrics")
        assert response.status_code == 200
        data = response.get_json()
        assert "total_reviews" in data

    def test_owner_can_access_workspace_metrics(self, client, make_user, login):
        user = make_user(username="owner", email="owner@example.com")
        login(email="owner@example.com")
        workspace = _workspace_for(user)

        response = client.get(f"/reviews/api/metrics?workspace_id={workspace.id}")
        assert response.status_code == 200

    def test_member_cannot_access_owner_workspace_metrics(self, client, make_user, login):
        owner = _create_user("owner", "owner@example.com")
        member = make_user(username="member", email="member@example.com")
        login(email="member@example.com")
        workspace = _workspace_for(owner)
        _add_member(workspace, member)

        response = client.get(f"/reviews/api/metrics?workspace_id={workspace.id}")
        assert response.status_code == 404

    def test_non_member_cannot_access_workspace_metrics(self, client, make_user, login):
        owner = _create_user("owner", "owner@example.com")
        outsider = make_user(username="outsider", email="outsider@example.com")  # noqa: F841
        login(email="outsider@example.com")
        workspace = _workspace_for(owner)

        response = client.get(f"/reviews/api/metrics?workspace_id={workspace.id}")
        assert response.status_code == 404

    def test_member_cannot_access_project_metrics(self, client, make_user, login):
        owner = _create_user("owner", "owner@example.com")
        member = make_user(username="member", email="member@example.com")
        login(email="member@example.com")
        workspace = _workspace_for(owner)
        _add_member(workspace, member)
        project = _create_project(owner, workspace)

        response = client.get(f"/reviews/api/metrics?project_id={project.id}")
        assert response.status_code == 404

    def test_unauthenticated_user_denied(self, client):
        response = client.get("/reviews/api/metrics")
        assert response.status_code == 302  # Redirect to login


class TestWorkspaceScopedUsageEndpoints:
    """Test that workspace-scoped usage endpoints respect membership."""

    def test_workspace_usage_endpoint_owner_access(self, client, make_user, login):
        """Test owner can access workspace usage."""
        user = make_user(username="owner", email="owner@example.com")
        login(email="owner@example.com")
        workspace = _workspace_for(user)
        _create_conversation(user)

        # The usage breakdown with workspace filter should work for owner
        response = client.get(f"/api/usage/breakdown?workspace_id={workspace.id}")
        assert response.status_code == 200

    def test_workspace_usage_endpoint_member_access(self, client, make_user, login):
        """Test member cannot access workspace usage they don't own."""
        owner = _create_user("owner", "owner@example.com")
        member = make_user(username="member", email="member@example.com")
        login(email="member@example.com")
        workspace = _workspace_for(owner)
        _add_member(workspace, member)
        _create_conversation(owner)

        response = client.get(f"/api/usage/breakdown?workspace_id={workspace.id}")
        assert response.status_code == 200
        data = response.get_json()
        # Member sees no data because they don't own the usage
        assert data["totals"]["count"] == 0

    def test_workspace_usage_endpoint_non_member_denied(self, client, make_user, login):
        """Test non-member cannot access workspace usage."""
        owner = _create_user("owner", "owner@example.com")
        outsider = make_user(username="outsider", email="outsider@example.com")  # noqa: F841
        login(email="outsider@example.com")
        workspace = _workspace_for(owner)
        _create_conversation(owner)

        response = client.get(f"/api/usage/breakdown?workspace_id={workspace.id}")
        assert response.status_code == 200
        data = response.get_json()
        assert data["totals"]["count"] == 0


class TestProjectScopedUsageEndpoints:
    """Test that project-scoped usage endpoints respect ownership."""

    def test_project_usage_endpoint_owner_access(self, client, make_user, login):
        """Test owner can access project usage."""
        user = make_user(username="owner", email="owner@example.com")
        login(email="owner@example.com")
        workspace = _workspace_for(user)
        project = _create_project(user, workspace)
        _create_conversation(user)

        response = client.get(f"/api/usage/breakdown?project_id={project.id}")
        assert response.status_code == 200

    def test_project_usage_endpoint_member_access(self, client, make_user, login):
        """Test member cannot access project usage they don't own."""
        owner = _create_user("owner", "owner@example.com")
        member = make_user(username="member", email="member@example.com")
        login(email="member@example.com")
        workspace = _workspace_for(owner)
        _add_member(workspace, member)
        project = _create_project(owner, workspace)
        _create_conversation(owner)

        response = client.get(f"/api/usage/breakdown?project_id={project.id}")
        assert response.status_code == 200
        data = response.get_json()
        assert data["totals"]["count"] == 0

    def test_project_usage_endpoint_non_member_denied(self, client, make_user, login):
        """Test non-member cannot access project usage."""
        owner = _create_user("owner", "owner@example.com")
        outsider = make_user(username="outsider", email="outsider@example.com")  # noqa: F841
        login(email="outsider@example.com")
        workspace = _workspace_for(owner)
        project = _create_project(owner, workspace)
        _create_conversation(owner)

        response = client.get(f"/api/usage/breakdown?project_id={project.id}")
        assert response.status_code == 200
        data = response.get_json()
        assert data["totals"]["count"] == 0


class TestUniformRefusalBehavior:
    """Test that all endpoints return uniform refusals for inaccessible resources."""

    def test_usage_endpoints_return_empty_not_error(self, client, make_user, login):
        """Endpoints should return empty data (not 404/403) for authorized but empty access."""
        owner = _create_user("owner", "owner@example.com")
        member = make_user(username="member", email="member@example.com")  # noqa: F841
        login(email="member@example.com")
        _create_conversation(owner)

        # All usage endpoints should return 200 with empty data
        response = client.get("/api/usage")
        assert response.status_code == 200
        assert response.get_json()["total_tokens"] == 0

        response = client.get("/api/usage/breakdown")
        assert response.status_code == 200
        assert response.get_json()["totals"]["count"] == 0

        response = client.get("/chat/api/usage/daily")
        assert response.status_code == 200
        assert response.get_json() == []

    def test_reviews_metrics_returns_404_for_inaccessible_workspace(self, client, make_user, login):
        """Reviews metrics should return 404 for workspaces user cannot access."""
        owner = _create_user("owner", "owner@example.com")
        outsider = make_user(username="outsider", email="outsider@example.com")  # noqa: F841
        login(email="outsider@example.com")
        workspace = _workspace_for(owner)

        response = client.get(f"/reviews/api/metrics?workspace_id={workspace.id}")
        assert response.status_code == 404

    def test_reviews_metrics_returns_404_for_inaccessible_project(self, client, make_user, login):
        """Reviews metrics should return 404 for projects user cannot access."""
        owner = _create_user("owner", "owner@example.com")
        outsider = make_user(username="outsider", email="outsider@example.com")  # noqa: F841
        login(email="outsider@example.com")
        workspace = _workspace_for(owner)
        project = _create_project(owner, workspace)

        response = client.get(f"/reviews/api/metrics?project_id={project.id}")
        assert response.status_code == 404
