"""The /metrics endpoint."""

from __future__ import annotations

from flask import Blueprint, Response, current_app

from .metrics import CONTENT_TYPE, render_metrics

bp = Blueprint("observability", __name__)


@bp.get("/metrics")
def metrics() -> Response:
    if not current_app.config.get("METRICS_ENABLED", True):
        return Response("not found\n", status=404, mimetype="text/plain")
    response = Response(render_metrics(), content_type=CONTENT_TYPE)
    response.headers["Cache-Control"] = "no-store"
    return response