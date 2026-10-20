"""Budget model, evaluation, and enforcement tests (#459, #469)."""

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Budget, User, Workspace
from app.services.budgets import BudgetError, BudgetExceededError, enforce_budget, evaluate_budget


def _user(email="budget@example.com"):
    user = User(username="budget-user", email=email)
    user.set_password("secret")
    db.session.add(user)
    db.session.commit()
    return user


def _workspace(user):
    workspace = Workspace(user_id=user.id, name="Budget WS", slug="budget-ws")
    db.session.add(workspace)
    db.session.commit()
    return workspace


class TestBudgetModel:
    def test_user_scope_defaults(self, app):
        with app.app_context():
            budget = Budget(user_id=_user().id)
            db.session.add(budget)
            db.session.commit()
            assert budget.is_user_scoped()
            assert not budget.is_workspace_scoped()
            assert budget.period == "monthly"
            assert budget.created_at is not None

    def test_workspace_scope_relationship(self, app):
        with app.app_context():
            user = _user()
            workspace = _workspace(user)
            budget = Budget(workspace_id=workspace.id, token_limit=1000, cost_limit=5.0)
            db.session.add(budget)
            db.session.commit()
            assert budget.is_workspace_scoped()
            assert budget.workspace == workspace
            assert budget in workspace.budgets

    def test_both_scopes_rejected(self, app):
        with app.app_context():
            user = _user()
            db.session.add(Budget(user_id=user.id, workspace_id=_workspace(user).id))
            with pytest.raises(IntegrityError):
                db.session.commit()
            db.session.rollback()


class TestBudgetEvaluation:
    def test_thresholds_and_remaining(self):
        budget = Budget(id=1, token_limit=100, cost_limit=10.0, period="monthly")
        assert evaluate_budget(budget)["status"] == "ok"
        warning = evaluate_budget(budget, token_spend=80, cost_spend=8.0)
        assert warning["status"] == "warning"
        assert warning["token_remaining"] == 20
        assert warning["cost_remaining"] == pytest.approx(2.0)
        exceeded = evaluate_budget(budget, token_spend=101)
        assert exceeded["status"] == "exceeded"
        assert exceeded["exceeded"] is True

    def test_unlimited_dimensions_are_ignored(self):
        budget = Budget(id=2, token_limit=None, cost_limit=None, period="monthly")
        result = evaluate_budget(budget, token_spend=10**9, cost_spend=10**9)
        assert result["status"] == "ok"
        assert result["token_remaining"] is None

    def test_overrides_are_independent(self):
        budget = Budget(id=3, token_limit=10, cost_limit=100.0, period="monthly")
        result = evaluate_budget(budget, token_spend=0, cost_spend=99.0)
        assert result["status"] == "warning"
        assert result["used_ratio"] == pytest.approx(0.99)

    @pytest.mark.parametrize("value", [-1, True, "10", None])
    def test_invalid_spend_rejected(self, value):
        budget = Budget(id=4, token_limit=10, period="monthly")
        with pytest.raises(BudgetError):
            evaluate_budget(budget, token_spend=value)

    def test_zero_limit_only_allows_zero_spend(self):
        budget = Budget(id=5, token_limit=0, period="monthly")
        assert evaluate_budget(budget, token_spend=0)["status"] == "ok"
        assert evaluate_budget(budget, token_spend=1)["status"] == "exceeded"

    def test_enforce_raises_when_exceeded(self):
        budget = Budget(id=6, token_limit=100, period="monthly")
        assert enforce_budget(budget, token_spend=100)["status"] == "warning"
        with pytest.raises(BudgetExceededError):
            enforce_budget(budget, token_spend=101)
