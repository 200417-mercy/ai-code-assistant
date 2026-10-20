"""Provider billing reconciliation routes (Phase 10).

Every endpoint is scoped to the signed-in user: their provider-reported imports
are reconciled against the estimate derived from *their* token usage. Imports
only ever write :class:`~app.models.provider_billing_record.ProviderBillingRecord`
rows, so the estimate is never overwritten.
"""

from __future__ import annotations

from flask import Response, abort, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.billing import bp
from app.extensions import db
from app.models import Workspace
from app.models.budget import BUDGET_PERIODS
from app.services import audit, billing_reconciliation, usage_dashboards
from app.services import budgets as budget_service
from app.services.billing_reconciliation import BillingImportError
from app.services.budget_access import can_access_workspace_billing


def _wants_json() -> bool:
    if request.args.get("format") == "json":
        return True
    return request.accept_mimetypes.best == "application/json"


def _extract_csv() -> tuple[str | None, str | None]:
    """Return ``(text, filename)`` from the request, or ``(None, error)``."""
    uploaded = request.files.get("file")
    if uploaded is not None and uploaded.filename:
        try:
            raw = uploaded.read()
        except OSError as exc:
            return None, f"Could not read the uploaded file: {exc}"
        try:
            return raw.decode("utf-8"), uploaded.filename
        except UnicodeDecodeError:
            return None, "The CSV must be UTF-8 encoded text."

    text = request.form.get("csv")
    if text is None:
        payload = request.get_json(silent=True)
        if isinstance(payload, dict):
            text = payload.get("csv")
    if isinstance(text, str) and text.strip():
        return text, None
    return None, "No CSV was provided."


@bp.route("/")
@login_required
def index():
    """Render the reconciliation page for the signed-in user."""
    report = billing_reconciliation.reconciliation_report(user_id=current_user.id)
    return render_template("billing/index.html", report=report)


@bp.route("/import", methods=["POST"])
@login_required
def import_billing():
    """Import a provider billing CSV and reconcile it against estimates.

    Accepts a ``multipart/form-data`` upload in the ``file`` field, a ``csv``
    form field, or a ``{"csv": "..."}`` JSON body. The estimate is recomputed
    from token usage and never overwritten by the import.
    """
    text, filename = _extract_csv()
    if text is None:
        if _wants_json():
            return jsonify({"error": filename, "kind": "invalid"}), 400
        return redirect(url_for("billing.index"))

    try:
        result = billing_reconciliation.import_billing_csv(
            text, user_id=current_user.id, source_filename=filename
        )
    except BillingImportError as exc:
        if _wants_json():
            return jsonify({"error": str(exc), "kind": exc.kind}), 400
        return redirect(url_for("billing.index"))

    audit.record(
        audit.PROVIDER_BILLING_IMPORTED,
        user=current_user,
        target_type="provider_billing",
        metadata={
            "created": result["created"],
            "updated": result["updated"],
            "rows": result["rows"],
            "source_filename": filename,
        },
    )
    db.session.commit()

    if _wants_json():
        return jsonify(result)
    return redirect(url_for("billing.index"))


@bp.route("/api/reconciliation")
@login_required
def api_reconciliation():
    """Return the signed-in user's reconciliation report as JSON."""
    period = request.args.get("period")
    try:
        report = billing_reconciliation.reconciliation_report(
            user_id=current_user.id, period=period or None
        )
    except BillingImportError as exc:
        return jsonify({"error": str(exc), "kind": exc.kind}), 400
    return jsonify(report)


# ---------------------------------------------------------------------------
# Budget CRUD API (#461) with owner/admin authorization (#468)
# ---------------------------------------------------------------------------


def _budget_not_found() -> tuple:
    """Uniform refusal for missing and inaccessible budgets (no oracle)."""
    return jsonify({"error": "Budget not found.", "kind": "not_found"}), 404


def _budget_invalid(message: str) -> tuple:
    return jsonify({"error": message, "kind": "invalid"}), 400


@bp.route("/api/budgets", methods=["GET"])
@login_required
def api_list_budgets():
    """List budgets the signed-in user may access."""
    items = budget_service.list_budgets(current_user)
    return jsonify(
        {
            "budgets": [budget.to_dict() for budget in items],
            "periods": list(BUDGET_PERIODS),
        }
    )


@bp.route("/api/budgets", methods=["POST"])
@login_required
def api_create_budget():
    """Create a user budget or, for owners/admins, a workspace budget.

    Body: ``name`` (optional), ``limit_amount`` (required, positive),
    ``period`` (required), optional ``currency`` (default ``USD``) and optional
    ``workspace_id`` to create a workspace-scoped budget.
    """
    try:
        budget = budget_service.create_budget(current_user, request.get_json(silent=True) or {})
    except budget_service.BudgetNotFoundError:
        return _budget_not_found()
    except budget_service.BudgetValidationError as exc:
        db.session.rollback()
        return _budget_invalid(str(exc))
    return jsonify(budget.to_dict()), 201


@bp.route("/api/budgets/<int:budget_id>", methods=["GET"])
@login_required
def api_get_budget(budget_id: int):
    """Return one accessible budget."""
    try:
        budget = budget_service.get_budget(budget_id, current_user)
    except budget_service.BudgetNotFoundError:
        return _budget_not_found()
    return jsonify(budget.to_dict())


@bp.route("/api/budgets/<int:budget_id>", methods=["PUT", "PATCH"])
@login_required
def api_update_budget(budget_id: int):
    """Update an accessible budget (scope and owner are immutable)."""
    try:
        budget = budget_service.update_budget(
            budget_id, current_user, request.get_json(silent=True) or {}
        )
    except budget_service.BudgetNotFoundError:
        return _budget_not_found()
    except budget_service.BudgetValidationError as exc:
        db.session.rollback()
        return _budget_invalid(str(exc))
    return jsonify(budget.to_dict())


@bp.route("/api/budgets/<int:budget_id>", methods=["DELETE"])
@login_required
def api_delete_budget(budget_id: int):
    """Delete an accessible budget."""
    try:
        budget_service.delete_budget(budget_id, current_user)
    except budget_service.BudgetNotFoundError:
        return _budget_not_found()
    return "", 204


# ---------------------------------------------------------------------------
# Usage and cost dashboards (#368)
# ---------------------------------------------------------------------------


def _parse_days():
    """Parse ``days`` and return ``(days, error_response)``."""
    try:
        return usage_dashboards.normalize_days(request.args.get("days")), None
    except ValueError as exc:
        return None, (jsonify({"error": str(exc), "kind": "invalid"}), 400)


def _dashboard_refusal():
    return jsonify({"error": "Usage dashboard not found.", "kind": "not_found"}), 404


def _csv_response(text: str, truncated: bool, filename: str) -> Response:
    response = Response(text, mimetype="text/csv")
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.headers["Cache-Control"] = "no-store"
    if truncated:
        response.headers["X-Export-Truncated"] = "true"
    return response


@bp.route("/usage")
@login_required
def usage_page():
    """Render the signed-in user's dashboard (optionally a workspace view)."""
    days, _error = _parse_days()
    if days is None:
        days = usage_dashboards.DEFAULT_WINDOW_DAYS
    workspace_id = request.args.get("workspace_id", type=int)
    if workspace_id is not None and not can_access_workspace_billing(workspace_id, current_user):
        abort(404)
    payload = usage_dashboards.dashboard(
        user_id=current_user.id, workspace_id=workspace_id, days=days
    )
    workspaces = (
        Workspace.query.filter_by(user_id=current_user.id)
        .order_by(Workspace.name, Workspace.id)
        .all()
    )
    return render_template(
        "billing/usage.html",
        dashboard=payload,
        days=days,
        workspace_id=workspace_id,
        workspaces=workspaces,
    )


@bp.route("/api/usage/dashboard")
@login_required
def api_usage_dashboard():
    """Return the signed-in user's usage/cost dashboard as JSON."""
    days, error = _parse_days()
    if error is not None:
        return error
    return jsonify(usage_dashboards.dashboard(user_id=current_user.id, days=days))


@bp.route("/api/usage/dashboard.csv")
@login_required
def api_usage_dashboard_csv():
    """Download the signed-in user's usage ledger as bounded CSV."""
    days, error = _parse_days()
    if error is not None:
        return error
    text, truncated = usage_dashboards.export_csv(user_id=current_user.id, days=days)
    return _csv_response(text, truncated, "usage.csv")


@bp.route("/api/workspaces/<int:workspace_id>/usage")
@login_required
def api_workspace_usage(workspace_id: int):
    """Return a workspace dashboard for its owner or a site administrator."""
    if not can_access_workspace_billing(workspace_id, current_user):
        return _dashboard_refusal()
    days, error = _parse_days()
    if error is not None:
        return error
    return jsonify(usage_dashboards.dashboard(workspace_id=workspace_id, days=days))


@bp.route("/api/workspaces/<int:workspace_id>/usage.csv")
@login_required
def api_workspace_usage_csv(workspace_id: int):
    """Download a workspace ledger as bounded CSV (owner/admin only)."""
    if not can_access_workspace_billing(workspace_id, current_user):
        return _dashboard_refusal()
    days, error = _parse_days()
    if error is not None:
        return error
    text, truncated = usage_dashboards.export_csv(workspace_id=workspace_id, days=days)
    return _csv_response(text, truncated, f"workspace-{workspace_id}-usage.csv")
