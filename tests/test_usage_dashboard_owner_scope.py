"""Owner-scoping and completeness checks for the per-user usage dashboard (#446)."""

from __future__ import annotations

from app.services import usage_ledger


def _record(user, *, provider, model, prompt, source="chat"):
    return usage_ledger.record_usage(
        source=source,
        user=user,
        provider=provider,
        model=model,
        prompt_tokens=prompt,
        completion_tokens=0,
        cost_estimate=0.01,
    )


def test_page_renders_totals_and_all_three_breakdowns(client, db, make_user, login):
    user = make_user(username="dash-user", email="dash@example.com")
    _record(user, provider="openai", model="gpt-4o", prompt=123)
    _record(user, provider="anthropic", model="claude", prompt=7, source="analysis")
    login(email="dash@example.com")

    response = client.get("/billing/usage")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "By provider" in body
    assert "By model" in body
    assert "By source" in body
    assert "openai" in body
    assert "gpt-4o" in body
    assert "analysis" in body
    assert ">130<" in body  # total tokens


def test_page_never_shows_another_users_usage(client, db, make_user, login):
    owner = make_user(username="owner", email="owner@example.com")
    other = make_user(username="other", email="other@example.com")
    _record(owner, provider="owner-provider", model="owner-model", prompt=1)
    _record(other, provider="secret-provider", model="secret-model", prompt=999_999)
    login(email="owner@example.com")

    body = client.get("/billing/usage").get_data(as_text=True)

    assert "owner-provider" in body
    assert "secret-provider" not in body
    assert "secret-model" not in body


def test_page_has_a_stable_empty_state(client, db, make_user, login):
    make_user(username="empty", email="empty@example.com")
    login(email="empty@example.com")

    body = client.get("/billing/usage").get_data(as_text=True)

    assert "No usage in this window." in body
    assert "Unknown" not in body


def test_invalid_window_falls_back_to_the_default(client, db, make_user, login):
    make_user(username="window", email="window@example.com")
    login(email="window@example.com")

    response = client.get("/billing/usage?days=junk")

    assert response.status_code == 200
    assert "Usage &amp; cost" in response.get_data(as_text=True)
