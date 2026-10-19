"""Plugin configuration validation (per-workspace).

A plugin manifest may declare a ``configuration`` mapping that describes the
configuration keys it understands (with example/default values). When declared,
that mapping is treated as an ad-hoc schema:

* only keys listed in the manifest's ``configuration`` are allowed;
* when a default value exists for a key, a supplied value must have the same
  JSON type (bool/int/float/str/list/dict/null);
* objects are length/depth-bounded to keep stored configuration small.

Plugins that declare no ``configuration`` accept any bounded JSON object.
Validation never inspects or leaks stored values and never touches secrets.
"""

from __future__ import annotations

import json
from typing import Any

from app.services.plugin_limits import get_plugin_limits

#: Maximum total serialized size of a stored configuration object.
MAX_CONFIG_CHARS = 64 * 1024
#: Maximum number of top-level keys.
MAX_CONFIG_KEYS = 200
#: Maximum nesting depth of a configuration object.
MAX_CONFIG_DEPTH = 8


def _json_kind(value: Any) -> str:
    """Return a coarse JSON type category for ``value``."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return "other"


def _depth_of(value: Any, max_depth: int, depth: int = 0) -> int:
    if not isinstance(value, (dict, list)) or depth > max_depth:
        return depth
    deepest = depth + 1
    items = value.values() if isinstance(value, dict) else value
    for item in items:
        deepest = max(deepest, _depth_of(item, max_depth, depth + 1))
    return deepest


def validate_plugin_config(
    declared: dict[str, Any] | None,
    proposed: Any,
    *,
    max_bytes: int | None = None,
    max_keys: int | None = None,
    max_depth: int | None = None,
) -> tuple[bool, str]:
    """Validate ``proposed`` against the manifest's ``declared`` configuration.

    Returns ``(ok, reason)``. ``proposed`` must be a JSON object that fits the
    declared key set/type expectations and the configured size/depth bounds
    (:mod:`app.services.plugin_limits`). Explicit bound arguments override the
    configured values, which keeps the validator usable outside an app context.
    """
    limits = get_plugin_limits()
    max_bytes = limits.config_max_bytes if max_bytes is None else max_bytes
    max_keys = limits.config_max_keys if max_keys is None else max_keys
    max_depth = limits.config_max_depth if max_depth is None else max_depth

    if not isinstance(proposed, dict):
        return False, "Configuration must be a JSON object."
    if not proposed:
        return True, ""

    if len(proposed) > max_keys:
        return False, f"Configuration has too many keys (max {max_keys})."
    if _depth_of(proposed, max_depth) > max_depth:
        return False, f"Configuration nests too deeply (max {max_depth})."
    if len(json.dumps(proposed)) > max_bytes:
        return False, f"Configuration is too large (max {max_bytes} bytes)."

    declared = declared if isinstance(declared, dict) else {}
    if declared:
        unknown = sorted(set(proposed.keys()) - set(declared.keys()))
        if unknown:
            return False, f"Unknown configuration key(s): {', '.join(unknown)}."
        for key, value in proposed.items():
            default = declared.get(key)
            if default is None:
                continue
            expected = _json_kind(default)
            actual = _json_kind(value)
            # JSON has one numeric kind for validation purposes.
            if expected in ("int", "float"):
                expected = "number"
            if actual in ("int", "float"):
                actual = "number"
            if actual != expected:
                return (
                    False,
                    f"Configuration key {key!r} must be {expected}, got {actual}.",
                )
    return True, ""
