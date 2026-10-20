"""Tests for the catalog-aware dependency graph builder (#391)."""

import json

import pytest

from app.extensions import db
from app.models import Plugin, Workspace
from app.services.plugin_catalog import parse_catalog
from app.services.plugin_deps import (
    CatalogDependencyGraph,
    PluginDependencyCycleError,
    PluginDependencyVersionError,
    validate_catalog_dependencies,
)
from app.services.plugin_ops import RemoteInstallError, install_from_catalog


def _manifest(plugin_id, dependencies=()):
    return {
        "id": plugin_id,
        "name": plugin_id.replace("-", " ").title(),
        "version": "0.1.0",
        "description": "dependency graph fixture",
        "author": "tester",
        "entry_point": "plugins.test:TestPlugin",
        "capabilities": ["PROJECT_READ"],
        "dependencies": list(dependencies),
    }


def _entry(plugin_id, dependencies=(), version="0.1.0"):
    manifest = _manifest(plugin_id, dependencies)
    manifest["version"] = version
    return {
        "id": plugin_id,
        "version": version,
        "source": f"catalog/{plugin_id}-{version}.zip",
        "integrity": "a" * 64,
        "manifest": manifest,
    }


def _catalog(entries):
    return parse_catalog({"version": 1, "plugins": entries})


def _order(entries, plugin_id="app"):
    catalog = _catalog(entries)
    return validate_catalog_dependencies(catalog, catalog.get(plugin_id))


class TestCatalogDependencyGraph:
    def test_simple_chain_is_dependency_first(self):
        order = _order(
            [
                _entry("app", ["middle"]),
                _entry("middle", ["base"]),
                _entry("base"),
            ]
        )

        assert order == ("base", "middle", "app")

    def test_diamond_is_not_a_false_cycle(self):
        order = _order(
            [
                _entry("app", ["left", "right"]),
                _entry("left", ["shared"]),
                _entry("right", ["shared"]),
                _entry("shared"),
            ]
        )

        assert order == ("shared", "left", "right", "app")
        assert len(order) == len(set(order))

    def test_direct_cycle_reports_the_full_chain(self):
        catalog = _catalog(
            [
                _entry("a", ["b"]),
                _entry("b", ["a"]),
            ]
        )

        with pytest.raises(PluginDependencyCycleError) as excinfo:
            validate_catalog_dependencies(catalog, catalog.get("a"))

        assert "a -> b -> a" in excinfo.value.message

    def test_indirect_cycle_reports_the_full_chain(self):
        catalog = _catalog(
            [
                _entry("a", ["b"]),
                _entry("b", ["c"]),
                _entry("c", ["a"]),
            ]
        )

        with pytest.raises(PluginDependencyCycleError) as excinfo:
            validate_catalog_dependencies(catalog, catalog.get("a"))

        assert "a -> b -> c -> a" in excinfo.value.message

    def test_duplicate_edges_are_deduped(self):
        order = _order(
            [
                _entry("app", ["base", "base>=0.1.0"]),
                _entry("base"),
            ]
        )

        assert order == ("base", "app")

    def test_order_is_deterministic_regardless_of_catalog_order(self):
        entries = [
            _entry("app", ["right", "left"]),
            _entry("left", ["shared"]),
            _entry("right", ["shared"]),
            _entry("shared"),
        ]

        first = _order(entries)
        second = _order(list(reversed(entries)))

        assert first == second == ("shared", "left", "right", "app")

    def test_non_catalog_dependencies_are_not_required_before_install(self):
        order = _order(
            [
                _entry("app", ["base", "requests>=2"]),
                _entry("base"),
            ]
        )

        assert order == ("base", "app")

    def test_catalogued_dependency_without_satisfying_version_is_rejected(self):
        catalog = _catalog(
            [
                _entry("app", ["base>=2"]),
                _entry("base", version="1.0.0"),
            ]
        )

        with pytest.raises(PluginDependencyVersionError, match="no catalogued version"):
            validate_catalog_dependencies(catalog, catalog.get("app"))

    def test_builder_can_be_reused_for_multiple_roots(self):
        catalog = _catalog(
            [
                _entry("app", ["shared"]),
                _entry("other", ["shared"]),
                _entry("shared"),
            ]
        )
        graph = CatalogDependencyGraph(catalog)

        assert graph.validate(catalog.get("app")) == ("shared", "app")
        assert graph.validate(catalog.get("other")) == ("shared", "other")


class TestCatalogInstallDependencyValidation:
    def _write_catalog(self, tmp_path, entries):
        path = tmp_path / "catalog.json"
        path.write_text(json.dumps({"version": 1, "plugins": entries}), encoding="utf-8")
        return path

    def test_service_refuses_cycle_before_registration(self, app, tmp_path):
        app.config["PLUGIN_CATALOG_FILE"] = str(
            self._write_catalog(tmp_path, [_entry("a", ["b"]), _entry("b", ["a"])])
        )

        with app.app_context():
            with pytest.raises(RemoteInstallError, match="a -> b -> a"):
                install_from_catalog("a")
            assert db.session.get(Plugin, "a") is None

    def test_api_refuses_cycle_before_registration(self, app, client, make_user, login, tmp_path):
        app.config["PLUGIN_CATALOG_FILE"] = str(
            self._write_catalog(tmp_path, [_entry("a", ["b"]), _entry("b", ["a"])])
        )
        owner = make_user()
        login()
        workspace = Workspace(user_id=owner.id, name="Cycle workspace")
        db.session.add(workspace)
        db.session.commit()

        response = client.post(
            f"/plugins/api/workspaces/{workspace.id}/plugins/install-from-catalog",
            json={"plugin_id": "a"},
        )

        assert response.status_code == 400
        assert "a -> b -> a" in response.get_json()["error"]
        assert db.session.get(Plugin, "a") is None
        assert db.session.get(Plugin, "b") is None
