"""Tests for #379, #383, #433 and #435.

- #379 marketplace listing pagination (stable pages, hard max size, metadata).
- #383 artifact download through the bounded plugin transport (guards + cap).
- #433 exactly one usage-ledger row per completed chat response (streamed,
  cancelled and single-completion fallback paths; no double counting).
- #435 usage retention purge (boundary safety + CLI).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from app.extensions import db
from app.models import Plugin, UsageRecord
from app.services.plugin_artifacts import download_artifact
from app.services.plugin_network import (
    PluginNetworkError,
    PluginNetworkPolicy,
)
from app.services.providers import (
    ProviderConfigurationError,
    register_provider,
    unregister_provider,
)
from app.services.providers.base import LLMProvider, ProviderResponse
from app.services.usage_ledger import purge_records

PUBLIC_IP = "93.184.216.34"
PUBLIC_RESOLVER = lambda host: [PUBLIC_IP]  # noqa: E731
ALLOW_ALL = PluginNetworkPolicy(allowlist=("*",))
ARTIFACT_URL = "https://plugins.example.com/hello-0.1.0.zip"


# --------------------------------------------------------------------------
# Fakes for the bounded transport
# --------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, headers=None, status_code=200, content=b"", is_redirect=False):
        self.headers = headers or {}
        self.status_code = status_code
        self.content = content
        self.is_redirect = is_redirect


class _FakeSession:
    def __init__(self, response=None):
        self.response = response or _FakeResponse(content=b"artifact-bytes")
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response


# --------------------------------------------------------------------------
# #379 Marketplace pagination
# --------------------------------------------------------------------------


def _plugin_row(plugin_id, workspace_id=1):
    row = Plugin(
        id=plugin_id,
        name=plugin_id,
        version="0.1.0",
        description="test",
        author="tester",
        entry_point=f"plugins.{plugin_id}:P",
        capabilities=["PROJECT_READ"],
    )
    db.session.add(row)
    db.session.commit()
    return row


def _workspace_for(user):
    from app.models import Workspace

    ws = Workspace(user_id=user.id, name="WS")
    db.session.add(ws)
    db.session.commit()
    return ws


class TestMarketplacePagination:
    def test_pagination_is_stable_across_pages(self, client, make_user, login):
        user = make_user()
        login()
        ws = _workspace_for(user)
        for index in range(5):
            _plugin_row(f"plugin-{index:02d}")

        first = client.get(f"/plugins/api/workspaces/{ws.id}/plugins?page=1&limit=2").get_json()
        second = client.get(f"/plugins/api/workspaces/{ws.id}/plugins?page=2&limit=2").get_json()

        assert [p["id"] for p in first["plugins"]] == ["plugin-00", "plugin-01"]
        assert [p["id"] for p in second["plugins"]] == ["plugin-02", "plugin-03"]
        assert first["pagination"]["total"] == 5
        assert first["pagination"]["pages"] == 3
        assert first["pagination"]["has_next"] is True
        assert first["pagination"]["has_prev"] is False
        assert second["pagination"]["has_prev"] is True
        assert first["ordering"] == ["id"]

    def test_oversized_limit_is_clamped(self, client, make_user, login):
        user = make_user()
        login()
        ws = _workspace_for(user)
        _plugin_row("only-plugin")

        payload = client.get(
            f"/plugins/api/workspaces/{ws.id}/plugins?page=1&limit=999999"
        ).get_json()

        assert payload["pagination"]["limit"] == payload["pagination"]["max_limit"]
        assert len(payload["plugins"]) == 1

    def test_invalid_page_defaults_to_1_and_limit_to_1(self, client, make_user, login):
        user = make_user()
        login()
        ws = _workspace_for(user)
        _plugin_row("a-plugin")
        _plugin_row("b-plugin")

        payload = client.get(f"/plugins/api/workspaces/{ws.id}/plugins?page=0&limit=0").get_json()

        assert payload["pagination"]["page"] == 1
        assert payload["pagination"]["limit"] == 1

    def test_page_beyond_end_is_empty_but_has_totals(self, client, make_user, login):
        user = make_user()
        login()
        ws = _workspace_for(user)
        _plugin_row("a-plugin")

        payload = client.get(f"/plugins/api/workspaces/{ws.id}/plugins?page=9&limit=10").get_json()

        assert payload["plugins"] == []
        assert payload["pagination"]["total"] == 1
        assert payload["pagination"]["has_next"] is False


# --------------------------------------------------------------------------
# #383 Artifact download through the bounded transport
# --------------------------------------------------------------------------


class TestArtifactDownload:
    def test_downloads_bytes_and_digest(self):
        session = _FakeSession(_FakeResponse(content=b"hello artifact"))

        result = download_artifact(
            ARTIFACT_URL, policy=ALLOW_ALL, session=session, resolver=PUBLIC_RESOLVER
        )

        assert result["content"] == b"hello artifact"
        assert result["size"] == 14
        assert result["sha256"] == hashlib.sha256(b"hello artifact").hexdigest()
        assert session.calls[0][2]["allow_redirects"] is False

    def test_refuses_non_https(self):
        session = _FakeSession()
        with pytest.raises(PluginNetworkError, match="https"):
            download_artifact(
                "http://plugins.example.com/p.zip",
                policy=ALLOW_ALL,
                session=session,
                resolver=PUBLIC_RESOLVER,
            )
        assert session.calls == []

    def test_refuses_host_not_on_allowlist(self):
        session = _FakeSession()
        with pytest.raises(PluginNetworkError, match="allowlist"):
            download_artifact(
                ARTIFACT_URL,
                policy=PluginNetworkPolicy(allowlist=("catalog.example.com",)),
                session=session,
                resolver=PUBLIC_RESOLVER,
            )
        assert session.calls == []

    def test_refuses_redirect(self):
        session = _FakeSession(
            _FakeResponse(
                {"Location": "https://evil.example.com/p.zip"},
                status_code=302,
                is_redirect=True,
            )
        )
        with pytest.raises(PluginNetworkError, match="redirect"):
            download_artifact(
                ARTIFACT_URL, policy=ALLOW_ALL, session=session, resolver=PUBLIC_RESOLVER
            )
        assert len(session.calls) == 1

    def test_refuses_declared_oversized_body(self):
        session = _FakeSession(_FakeResponse({"Content-Length": "999999"}, content=b"x"))
        with pytest.raises(PluginNetworkError, match="byte cap"):
            download_artifact(
                ARTIFACT_URL,
                policy=PluginNetworkPolicy(allowlist=("*",), max_bytes=10),
                session=session,
                resolver=PUBLIC_RESOLVER,
            )

    def test_refuses_undeclared_oversized_body(self):
        session = _FakeSession(_FakeResponse(content=b"x" * 20))
        with pytest.raises(PluginNetworkError, match="byte cap"):
            download_artifact(
                ARTIFACT_URL,
                policy=PluginNetworkPolicy(allowlist=("*",), max_bytes=10),
                session=session,
                resolver=PUBLIC_RESOLVER,
            )

    def test_writes_destination_atomically(self, tmp_path):
        session = _FakeSession(_FakeResponse(content=b"zip-bytes"))
        destination = tmp_path / "nested" / "plugin.zip"

        result = download_artifact(
            ARTIFACT_URL,
            policy=ALLOW_ALL,
            session=session,
            resolver=PUBLIC_RESOLVER,
            destination=destination,
        )

        assert destination.read_bytes() == b"zip-bytes"
        assert result["path"] == str(destination)
        assert "content" not in result


# --------------------------------------------------------------------------
# #435 Usage retention purge
# --------------------------------------------------------------------------


def _usage_row(*, created_at, source="chat", tokens=1, user_id=None):
    row = UsageRecord(
        source=source,
        prompt_tokens=tokens,
        completion_tokens=tokens,
        total_tokens=tokens * 2,
        created_at=created_at,
        user_id=user_id,
    )
    db.session.add(row)
    db.session.commit()
    return row


class TestUsagePurge:
    def test_purges_only_rows_older_than_window(self, db):
        now = datetime.now(UTC)
        old = _usage_row(created_at=now - timedelta(days=40))
        recent = _usage_row(created_at=now - timedelta(days=5))
        old_id, recent_id = old.id, recent.id

        result = purge_records(retention_days=30, now=now)

        assert result["deleted"] == 1
        db.session.expire_all()
        assert db.session.get(UsageRecord, old_id) is None
        assert db.session.get(UsageRecord, recent_id) is not None

    def test_boundary_row_is_retained(self, db):
        now = datetime.now(UTC)
        cutoff_row = _usage_row(created_at=now - timedelta(days=30))

        result = purge_records(retention_days=30, now=now)

        assert result["deleted"] == 0
        assert db.session.get(UsageRecord, cutoff_row.id) is not None

    def test_dry_run_deletes_nothing(self, db):
        now = datetime.now(UTC)
        old = _usage_row(created_at=now - timedelta(days=90))

        result = purge_records(retention_days=30, now=now, dry_run=True)

        assert result["deleted"] == 1
        assert result["dry_run"] is True
        assert db.session.get(UsageRecord, old.id) is not None

    def test_invalid_retention_rejected(self, db):
        with pytest.raises(ValueError):
            purge_records(retention_days=0)
        with pytest.raises(ValueError):
            purge_records(retention_days=-5)

    def test_cli_purge_json(self, app, db):
        now = datetime.now(UTC)
        _usage_row(created_at=now - timedelta(days=90))
        _usage_row(created_at=now - timedelta(days=1))

        result = app.test_cli_runner().invoke(args=["usage", "purge", "--days", "30", "--json"])

        assert result.exit_code == 0
        import json

        payload = json.loads(result.output)
        assert payload["retention_days"] == 30
        assert payload["deleted"] == 1
        assert UsageRecord.query.count() == 1


# --------------------------------------------------------------------------
# #433 Ledger correctness for streamed / cancelled / fallback replies
# --------------------------------------------------------------------------


class _StreamingProvider(LLMProvider):
    name = "ledger-stream"
    models = ("ledger-stream-1",)

    def chat(self, messages, *, model=None, params=None):
        return ProviderResponse(content="complete reply", model=self.models[0])

    def stream(self, messages, *, model=None, params=None):
        yield from ("hello ", "there")


class _EmptyStreamProvider(LLMProvider):
    """Yields no tokens, forcing the single-completion fallback."""

    name = "ledger-empty"
    models = ("ledger-empty-1",)

    def chat(self, messages, *, model=None, params=None):
        return ProviderResponse(content="fallback reply", model=self.models[0])

    def stream(self, messages, *, model=None, params=None):
        return iter(())


class _FailingProvider(LLMProvider):
    name = "ledger-fail"
    models = ("ledger-fail-1",)

    def chat(self, messages, *, model=None, params=None):
        raise ProviderConfigurationError("boom", provider=self.name)

    def stream(self, messages, *, model=None, params=None):
        yield "half "
        raise ProviderConfigurationError("boom", provider=self.name)


@pytest.fixture()
def streaming_provider(monkeypatch):
    register_provider("ledger-stream", _StreamingProvider)
    monkeypatch.setenv("LLM_PROVIDER", "ledger-stream")
    yield
    unregister_provider("ledger-stream")


@pytest.fixture()
def empty_stream_provider(monkeypatch):
    register_provider("ledger-empty", _EmptyStreamProvider)
    monkeypatch.setenv("LLM_PROVIDER", "ledger-empty")
    yield
    unregister_provider("ledger-empty")


@pytest.fixture()
def failing_provider(monkeypatch):
    register_provider("ledger-fail", _FailingProvider)
    monkeypatch.setenv("LLM_PROVIDER", "ledger-fail")
    yield
    unregister_provider("ledger-fail")


def _register(client):
    client.post(
        "/auth/register",
        data={
            "username": "ledgeruser",
            "email": "ledgeruser@example.com",
            "password": "supersecret123",
            "password_confirm": "supersecret123",
        },
    )


def _create_conversation(client):
    response = client.post("/chat/conversations", json={})
    assert response.status_code == 201
    return response.get_json()


def _chat_rows():
    return UsageRecord.query.filter_by(source="chat").count()


class TestChatLedgerCorrectness:
    def test_non_streaming_records_one_row(self, client, db):
        _register(client)
        conversation = _create_conversation(client)

        response = client.post(
            f"/chat/conversations/{conversation['id']}/messages",
            json={"content": "hello"},
        )

        assert response.status_code == 201
        assert _chat_rows() == 1
        row = UsageRecord.query.filter_by(source="chat").one()
        assert row.conversation_id == conversation["id"]
        assert row.total_tokens > 0

    def test_streamed_completion_records_one_row(self, client, db, streaming_provider):
        _register(client)
        conversation = _create_conversation(client)

        response = client.post(
            f"/chat/conversations/{conversation['id']}/stream",
            json={"content": "hello"},
        )
        response.get_data()

        assert _chat_rows() == 1

    def test_cancelled_stream_records_one_row(self, client, db, streaming_provider):
        _register(client)
        conversation = _create_conversation(client)

        response = client.post(
            f"/chat/conversations/{conversation['id']}/stream",
            json={"content": "hello"},
            buffered=False,
        )
        iterator = response.response
        next(iterator)
        iterator.close()

        db.session.expire_all()
        assert _chat_rows() == 1

    def test_fallback_records_exactly_one_row(self, client, db, empty_stream_provider):
        _register(client)
        conversation = _create_conversation(client)

        response = client.post(
            f"/chat/conversations/{conversation['id']}/stream",
            json={"content": "hello"},
        )
        response.get_data()

        # The empty stream produced no message; only the fallback completion
        # was counted - never both.
        assert _chat_rows() == 1

    def test_provider_failure_mid_stream_records_one_row(self, client, db, failing_provider):
        _register(client)
        conversation = _create_conversation(client)

        response = client.post(
            f"/chat/conversations/{conversation['id']}/stream",
            json={"content": "hello"},
        )
        response.get_data()

        assert _chat_rows() == 1
