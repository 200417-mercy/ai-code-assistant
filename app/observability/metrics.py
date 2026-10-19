"""In-process Prometheus metrics with bounded label cardinality."""

from __future__ import annotations

import threading

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
MAX_SERIES_PER_METRIC = 500
OVERFLOW_LABEL = "other"
MAX_LABEL_LENGTH = 64


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _clean(value: object) -> str:
    text = str(value) if value not in (None, "") else "unknown"
    return text[:MAX_LABEL_LENGTH]


def _format_value(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return repr(float(value))


class Counter:
    """A monotonic counter. The number of label combinations is capped."""

    def __init__(self, name: str, help_text: str, label_names: tuple[str, ...] = ()):
        self.name = name
        self.help_text = help_text
        self.label_names = label_names
        self._values: dict[tuple[str, ...], float] = {}
        self._lock = threading.Lock()

    def inc(self, amount: float = 1.0, **labels: object) -> None:
        if amount < 0:
            raise ValueError("counters can only increase")
        if set(labels) != set(self.label_names):
            raise ValueError(f"{self.name} expects labels {self.label_names}")
        key = tuple(_clean(labels[name]) for name in self.label_names)
        with self._lock:
            if key not in self._values and len(self._values) >= MAX_SERIES_PER_METRIC:
                key = tuple(OVERFLOW_LABEL for _ in self.label_names)
            self._values[key] = self._values.get(key, 0.0) + amount

    def reset(self) -> None:
        with self._lock:
            self._values.clear()

    def render(self) -> list[str]:
        lines = [
            f"# HELP {self.name} {self.help_text}",
            f"# TYPE {self.name} counter",
        ]
        with self._lock:
            items = sorted(self._values.items())
        for key, value in items:
            if self.label_names:
                pairs = ",".join(
                    f'{name}="{_escape(val)}"'
                    for name, val in zip(self.label_names, key, strict=False)
                )
                lines.append(f"{self.name}{{{pairs}}} {_format_value(value)}")
            else:
                lines.append(f"{self.name} {_format_value(value)}")
        return lines


class Registry:
    def __init__(self) -> None:
        self._counters: list[Counter] = []

    def counter(self, name: str, help_text: str, label_names: tuple[str, ...] = ()) -> Counter:
        counter = Counter(name, help_text, label_names)
        self._counters.append(counter)
        return counter

    def reset(self) -> None:
        for counter in self._counters:
            counter.reset()

    def render(self) -> str:
        lines: list[str] = []
        for counter in self._counters:
            lines.extend(counter.render())
        return "\n".join(lines) + "\n"


REGISTRY = Registry()

HTTP_REQUESTS = REGISTRY.counter(
    "http_requests_total",
    "Total HTTP requests processed.",
    ("method", "endpoint", "status"),
)
LLM_TOKENS = REGISTRY.counter(
    "llm_tokens_total",
    "Total LLM tokens consumed.",
    ("provider", "model", "direction"),
)
LLM_COST = REGISTRY.counter(
    "llm_cost_usd_total",
    "Estimated LLM cost in USD.",
    ("provider", "model"),
)


def record_request(method: str, endpoint: str, status_code: int) -> None:
    HTTP_REQUESTS.inc(
        method=method,
        endpoint=endpoint,
        status=f"{status_code // 100}xx",
    )


def record_usage(
    provider: str,
    model: str,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    cost_usd: float | None = None,
) -> None:
    """Record LLM usage. Never raises on missing values."""
    if prompt_tokens and prompt_tokens > 0:
        LLM_TOKENS.inc(prompt_tokens, provider=provider, model=model, direction="in")
    if completion_tokens and completion_tokens > 0:
        LLM_TOKENS.inc(completion_tokens, provider=provider, model=model, direction="out")
    if cost_usd and cost_usd > 0:
        LLM_COST.inc(cost_usd, provider=provider, model=model)


def render_metrics() -> str:
    return REGISTRY.render()
