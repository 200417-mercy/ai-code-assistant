"""Hello Webhook example plugin.

This example demonstrates a plugin that:
1. Subscribes to the `project.created` event (requires PROJECT_READ capability)
2. Exercises the NOTIFICATION_CREATE capability by creating a notification
3. Uses the public plugin APIs for event subscription and capability usage

Run it from the repository root. The repository root must be importable so
``app`` resolves, and this directory must be importable so the manifest's
``entry_point`` (``hello_webhook.plugin:HelloWebhookPlugin``) resolves::

    # Windows (PowerShell)
    $env:PYTHONPATH = ".;examples/plugins/hello_webhook"
    python examples/plugins/hello_webhook/plugin.py

    # Linux / macOS
    PYTHONPATH=.:examples/plugins/hello_webhook python examples/plugins/hello_webhook/plugin.py
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.services.events import Event, get_dispatcher

logger = logging.getLogger("hello_webhook")


class HelloWebhookPlugin:
    """A minimal plugin that demonstrates webhook-style event handling."""

    def __init__(self, app=None, manifest=None):
        self.app = app
        self.manifest = manifest
        self.received: list[Event] = []

    @property
    def plugin_id(self) -> str:
        """Manifest id used as the subscription owner."""
        return self.manifest.id if self.manifest is not None else "hello-webhook"

    def subscribe(self) -> None:
        """Subscribe to project.created event."""
        get_dispatcher().subscribe("project.created", self.on_event, plugin_id=self.plugin_id)
        logger.info("%s subscribed to project.created", self.plugin_id)

    def unsubscribe(self) -> None:
        """Remove this plugin's project.created subscription."""
        get_dispatcher().unsubscribe("project.created", self.on_event)

    def on_event(self, event: Event) -> None:
        """Record and log a received event, then exercise NOTIFICATION_CREATE capability."""
        self.received.append(event)
        logger.info("%s received %s: %s", self.plugin_id, event.event_type, event.data)

        # Demonstrate exercising a capability - create a notification
        # This simulates what a webhook plugin would do when receiving an event
        if event.data and "project_id" in event.data:
            self._create_webhook_notification(event.workspace_id, event.data["project_id"])

    def _create_webhook_notification(self, workspace_id: int, project_id: int) -> None:
        """Simulate creating a notification using the NOTIFICATION_CREATE capability."""
        # In a real plugin, you would check if you have the capability grant
        # and then use the appropriate service to create a notification
        logger.info(
            "%s would create notification for project %d in workspace %d "
            "(requires NOTIFICATION_CREATE capability)",
            self.plugin_id,
            project_id,
            workspace_id,
        )

    # Optional lifecycle hooks, invoked by PluginRegistry.enable/disable.
    def on_enable(self) -> None:
        self.subscribe()

    def on_disable(self) -> None:
        self.unsubscribe()


def main() -> None:
    """Load the example from its manifest and exercise the registry flow."""
    from app.services.plugins import Plugin, PluginManifest, PluginRegistry

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    manifest = PluginManifest.from_file(Path(__file__).with_name("manifest.json"))
    registry = PluginRegistry()
    plugin = Plugin(manifest)
    plugin.load()  # imports ``hello_webhook.plugin:HelloWebhookPlugin`` from this example directory
    registry.register(plugin)

    instance: HelloWebhookPlugin = plugin.get_instance()
    instance.subscribe()
    try:
        # A host application would instead dispatch real ``project.created``
        # events; here we dispatch one directly so the example is self-contained.
        instance.on_event(
            Event(event_type="project.created", data={"project_id": 42}, workspace_id=1, user_id=1)
        )
    finally:
        instance.unsubscribe()

    print(f"{manifest.id} registered; recorded {len(instance.received)} event(s).")


if __name__ == "__main__":
    main()
