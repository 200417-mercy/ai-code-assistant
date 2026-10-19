"""Provider billing reconciliation blueprint (Phase 10).

Lets a user import the amount their LLM provider actually charged for a month
and compare it against the app's own token-based estimate.
"""

from flask import Blueprint

bp = Blueprint("billing", __name__, url_prefix="/billing")

from app.billing import routes  # noqa: E402, F401  (import routes to register them)
