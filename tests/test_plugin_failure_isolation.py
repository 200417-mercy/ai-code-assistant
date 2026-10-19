"""Regression tests for plugin failure isolation (#410).

A failure at any plugin invocation boundary - loading the entry point,
instantiating the plugin class, running a lifecycle hook, or dispatching an
event handler - must be contained. Requests complete, other handlers still
run, the registry stays consistent, and one bounded error report is recorded.
"""

import types

import pytest

from app.extensions import db
from app.models import Plugin as PluginRow
from app.models import PluginErrorReport
from app.services.events import emit_event, get_dispatcher
from app.services.plugins import (
    Plugin,
    PluginLoadError,
    PluginManifest,
    PluginRegistry,
)


def _manifest(plugin_id, entry_point="plugins.test:TestPlugin"):
    return PluginManifest(
        id=plugin_id,
        name="Test plugin",
        version="0.1.0",
        description="test",
        author="tester",
        entry_point=entry_point,
        capabilities=["PROJECT_READ"],
    )


def _reports(plugin_id):
    return (
        PluginErrorReport.query.filter_by(plugin_id=plugin_id)
        .order_by(PluginErrorReport.id.asc())
        .all()
    )


class TestLoaderFailures:
    def test_import_failure_is_isolated_and_recorded(self, app):
        plugin = Plugin(_manifest("broken-loader", "not_a_real_plugin_module:Plugin"))
        registry = PluginRegistry()
        registry.register(plugin)

        with pytest.raises(PluginLoadError):
            registry.load_plugin("broken-loader")

        # The failed load leaves the registry and the plugin object untouched.
        assert registry.get("broken-loader") is plugin
        assert plugin.module is None
        assert len(registry) == 1

        reports = _reports("broken-loader")
        assert len(reports) == 1
        assert reports[0].operation == "load"
        assert reports[0].exception_type.endswith("PluginLoadError")

    def test_missing_entry_point_class_is_isolated_and_recorded(self, app):
        plugin = Plugin(_manifest("missing-class", "types:NotAPluginClass"))
        registry = PluginRegistry()
        registry.register(plugin)

        with pytest.raises(PluginLoadError):
            registry.load_plugin("missing-class")

        assert plugin.module is None
        assert registry.get("missing-class") is plugin
        reports = _reports("missing-class")
        assert [report.operation for report in reports] == ["load"]

    def test_failed_reload_does_not_replace_a_loaded_module(self, app):
        plugin = Plugin(_manifest("reload-failure", "not_a_real_plugin_module:Plugin"))
        existing = types.ModuleType("existing_plugin_module")
        plugin.module = existing
        registry = PluginRegistry()
        registry.register(plugin)

        with pytest.raises(PluginLoadError):
            plugin.load(registry=registry)

        assert plugin.module is existing


class TestHookAndInstantiationFailures:
    def test_instantiation_failure_is_contained_and_recorded(self, app):
        class BrokenInit:
            def __init__(self, app=None, manifest=None):
                raise RuntimeError("cannot construct")

        module = types.ModuleType("broken_init_mod")
        module.BrokenInit = BrokenInit
        plugin = Plugin(_manifest("broken-init", "broken_init_mod:BrokenInit"))
        plugin.module = module
        registry = PluginRegistry()
        registry.register(plugin)

        result = plugin.invoke_hook("on_enable")

        assert result["ok"] is False
        assert result["called"] is False
        reports = _reports("broken-init")
        assert len(reports) == 1
        assert reports[0].operation == "hook:on_enable"
        assert reports[0].exception_type.endswith("RuntimeError")

    def test_hook_failure_keeps_the_registry_consistent(self, app):
        class FailingHook:
            def __init__(self, app=None, manifest=None):
                pass

            def on_disable(self):
                raise RuntimeError("disable boom")

        module = types.ModuleType("failing_hook_mod")
        module.FailingHook = FailingHook
        plugin = Plugin(_manifest("failing-hook", "failing_hook_mod:FailingHook"))
        plugin.module = module
        registry = PluginRegistry()
        registry.register(plugin)

        registry.disable("failing-hook")  # must not raise

        assert plugin.enabled is False
        assert registry.get("failing-hook") is plugin
        reports = _reports("failing-hook")
        assert len(reports) == 1
        assert reports[0].operation == "hook:on_disable"


class TestDispatchFailures:
    def test_registry_dispatch_isolates_handlers_and_records(self, app):
        registry = PluginRegistry()
        calls = []

        def good_handler(event_type, data):
            calls.append(event_type)

        def bad_handler(event_type, data):
            raise RuntimeError("dispatch boom")

        registry.subscribe("bad-plugin", "project.created", bad_handler)
        registry.subscribe("good-plugin", "project.created", good_handler)

        registry.dispatch("project.created", {"project_id": 1})  # must not raise

        assert calls == ["project.created"]
        reports = _reports("bad-plugin")
        assert len(reports) == 1
        assert reports[0].operation == "registry-dispatch:project.created"

    def test_emit_event_never_raises_for_a_failing_plugin_handler(self, app):
        db.session.add(
            PluginRow(
                id="event-failer",
                name="Event failer",
                version="0.1.0",
                entry_point="plugins.test:TestPlugin",
                capabilities=["GITHUB_READ"],
                enabled=True,
            )
        )
        db.session.commit()

        dispatcher = get_dispatcher()

        def bad_handler(event):
            raise RuntimeError("handler boom")

        dispatcher.subscribe("github.connected", bad_handler, plugin_id="event-failer")
        try:
            result = emit_event("github.connected", data={})
        finally:
            dispatcher.unsubscribe("github.connected", bad_handler)

        assert result["failed"] == 1
        reports = _reports("event-failer")
        assert len(reports) == 1
        assert reports[0].operation == "dispatch:github.connected"
