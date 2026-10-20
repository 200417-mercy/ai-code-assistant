"""Call-time plugin capability assertions (#407)."""

from app.services.capabilities import CapabilityStore
from app.services.plugins import PluginRegistry


def _dispatch(registry, calls, workspace_id=1, capability="project:read"):
    registry.dispatch(
        "project.created",
        {},
        workspace_id=workspace_id,
        required_capability=capability,
    )
    return calls


class TestCallTimeCapabilities:
    def test_missing_grant_is_refused(self, app):
        with app.app_context():
            registry = PluginRegistry()
            calls = []
            registry.subscribe("plugin-a", "project.created", lambda **_: calls.append(1))
            assert _dispatch(registry, calls, workspace_id=31) == []

    def test_granted_capability_runs_handler(self, app):
        with app.app_context():
            registry = PluginRegistry()
            calls = []
            registry.subscribe("plugin-a", "project.created", lambda **_: calls.append(1))
            CapabilityStore.grant("plugin-a", 32, "project:read")
            assert _dispatch(registry, calls, workspace_id=32) == [1]

    def test_revoked_capability_is_refused(self, app):
        with app.app_context():
            registry = PluginRegistry()
            calls = []
            registry.subscribe("plugin-a", "project.created", lambda **_: calls.append(1))
            CapabilityStore.grant("plugin-a", 33, "project:read")
            CapabilityStore.revoke("plugin-a", 33, "project:read")
            assert _dispatch(registry, calls, workspace_id=33) == []

    def test_wrong_workspace_is_refused(self, app):
        with app.app_context():
            registry = PluginRegistry()
            calls = []
            registry.subscribe("plugin-a", "project.created", lambda **_: calls.append(1))
            CapabilityStore.grant("plugin-a", 34, "project:read")
            assert _dispatch(registry, calls, workspace_id=35) == []

    def test_missing_workspace_fails_closed(self, app):
        with app.app_context():
            registry = PluginRegistry()
            calls = []
            registry.subscribe("plugin-a", "project.created", lambda **_: calls.append(1))
            CapabilityStore.grant("plugin-a", 36, "project:read")
            registry.dispatch("project.created", {}, required_capability="project:read")
            assert calls == []
