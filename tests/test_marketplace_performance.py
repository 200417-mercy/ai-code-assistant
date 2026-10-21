"""Performance guardrails for the read-only marketplace catalog (#424).

The catalog loader caps entries at 500, so a maximum-size catalog is the
worst-case input. The assertions are deliberately generous enough for slow CI
runners while still catching accidental unbounded work (for example, loading
the whole catalog into every response) or super-linear filtering.
"""

from __future__ import annotations

import json
import time
import tracemalloc

from app.services.marketplace import filter_entries, load_marketplace

CATALOG_SIZE = 500
HASH = "a" * 64


def _manifest(index: int) -> dict:
    return {
        "id": f"plugin-{index}",
        "name": f"Plugin {index}",
        "version": "1.0.0",
        "description": f"Synthetic marketplace plugin {index}",
        "author": "Benchmark Labs",
        "entry_point": "plugins.bench:BenchPlugin",
        "capabilities": ["PROJECT_READ"],
        "permissions": ["read:project:files"],
        "dependencies": ["base-plugin>=1.0.0"],
        "compatibility": ">=0.8.0",
    }


def _entry(index: int) -> dict:
    return {
        "id": f"plugin-{index}",
        "version": "1.0.0",
        "source": f"https://plugins.example.com/plugin-{index}.zip",
        "integrity": f"sha256:{HASH}",
        "manifest": _manifest(index),
    }


def _write_catalog(tmp_path, size: int = CATALOG_SIZE):
    path = tmp_path / "marketplace.json"
    path.write_text(
        json.dumps({"version": 1, "plugins": [_entry(index) for index in range(size)]}),
        encoding="utf-8",
    )
    return path


def _config(path) -> dict:
    return {
        "PLUGIN_MARKETPLACE_ENABLED": True,
        "PLUGIN_MARKETPLACE_CATALOG": str(path),
        "PLUGIN_MARKETPLACE_URL": "",
    }


def test_maximum_catalog_load_and_filter_are_bounded(tmp_path):
    path = _write_catalog(tmp_path)
    config = _config(path)

    tracemalloc.start()
    try:
        started = time.perf_counter()
        catalog = load_marketplace(config)
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(catalog) == CATALOG_SIZE
    assert elapsed < 10.0, f"catalog load took {elapsed:.2f}s"
    assert peak < 128 * 1024 * 1024, f"catalog load peaked at {peak / 1024 / 1024:.1f} MiB"

    started = time.perf_counter()
    matches = filter_entries(catalog, query="plugin-499")
    filter_elapsed = time.perf_counter() - started

    assert [entry.plugin_id for entry in matches] == ["plugin-499"]
    assert filter_elapsed < 2.0, f"filtering took {filter_elapsed:.2f}s"


def test_api_response_is_paginated_and_bounded(app, client, make_user, login, tmp_path):
    make_user(username="perf-user", email="perf@example.com")
    app.config.update(_config(_write_catalog(tmp_path)))
    login(email="perf@example.com")

    started = time.perf_counter()
    response = client.get("/plugins/api/marketplace?limit=100")
    elapsed = time.perf_counter() - started

    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload["entries"]) == 100
    assert payload["pagination"]["total"] == CATALOG_SIZE
    assert payload["pagination"]["pages"] == 5
    assert elapsed < 10.0, f"paged API response took {elapsed:.2f}s"
    # 100 entries of metadata only; the full 500-entry catalog is never echoed.
    assert len(response.data) < 2 * 1024 * 1024
    expected_ids = sorted(f"plugin-{index}" for index in range(CATALOG_SIZE))[:100]
    assert [entry["id"] for entry in payload["entries"]] == expected_ids


def test_catalog_filtering_matches_the_declared_capability(tmp_path):
    path = _write_catalog(tmp_path, size=50)
    catalog = load_marketplace(_config(path))

    matches = filter_entries(catalog, capability="project_read")
    assert len(matches) == 50
    assert filter_entries(catalog, capability="stellar_write") == []
