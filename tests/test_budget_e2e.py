"""End-to-end usage and budget enforcement tests (issue #489).

The path under test is:

    model call -> usage ledger -> dashboard aggregation -> budget warning
                                                -> hard refusal

Everything runs offline against the deterministic mock provider.
"""

from app.extensions import db
from app.models import Conversation, Message, UsageRecord, Workspace
from app.services import budgets, pricing


def _workspace_conversation(user):
    workspace = Workspace(user_id=user.id, name="E2E")
    db.session.add(workspace)
    db.session.commit()
    conversation = Conversation(user_id=user.id, title="E2E", workspace_id=workspace.id)
    db.session.add(conversation)
    db.session.commit()
    return workspace, conversation


def _send(client, conversation_id, content="hello budget"):
    return client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": content},
    )


def test_model_call_is_recorded_aggregated_and_visible_on_dashboard(client, make_user, login):
    user = make_user()
    conversation = Conversation(user_id=user.id, title="E2E")
    db.session.add(conversation)
    db.session.commit()
    login()

    response = _send(client, conversation.id)
    assert response.status_code == 201, response.get_json()

    records = UsageRecord.query.filter_by(user_id=user.id).all()
    assert len(records) == 1
    assert records[0].source == "chat"
    assert records[0].total_tokens > 0
    assert records[0].status == "success"

    breakdown = client.get("/api/usage/breakdown").get_json()
    assert breakdown["totals"]["total_tokens"] == records[0].total_tokens
    assert breakdown["by_source"]["chat"]["total_tokens"] == records[0].total_tokens

    budget = budgets.upsert_budget(
        scope="user", scope_id=user.id, token_limit=records[0].total_tokens * 2
    )
    listing = client.get("/billing/api/budgets").get_json()
    row = next(item for item in listing["budgets"] if item["id"] == budget.id)
    assert row["tokens_used"] == records[0].total_tokens
    assert row["token_limit"] == records[0].total_tokens * 2
    assert row["allowed"] is True
    assert row["warned"] is False


def test_warning_then_hard_refusal_stops_the_model_call(client, make_user, login):
    user = make_user()
    conversation = Conversation(user_id=user.id, title="E2E warn")
    db.session.add(conversation)
    db.session.commit()
    login()

    # First call establishes deterministic usage with no budget configured.
    assert _send(client, conversation.id).status_code == 201
    first = UsageRecord.query.filter_by(user_id=user.id).one()

    # A budget at 10x current usage with a 0.1 threshold starts in the warning
    # band but still allows the call.
    budget = budgets.upsert_budget(
        scope="user",
        scope_id=user.id,
        token_limit=first.total_tokens * 10,
        warn_threshold=0.1,
    )
    warned = _send(client, conversation.id)
    assert warned.status_code == 201
    assert warned.headers.get("X-Budget-Warning") == "approaching_limit"
    assert UsageRecord.query.filter_by(user_id=user.id).count() == 2
    status = budgets.evaluate_budget(budget)
    assert status.warned is True
    assert status.allowed is True

    # Tighten the limit to the spend already recorded: the next call must be
    # refused before a provider call or ledger row.
    spend = budgets.evaluate_budget(budget).tokens_used
    budgets.upsert_budget(
        scope="user",
        scope_id=user.id,
        token_limit=spend,
        warn_threshold=0.5,
    )
    message_count = Message.query.count()
    ledger_count = UsageRecord.query.count()

    refused = _send(client, conversation.id)
    assert refused.status_code == 429
    body = refused.get_json()
    assert body["kind"] == "budget_exceeded"
    assert body["limits_hit"] == ["tokens"]
    assert refused.headers["Retry-After"]
    assert Message.query.count() == message_count
    assert UsageRecord.query.count() == ledger_count


def test_cost_budget_is_enforced_from_real_model_call_costs(client, make_user, login):
    user = make_user()
    workspace, conversation = _workspace_conversation(user)
    pricing.set_workspace_price_override(
        workspace=workspace,
        provider="mock",
        model="mock-1",
        input_per_1k_usd=1.0,
        output_per_1k_usd=1.0,
        user=user,
    )
    login()

    budget = budgets.upsert_budget(scope="workspace", scope_id=workspace.id, cost_limit=1.0)
    assert _send(client, conversation.id).status_code == 201

    status = budgets.evaluate_budget(budget)
    assert status.cost_used > 0
    assert status.allowed

    # The workspace's cost limit now equals what was actually spent.
    budgets.upsert_budget(scope="workspace", scope_id=workspace.id, cost_limit=status.cost_used)
    ledger_count = UsageRecord.query.count()
    refused = _send(client, conversation.id)
    assert refused.status_code == 429
    assert refused.get_json()["limits_hit"] == ["cost"]
    assert UsageRecord.query.count() == ledger_count


def test_both_quota_types_surface_in_one_refusal(client, make_user, login):
    user = make_user()
    conversation = Conversation(user_id=user.id, title="E2E both")
    db.session.add(conversation)
    db.session.commit()
    login()

    assert _send(client, conversation.id).status_code == 201
    record = UsageRecord.query.filter_by(user_id=user.id).one()
    budgets.upsert_budget(
        scope="user",
        scope_id=user.id,
        token_limit=record.total_tokens,
        # A zero cost limit blocks immediately, so both quota types are hit.
        cost_limit=0,
    )

    refused = _send(client, conversation.id)
    assert refused.status_code == 429
    assert set(refused.get_json()["limits_hit"]) == {"tokens", "cost"}
