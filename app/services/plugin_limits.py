"""Configurable resource limits for plugin execution (#409).

Plugins are third-party code, so every operation that can consume unbounded
resources is bounded by a documented default. Operators may raise or lower the
bounds through environment-backed Flask settings:

===============================  =========================  ===============
Setting                          Default                    Applies to
===============================  =========================  ===============
``PLUGIN_EXECUTION_TIMEOUT``     ``5`` seconds              Event handlers / lifecycle hooks
``PLUGIN_CONFIG_MAX_BYTES``      ``65536``                  Stored per-workspace configuration
``PLUGIN_CONFIG_MAX_KEYS``       ``200``                    Stored per-workspace configuration
``PLUGIN_CONFIG_MAX_DEPTH``      ``8``                      Stored per-workspace configuration
``PLUGIN_ERROR_REPORT_LIMIT``    ``100`` (max ``500``)      Owner error-report reads
===============================  =========================  ===============

Invalid or non-positive values fall back to the conservative default, so a
mistyped environment variable can never silently remove a safety bound. A
non-positive execution timeout is the one deliberate exception: it disables the
callback bound for local debugging.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Fallback execution budget (seconds) when the setting is missing or invalid.
DEFAULT_EXECUTION_TIMEOUT = 5.0
#: Maximum serialized size of a stored plugin configuration object.
DEFAULT_CONFIG_MAX_BYTES = 64 * 1024
#: Maximum number of top-level keys in a stored plugin configuration.
DEFAULT_CONFIG_MAX_KEYS = 200
#: Maximum nesting depth of a stored plugin configuration.
DEFAULT_CONFIG_MAX_DEPTH = 8
#: Default number of error reports returned to a workspace owner.
DEFAULT_ERROR_REPORT_LIMIT = 100
#: Hard ceiling for owner error-report reads, regardless of configuration.
MAX_ERROR_REPORT_LIMIT = 500


@dataclass(frozen=True)
class PluginLimits:
    """Resolved plugin resource limits for the current application context."""

    execution_timeout: float
    config_max_bytes: int
    config_max_keys: int
    config_max_depth: int
    error_report_limit: int


def _setting(name: str, default: Any) -> Any:
    """Return ``name`` from the Flask config when an app context is active."""
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            return current_app.config.get(name, default)
    except Exception:  # pragma: no cover - Flask is always present in the app
        pass
    return default


def _positive_int(value: Any, default: int) -> int:
    """Coerce ``value`` to a positive integer, otherwise return ``default``."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _timeout(value: Any) -> float:
    """Coerce the execution budget; ``<= 0`` deliberately disables the bound."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return DEFAULT_EXECUTION_TIMEOUT


def get_plugin_limits() -> PluginLimits:
    """Return the effective plugin limits, falling back to safe defaults."""
    return PluginLimits(
        execution_timeout=_timeout(_setting("PLUGIN_EXECUTION_TIMEOUT", DEFAULT_EXECUTION_TIMEOUT)),
        config_max_bytes=_positive_int(
            _setting("PLUGIN_CONFIG_MAX_BYTES", DEFAULT_CONFIG_MAX_BYTES),
            DEFAULT_CONFIG_MAX_BYTES,
        ),
        config_max_keys=_positive_int(
            _setting("PLUGIN_CONFIG_MAX_KEYS", DEFAULT_CONFIG_MAX_KEYS),
            DEFAULT_CONFIG_MAX_KEYS,
        ),
        config_max_depth=_positive_int(
            _setting("PLUGIN_CONFIG_MAX_DEPTH", DEFAULT_CONFIG_MAX_DEPTH),
            DEFAULT_CONFIG_MAX_DEPTH,
        ),
        error_report_limit=min(
            _positive_int(
                _setting("PLUGIN_ERROR_REPORT_LIMIT", DEFAULT_ERROR_REPORT_LIMIT),
                DEFAULT_ERROR_REPORT_LIMIT,
            ),
            MAX_ERROR_REPORT_LIMIT,
        ),
    )


__all__ = [
    "DEFAULT_CONFIG_MAX_BYTES",
    "DEFAULT_CONFIG_MAX_DEPTH",
    "DEFAULT_CONFIG_MAX_KEYS",
    "DEFAULT_ERROR_REPORT_LIMIT",
    "DEFAULT_EXECUTION_TIMEOUT",
    "MAX_ERROR_REPORT_LIMIT",
    "PluginLimits",
    "get_plugin_limits",
]
