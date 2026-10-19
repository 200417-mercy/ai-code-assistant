import re

import pytest
from flask import Flask

from app.observability import init_observability, record_usage
from app.observability.metrics import (
    MAX_SERIES_PER_METRIC,
    REGISTRY,
    Counter,
)

SAMPLE_LINE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*(\{[^}]*\})? -?\d+(\.\d+)?([eE][+-]?\d+)?$")


def make_app(**config):
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config.update(config)
    init_observability(app)

    @app.get("/items/<int:item_id>")
    def item(item_id):
        return {"id": item_id}

    return app


@pytest.fixture()
def client():
    REGISTRY.reset()
    yield make_app().test_client()
    REGISTRY.reset()


def test_counter_renders_help_type_and_sample():
    counter = Counter("demo_total", "A demo.", ("kind",))
    counter.inc(2, kind="a")
    lines = counter.render()
    assert lines[0] == "# HELP demo_total A demo."
    assert lines[1] == "# TYPE demo_total counter"
    assert 'demo_total{kind="a"} 2' in lines


def test_counter_escapes_label_values():
    counter = Counter("esc_total", "Escaping.", ("kind",))
    counter.inc(kind='a"b\\c\nd')
    assert 'esc_total{kind="a\\"b\\\\c\\nd"} 1' in counter.render()


def test_counter_rejects_negative_and_wrong_labels():
    counter = Counter("neg_total", "Negative.", ("kind",))
    with pytest.raises(ValueError):
        counter.inc(-1, kind="a")
    with pytest.raises(ValueError):
        counter.inc(1, other="a")


def test_counter_cardinality_is_bounded():
    counter = Counter("cap_total", "Cap.", ("kind",))
    for i in range(MAX_SERIES_PER_METRIC + 50):
        counter.inc(kind=f"k{i}")
    samples = [line for line in counter.render() if not line.startswith("#")]
    assert len(samples) == MAX_SERIES_PER_METRIC + 1
    assert 'cap_total{kind="other"} 50' in samples


def test_metrics_endpoint_returns_valid_exposition(client):
    client.get("/items/1")
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.content_type.startswith("text/plain")
    assert "version=0.0.4" in response.content_type
    body = response.get_data(as_text=True)
    assert body.endswith("\n")
    for line in body.strip().splitlines():
        assert line.startswith("#") or SAMPLE_LINE.match(line), line


def test_metrics_declares_all_families(client):
    body = client.get("/metrics").get_data(as_text=True)
    for name in ("http_requests_total", "llm_tokens_total", "llm_cost_usd_total"):
        assert f"# TYPE {name} counter" in body


def test_request_counter_uses_route_pattern_not_raw_path(client):
    client.get("/items/1")
    client.get("/items/2")
    body = client.get("/metrics").get_data(as_text=True)
    assert (
        'http_requests_total{method="GET",endpoint="/items/<int:item_id>",' 'status="2xx"} 2'
    ) in body
    assert "/items/1" not in body


def test_unknown_paths_share_one_series(client):
    client.get("/nope-1")
    client.get("/nope-2")
    body = client.get("/metrics").get_data(as_text=True)
    assert ('http_requests_total{method="GET",endpoint="unmatched",status="4xx"} 2') in body


def test_token_and_cost_counters_are_published(client):
    record_usage("openai", "gpt-x", 10, 5, 0.25)
    record_usage("openai", "gpt-x", 2, 1, 0.5)
    body = client.get("/metrics").get_data(as_text=True)
    assert 'llm_tokens_total{provider="openai",model="gpt-x",direction="in"} 12' in body
    assert 'llm_tokens_total{provider="openai",model="gpt-x",direction="out"} 6' in body
    assert 'llm_cost_usd_total{provider="openai",model="gpt-x"} 0.75' in body


def test_record_usage_ignores_missing_values(client):
    record_usage("openai", "gpt-x", None, None, None)
    body = client.get("/metrics").get_data(as_text=True)
    assert 'provider="openai"' not in body


def test_metrics_endpoint_can_be_disabled():
    REGISTRY.reset()
    response = make_app(METRICS_ENABLED=False).test_client().get("/metrics")
    assert response.status_code == 404
