"""Tests for configurable plugin resource limits (#409).

Every bound has a conservative default, readings honour non-default app
configuration, and malformed/non-positive values fall back to the default
instead of silently disabling the protection.
"""

from app.extensions import db
from app.models import Workspace
from app.services.plugin_config import validate_plugin_config
from app.services.plugin_errors import list_workspace_error_reports, record_plugin_error
from app.services.plugin_limits import (
    DEFAULT_CONFIG_MAX_BYTES,
    DEFAULT_CONFIG_MAX_DEPTH,
    DEFAULT_CONFIG_MAX_KEYS,
    DEFAULT_ERROR_REPORT_LIMIT,
    DEFAULT_EXECUTION_TIMEOUT,
    MAX_ERROR_REPORT_LIMIT,
    get_plugin_limits,
)

LIMIT_SETTINGS = (
    "PLUGIN_EXECUTION_TIMEOUT",
    "PLUGIN_CONFIG_MAX_BYTES",
    "PLUGIN_CONFIG_MAX_KEYS",
    "PLUGIN_CONFIG_MAX_DEPTH",
    "PLUGIN_ERROR_REPORT_LIMIT",
)


class TestResolution:
    def test_defaults_when_settings_are_missing(self, app):
        for name in LIMIT_SETTINGS:
            app.config.pop(name, None)

        limits = get_plugin_limits()

        assert limits.execution_timeout == DEFAULT_EXECUTION_TIMEOUT
        assert limits.config_max_bytes == DEFAULT_CONFIG_MAX_BYTES
        assert limits.config_max_keys == DEFAULT_CONFIG_MAX_KEYS
        assert limits.config_max_depth == DEFAULT_CONFIG_MAX_DEPTH
        assert limits.error_report_limit == DEFAULT_ERROR_REPORT_LIMIT

    def test_reads_non_default_values(self, app):
        app.config.update(
            PLUGIN_EXECUTION_TIMEOUT=0.25,
            PLUGIN_CONFIG_MAX_BYTES=1024,
            PLUGIN_CONFIG_MAX_KEYS=12,
            PLUGIN_CONFIG_MAX_DEPTH=3,
            PLUGIN_ERROR_REPORT_LIMIT=7,
        )

        limits = get_plugin_limits()

        assert limits.execution_timeout == 0.25
        assert limits.config_max_bytes == 1024
        assert limits.config_max_keys == 12
        assert limits.config_max_depth == 3
        assert limits.error_report_limit == 7

    def test_invalid_values_fall_back_to_defaults(self, app):
        app.config.update(
            PLUGIN_CONFIG_MAX_BYTES="not-a-number",
            PLUGIN_CONFIG_MAX_KEYS=0,
            PLUGIN_CONFIG_MAX_DEPTH=-4,
            PLUGIN_ERROR_REPORT_LIMIT=None,
        )

        limits = get_plugin_limits()

        assert limits.config_max_bytes == DEFAULT_CONFIG_MAX_BYTES
        assert limits.config_max_keys == DEFAULT_CONFIG_MAX_KEYS
        assert limits.config_max_depth == DEFAULT_CONFIG_MAX_DEPTH
        assert limits.error_report_limit == DEFAULT_ERROR_REPORT_LIMIT

    def test_error_report_limit_has_a_hard_ceiling(self, app):
        app.config["PLUGIN_ERROR_REPORT_LIMIT"] = MAX_ERROR_REPORT_LIMIT + 10_000

        assert get_plugin_limits().error_report_limit == MAX_ERROR_REPORT_LIMIT


class TestConfigurationValidation:
    def test_non_default_key_limit_is_enforced(self, app):
        app.config["PLUGIN_CONFIG_MAX_KEYS"] = 1

        ok, reason = validate_plugin_config(None, {"a": 1, "b": 2})

        assert ok is False
        assert "max 1" in reason

    def test_non_default_depth_limit_is_enforced(self, app):
        app.config["PLUGIN_CONFIG_MAX_DEPTH"] = 1

        ok, reason = validate_plugin_config(None, {"a": {"b": {"c": 1}}})

        assert ok is False
        assert "max 1" in reason

    def test_non_default_byte_limit_is_enforced(self, app):
        app.config["PLUGIN_CONFIG_MAX_BYTES"] = 16

        ok, reason = validate_plugin_config(None, {"key": "x" * 64})

        assert ok is False
        assert "max 16 bytes" in reason

    def test_explicit_bounds_override_the_configured_values(self, app):
        app.config["PLUGIN_CONFIG_MAX_KEYS"] = 100

        ok, reason = validate_plugin_config(None, {"a": 1, "b": 2}, max_keys=1)

        assert ok is False
        assert "max 1" in reason


class TestErrorReportLimit:
    def _workspace(self, owner):
        workspace = Workspace(user_id=owner.id, name="Limits")
        db.session.add(workspace)
        db.session.commit()
        return workspace

    def test_default_and_non_default_read_limits(self, app, make_user):
        owner = make_user()
        workspace = self._workspace(owner)
        for index in range(3):
            record_plugin_error(
                f"plugin-{index}",
                "dispatch:project.created",
                message="boom",
                workspace_id=workspace.id,
            )

        app.config["PLUGIN_ERROR_REPORT_LIMIT"] = 2
        assert len(list_workspace_error_reports(workspace.id)) == 2

        app.config["PLUGIN_ERROR_REPORT_LIMIT"] = 1
        assert len(list_workspace_error_reports(workspace.id)) == 1

        assert len(list_workspace_error_reports(workspace.id, limit=3)) == 3
