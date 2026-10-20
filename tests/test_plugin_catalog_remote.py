"""Remote marketplace catalog tests (#375).

The fetch is https-only, allowlisted, bounded, and redirect-free. These tests
use a mocked transport so no socket is opened and assert that policy violations
are refused before the transport runs.
"""

import json

import pytest

from app.services import plugin_catalog
from app.services.plugin_catalog import (
    MAX_CATALOG_BYTES,
    CatalogError,
    CatalogUnavailableError,
    load_plugin_catalog,
    load_remote_catalog,
)

URL = "https://plugins.example.com/catalog.json"


def _manifest(plugin_id="hello-world", version="1.0.0"):
    return {
        "id": plugin_id,
        "name": "Hello World",
        "version": version,
        "description": "A test catalog entry",
        "author": "Tester",
        "entry_point": "plugins.hello:HelloPlugin",
        "capabilities": [],
    }


def _catalog(plugin_id="hello-world", version="1.0.0"):
    return {
        "version": 1,
        "plugins": [
            {
                "id": plugin_id,
                "version": version,
                "source": "https://plugins.example.com/hello-1.0.0.zip",
                "integrity": "sha256:" + "a" * 64,
                "manifest": _manifest(plugin_id, version),
            }
        ],
    }


def _config(**overrides):
    config = {
        "PLUGIN_MARKETPLACE_URL": URL,
        "PLUGIN_NETWORK_ALLOWLIST": "plugins.example.com",
        "PLUGIN_NETWORK_HTTPS_ONLY": True,
        "PLUGIN_NETWORK_TIMEOUT": 7,
        "PLUGIN_NETWORK_MAX_BYTES": 4096,
    }
    config.update(overrides)
    return config


def _resolver(_host):
    return ["93.184.216.34"]


class _Response:
    def __init__(self, content=b"", status_code=200, headers=None):
        self.content = content
        self.status_code = status_code
        self.headers = headers or {}


class _Transport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def _body(document=None):
    return json.dumps(document or _catalog()).encode("utf-8")


class TestRemoteCatalogFetch:
    def test_fetches_and_validates_an_allowlisted_https_catalog(self):
        transport = _Transport(_Response(_body(), headers={"Content-Length": str(len(_body()))}))
        catalog = load_remote_catalog(_config(), resolver=_resolver, transport=transport)

        assert len(catalog) == 1
        assert catalog.get("hello-world").version == "1.0.0"
        assert len(transport.calls) == 1
        url, kwargs = transport.calls[0]
        assert url == URL
        assert kwargs["max_bytes"] == MAX_CATALOG_BYTES
        assert kwargs["policy"].timeout == 7

    def test_refuses_http_before_any_request(self):
        transport = _Transport(_Response(_body()))
        with pytest.raises(CatalogUnavailableError, match="https"):
            load_remote_catalog(
                _config(PLUGIN_MARKETPLACE_URL="http://plugins.example.com/catalog.json"),
                resolver=_resolver,
                transport=transport,
            )
        assert transport.calls == []

    def test_refuses_a_host_outside_the_allowlist_before_any_request(self):
        transport = _Transport(_Response(_body()))
        with pytest.raises(CatalogUnavailableError, match="allowlist"):
            load_remote_catalog(
                _config(PLUGIN_MARKETPLACE_URL="https://evil.example.com/catalog.json"),
                resolver=_resolver,
                transport=transport,
            )
        assert transport.calls == []

    def test_refuses_an_empty_allowlist_before_any_request(self):
        transport = _Transport(_Response(_body()))
        with pytest.raises(CatalogUnavailableError, match="allowlist"):
            load_remote_catalog(
                _config(PLUGIN_NETWORK_ALLOWLIST=""),
                resolver=_resolver,
                transport=transport,
            )
        assert transport.calls == []

    def test_refuses_a_redirect(self):
        transport = _Transport(_Response(b"", status_code=302, headers={"Location": "https://x"}))
        with pytest.raises(CatalogUnavailableError, match="redirect"):
            load_remote_catalog(_config(), resolver=_resolver, transport=transport)

    def test_refuses_a_non_success_status(self):
        transport = _Transport(_Response(b"{}", status_code=503))
        with pytest.raises(CatalogUnavailableError, match="status"):
            load_remote_catalog(_config(), resolver=_resolver, transport=transport)

    def test_refuses_an_oversized_body(self):
        transport = _Transport(_Response(b"{" + b" " * MAX_CATALOG_BYTES))
        with pytest.raises(CatalogUnavailableError, match="exceeds"):
            load_remote_catalog(_config(), resolver=_resolver, transport=transport)

    def test_refuses_an_oversized_declared_length(self):
        transport = _Transport(
            _Response(_body(), headers={"Content-Length": str(MAX_CATALOG_BYTES + 1)})
        )
        with pytest.raises(CatalogUnavailableError, match="exceeds"):
            load_remote_catalog(_config(), resolver=_resolver, transport=transport)

    def test_rejects_invalid_json(self):
        transport = _Transport(_Response(b"{not json"))
        with pytest.raises(CatalogError, match="JSON"):
            load_remote_catalog(_config(), resolver=_resolver, transport=transport)

    def test_rejects_an_invalid_catalog_document(self):
        transport = _Transport(_Response(b'{"version": 99, "plugins": []}'))
        with pytest.raises(CatalogError, match="version"):
            load_remote_catalog(_config(), resolver=_resolver, transport=transport)


class TestConfiguration:
    def test_load_plugin_catalog_dispatches_to_the_remote_url(self, monkeypatch):
        sentinel = object()
        calls = []

        def fake_remote(config):
            calls.append(config)
            return sentinel

        monkeypatch.setattr(plugin_catalog, "load_remote_catalog", fake_remote)
        result = load_plugin_catalog({"PLUGIN_CATALOG_FILE": "", "PLUGIN_MARKETPLACE_URL": URL})
        assert result is sentinel
        assert calls == [{"PLUGIN_CATALOG_FILE": "", "PLUGIN_MARKETPLACE_URL": URL}]

    def test_neither_source_configured_is_unavailable(self):
        with pytest.raises(CatalogUnavailableError, match="PLUGIN_CATALOG_FILE"):
            load_plugin_catalog({"PLUGIN_CATALOG_FILE": "", "PLUGIN_MARKETPLACE_URL": ""})

    def test_local_file_takes_precedence_over_remote(self, tmp_path, monkeypatch):
        catalog_file = tmp_path / "catalog.json"
        catalog_file.write_text(json.dumps(_catalog()), encoding="utf-8")

        def fail_remote(_config):
            raise AssertionError("remote catalog must not be fetched")

        monkeypatch.setattr(plugin_catalog, "load_remote_catalog", fail_remote)
        catalog = load_plugin_catalog(
            {
                "PLUGIN_CATALOG_FILE": str(catalog_file),
                "PLUGIN_MARKETPLACE_URL": URL,
            }
        )
        assert catalog.get("hello-world") is not None
