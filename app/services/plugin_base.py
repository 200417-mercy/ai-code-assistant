"""Stable plugin SDK base class (Phase 9, issue #412).

Plugin authors may subclass :class:`PluginBase` instead of writing every
lifecycle method themselves. The class is intentionally small: it defines only
optional, no-op defaults and a versioned contract. The registry still discovers
handlers through the manifest's ``entry_point`` and invokes lifecycle hooks
defensively.

Versioning contract
-------------------
``PLUGIN_SDK_VERSION`` follows semantic versioning for the *author-facing*
surface (this class, the lifecycle hook names, and the documented constructor):

* additive methods/attributes may appear in minor releases;
* existing method names and their call signatures are stable within a major
  version;
* a breaking change bumps the major version and is called out in
  ``docs/plugin-developer-guide.md``.

The current major version is ``1``.
"""

from __future__ import annotations

from typing import Any

PLUGIN_SDK_VERSION = "1.0"


class PluginBase:
    """Optional base class for plugins.

    The loader instantiates plugins as ``PluginClass(app=..., manifest=...)``
    when the constructor accepts those keywords (falling back to
    ``PluginClass(manifest=...)``). Subclasses may inherit this constructor or
    define their own with compatible arguments.

    Every lifecycle method is a no-op by default, so a plugin only implements
    what it needs. Handlers must not raise: the registry isolates failures, but
    returning safe data is the documented contract.
    """

    #: Author-facing SDK version implemented by this class.
    plugin_sdk_version = PLUGIN_SDK_VERSION

    def __init__(self, app: Any = None, manifest: Any = None) -> None:
        self.app = app
        self.manifest = manifest

    @property
    def plugin_id(self) -> str | None:
        """Manifest id, when the plugin was constructed with a manifest."""
        return getattr(self.manifest, "id", None)

    def register(self) -> None:
        """Optional subscription hook, called from ``on_enable`` by convention."""

    def handle_event(self, event: Any) -> Any:
        """Optional generic event handler; return safe serializable data."""
        return None

    def on_enable(self) -> None:
        """Optional lifecycle hook invoked when the plugin is enabled."""

    def on_disable(self) -> None:
        """Optional lifecycle hook invoked when the plugin is disabled."""

    def on_uninstall(self) -> None:
        """Optional lifecycle hook invoked when the plugin is uninstalled."""


__all__ = ["PLUGIN_SDK_VERSION", "PluginBase"]
