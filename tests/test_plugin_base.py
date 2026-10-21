"""Tests for the stable plugin SDK base class (#412)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

from app.services.plugin_base import PLUGIN_SDK_VERSION, PluginBase

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples" / "plugins"


def test_sdk_version_is_major_one():
    assert PLUGIN_SDK_VERSION.split(".")[0] == "1"
    assert PluginBase.plugin_sdk_version == PLUGIN_SDK_VERSION


def test_default_lifecycle_methods_are_safe_no_ops():
    plugin = PluginBase()

    assert plugin.register() is None
    assert plugin.handle_event(SimpleNamespace(event_type="x", data={})) is None
    assert plugin.on_enable() is None
    assert plugin.on_disable() is None
    assert plugin.on_uninstall() is None
    assert plugin.plugin_id is None


def test_plugin_id_comes_from_the_manifest():
    manifest = SimpleNamespace(id="hello-world")
    plugin = PluginBase(app=object(), manifest=manifest)

    assert plugin.plugin_id == "hello-world"
    assert plugin.app is not None
    assert plugin.manifest is manifest


def test_subclasses_can_override_only_the_hooks_they_need():
    calls = []

    class MinimalPlugin(PluginBase):
        def on_enable(self):
            calls.append("enabled")

        def handle_event(self, event):
            return {"event": event.event_type}

    plugin = MinimalPlugin()
    plugin.on_enable()
    plugin.on_disable()
    plugin.on_uninstall()

    assert calls == ["enabled"]
    assert plugin.handle_event(SimpleNamespace(event_type="project.created")) == {
        "event": "project.created"
    }


def test_example_plugin_builds_on_the_base_class(monkeypatch):
    monkeypatch.syspath_prepend(str(EXAMPLES_DIR))
    sys.modules.pop("hello_world.plugin", None)
    module = importlib.import_module("hello_world.plugin")

    plugin = module.HelloWorldPlugin()

    assert isinstance(plugin, PluginBase)
    assert plugin.plugin_sdk_version == PLUGIN_SDK_VERSION
