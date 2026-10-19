"""Entry point for the example ``artifact-hello`` plugin artifact.

The module name and class must match ``entry_point`` in ``manifest.json``
(``plugin:HelloArtifactPlugin``). Importing this module has no side effects.
"""

from __future__ import annotations


class HelloArtifactPlugin:
    """A minimal plugin used to demonstrate the artifact package format."""

    def __init__(self, app=None, manifest=None):
        self.app = app
        self.manifest = manifest

    def on_enable(self) -> None:
        """Lifecycle hook called when the plugin is enabled."""

    def on_disable(self) -> None:
        """Lifecycle hook called when the plugin is disabled."""
