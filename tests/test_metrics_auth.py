"""Admin-session access to /metrics when no bearer token is configured (#370)."""

from __future__ import annotations

from app.observability.metrics import REGISTRY
from app.services import usage_ledger


def _login(client, email="admin@example.com"):
    response = client.post(
        "/auth/login",
        data={"email": email, "password": "supersecret123"},
        follow_redirects=True,
    )
    assert response.status_code == 200


def test_admin_session_can_read_metrics(app, make_user, db):
    app.config["METRICS_TOKEN"] = ""
    admin = make_user(username="metrics-admin", email="admin@example.com")
    admin.is_admin = True
    db.session.commit()
    client = app.test_client()
    _login(client)

    REGISTRY.reset()
    try:
        response = client.get("/metrics")
        assert response.status_code == 200
        assert response.content_type.startswith("text/plain")
        assert "# TYPE http_requests_total counter" in response.get_data(as_text=True)
    finally:
        REGISTRY.reset()


def test_non_admin_session_is_denied(app, make_user, db):
    app.config["METRICS_TOKEN"] = ""
    make_user(username="metrics-user", email="user@example.com")
    client = app.test_client()
    _login(client, email="user@example.com")

    response = client.get("/metrics")
    assert response.status_code == 401
    assert "authentication required" in response.get_data(as_text=True)


def test_anonymous_is_denied_when_no_token_is_configured(app):
    app.config["METRICS_TOKEN"] = ""
    response = app.test_client().get("/metrics")
    assert response.status_code == 401


def test_bearer_token_is_accepted_by_the_real_app(app):
    app.config["METRICS_TOKEN"] = "scrape-secret"
    client = app.test_client()
    assert client.get("/metrics").status_code == 401
    response = client.get("/metrics", headers={"Authorization": "Bearer scrape-secret"})
    assert response.status_code == 200
    assert "# TYPE http_requests_total counter" in response.get_data(as_text=True)


def test_usage_ledger_updates_token_and_cost_counters(app, make_user):
    app.config["METRICS_TOKEN"] = "scrape-secret"
    user = make_user(username="metrics-usage", email="usage@example.com")
    REGISTRY.reset()
    try:
        usage_ledger.record_usage(
            source="chat",
            user=user,
            provider="mock",
            model="mock-1",
            prompt_tokens=3,
            completion_tokens=2,
            cost_estimate=0.5,
        )
        body = (
            app.test_client()
            .get("/metrics", headers={"Authorization": "Bearer scrape-secret"})
            .get_data(as_text=True)
        )
        assert 'llm_tokens_total{provider="mock",model="mock-1",direction="in"} 3' in body
        assert 'llm_tokens_total{provider="mock",model="mock-1",direction="out"} 2' in body
        assert 'llm_cost_usd_total{provider="mock",model="mock-1"} 0.5' in body
    finally:
        REGISTRY.reset()
