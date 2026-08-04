"""Tool-argument shrinking and tool-result truncation (Unicode scalar lengths).

Authority: contracts/spec/normalization.md + tip Rust/TS/.NET normalizers.
Goldens (e.g. pi/unicode-boundaries) pin the ellipsis truncation marker for
tool *results*. Object-argument shrinking retains the 2,000-scalar preferred
leaf floor (contract pin) with tip-compatible zeroing fallback.
"""

from __future__ import annotations

import json
import math
from typing import Any, Literal

from hypabolic_trajectory.canonical import compact_json, escape_json_string

# Preferred floor while shrinking object string leaves (normalization.md pin).
_ARGUMENT_LEAF_FLOOR = 2_000


def code_point_length(value: str) -> int:
    """Count Unicode scalar values (Python ``str`` code points)."""
    return len(value)


def truncation_marker(removed: int) -> str:
    """Tip marker for omitted scalars: single ellipsis when anything is removed."""
    return "…" if removed > 0 else ""


def slice_code_points(value: str, start: int, end: int) -> str:
    """Slice *value* by Unicode scalar indexes ``[start, end)``."""
    if start < 0:
        start = 0
    if end < start:
        end = start
    return "".join(list(value)[start:end])


def wrap_raw(raw: str, limit: int | None = None) -> str:
    """Wrap *raw* as ``{\"_raw\": ...}`` compact JSON; optionally fit *limit*."""
    if limit is None:
        return compact_json({"_raw": raw})
    full = compact_json({"_raw": raw})
    if code_point_length(full) <= limit:
        return full
    points = list(raw)
    low = 0
    high = min(len(points), limit)
    best = "{}"
    while low <= high:
        keep = low + (high - low) // 2
        candidate = compact_json(
            {
                "_raw": "".join(points[:keep])
                + truncation_marker(len(points) - keep)
            }
        )
        if code_point_length(candidate) <= limit:
            best = candidate
            low = keep + 1
        elif keep == 0:
            break
        else:
            high = keep - 1
    return best


def _bounds_relaxed_json(value: Any) -> str:
    """Compact emit for tool-argument re-serialize (peer tip ``relaxed_json``).

    Unlike identity ``compact_json`` / ``canonical_json``, integers are emitted
    with invariant decimal formatting and **no** signed-int64 rejection. Tool
    argument payloads may contain arbitrary JSON numbers; peers (TS/Rust/.NET)
    re-emit those without hard-failing the bounds path.
    """
    return _bounds_write_value(value, active=set())


def _bounds_write_value(value: Any, *, active: set[int]) -> str:
    if value is None:
        return "null"
    # bool before int (bool is a subclass of int).
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if type(value) is float:
        if not math.isfinite(value):
            raise TypeError(
                "bounds relaxed JSON does not accept non-finite floats"
            )
        return json.dumps(value, allow_nan=False)
    if type(value) is str:
        return escape_json_string(value)
    if type(value) is dict:
        obj_id = id(value)
        if obj_id in active:
            raise TypeError("bounds relaxed JSON does not accept cyclic trees")
        active.add(obj_id)
        try:
            parts: list[str] = []
            for k, v in value.items():
                if type(k) is not str:
                    raise TypeError(
                        f"JSON object keys must be str, got {type(k).__name__}"
                    )
                parts.append(
                    f"{escape_json_string(k)}:{_bounds_write_value(v, active=active)}"
                )
            return "{" + ",".join(parts) + "}"
        finally:
            active.discard(obj_id)
    if type(value) is list:
        list_id = id(value)
        if list_id in active:
            raise TypeError("bounds relaxed JSON does not accept cyclic trees")
        active.add(list_id)
        try:
            body = ",".join(
                _bounds_write_value(item, active=active) for item in value
            )
            return "[" + body + "]"
        finally:
            active.discard(list_id)
    raise TypeError(
        f"value is not JSON-serializable for bounds emit: {type(value).__name__}"
    )


def _reject_non_finite_constant(name: str) -> float:
    raise ValueError(f"non-finite JSON constant: {name}")


def _parse_arguments_json(raw: str) -> Any | None:
    """Parse tool arguments JSON; reject non-finite number constants.

    ``json.loads`` accepts bare ``NaN``/``Infinity`` by default; those are not
    valid JSON and must be reshaped to ``_raw`` like other non-object values.
    """
    try:
        return json.loads(raw, parse_constant=_reject_non_finite_constant)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _contains_non_finite(value: Any) -> bool:
    if type(value) is float:
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(_contains_non_finite(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_non_finite(v) for v in value)
    return False


def _collect_string_leaves(
    value: Any,
    leaves: list[tuple[Any, Any]],
) -> None:
    """Collect ``(parent, key)`` pairs for every string leaf under *value*."""
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, str):
                leaves.append((value, key))
            elif isinstance(child, (dict, list)):
                _collect_string_leaves(child, leaves)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            if isinstance(child, str):
                leaves.append((value, index))
            elif isinstance(child, (dict, list)):
                _collect_string_leaves(child, leaves)


def _clone_object(raw: str) -> dict[str, Any]:
    parsed = json.loads(raw, parse_constant=_reject_non_finite_constant)
    assert isinstance(parsed, dict)
    return parsed


def _shrink_object_with_floor(clone: dict[str, Any], limit: int) -> str:
    """Progressive leaf shrink preferring the 2,000-scalar floor (.NET legacy)."""
    leaves: list[tuple[Any, Any]] = []
    _collect_string_leaves(clone, leaves)
    serialized = _bounds_relaxed_json(clone)
    seen: set[str] = set()
    while code_point_length(serialized) > limit and leaves:
        if serialized in seen:
            break
        seen.add(serialized)
        largest_parent: Any = None
        largest_key: Any = None
        largest_len = -1
        for parent, key in leaves:
            text = parent[key]
            if not isinstance(text, str):
                continue
            length = code_point_length(text)
            if length > largest_len:
                largest_len = length
                largest_parent = parent
                largest_key = key
        if largest_parent is None or largest_len <= _ARGUMENT_LEAF_FLOOR:
            break
        value = largest_parent[largest_key]
        value_len = code_point_length(value)
        keep = max(_ARGUMENT_LEAF_FLOOR, value_len // 2)
        largest_parent[largest_key] = (
            slice_code_points(value, 0, keep) + truncation_marker(value_len - keep)
        )
        serialized = _bounds_relaxed_json(clone)
    return serialized


def _shrink_object_zero_leaves(clone: dict[str, Any], limit: int) -> str:
    """Tip fallback: zero longest string leaves until under limit or exhausted."""
    leaves: list[tuple[Any, Any]] = []
    _collect_string_leaves(clone, leaves)
    lengths = [
        code_point_length(parent[key]) if isinstance(parent[key], str) else 0
        for parent, key in leaves
    ]
    serialized = _bounds_relaxed_json(clone)
    while code_point_length(serialized) > limit:
        candidates = [(i, length) for i, length in enumerate(lengths) if length > 0]
        if not candidates:
            break
        # Rust: max_by_key(|(index, length)| (**length, Reverse(*index)))
        largest = max(candidates, key=lambda pair: (pair[1], -pair[0]))[0]
        parent, key = leaves[largest]
        parent[key] = ""
        lengths[largest] = 0
        serialized = _bounds_relaxed_json(clone)
    return serialized


def shrink_arguments(
    raw_input: str | None,
    limit: int | None,
) -> tuple[str, bool, bool]:
    """Return ``(arguments_json, reshaped, truncated)`` for a tool call.

    Empty / non-object / non-finite arguments become a JSON object with ``_raw``.
    Object arguments over *limit* shrink string leaves with a 2,000-scalar
    preferred floor, then zero remaining leaves (tip), then bounded ``_raw``.

    Object re-emit uses bounds-local relaxed JSON (arbitrary JSON ints allowed).
    On non-JSON emit failure, falls back to bounded ``_raw`` like exhausted leaves.
    """
    raw = raw_input if raw_input else "{}"
    parsed = _parse_arguments_json(raw)

    if not isinstance(parsed, dict) or _contains_non_finite(parsed):
        full = wrap_raw(raw)
        if limit is None or code_point_length(full) <= limit:
            return full, True, False
        return wrap_raw(raw, limit), True, True

    if limit is None or code_point_length(raw) <= limit:
        return raw, False, False

    try:
        floor_shrunk = _shrink_object_with_floor(_clone_object(raw), limit)
        if code_point_length(floor_shrunk) <= limit:
            return floor_shrunk, False, True

        zeroed = _shrink_object_zero_leaves(_clone_object(raw), limit)
        if code_point_length(zeroed) <= limit:
            return zeroed, False, True
    except TypeError:
        # Emit failure (unexpected tree shape, etc.): peer-compatible _raw wrap.
        return wrap_raw(raw, limit), True, True

    return wrap_raw(raw, limit), True, True


def truncate_result(
    text: str,
    limit: int | None,
    strategy: Literal["head", "head-tail"],
) -> str:
    """Truncate tool-result body to *limit* Unicode scalars (marker included)."""
    if limit is None:
        return text
    points = list(text)
    if len(points) <= limit:
        return text

    low = 0
    high = min(len(points) - 1, limit)
    keep = -1
    marker = ""
    while low <= high:
        candidate_keep = low + (high - low) // 2
        candidate_marker = truncation_marker(len(points) - candidate_keep)
        if candidate_keep + code_point_length(candidate_marker) <= limit:
            keep = candidate_keep
            marker = candidate_marker
            low = candidate_keep + 1
        else:
            high = candidate_keep - 1

    if keep < 0:
        marker = slice_code_points("…", 0, limit)
        keep = max(0, limit - code_point_length(marker))

    if strategy == "head":
        return "".join(points[:keep]) + marker

    # head-tail: odd retained payload's extra scalar goes to the head.
    head = (keep + 1) // 2
    tail = keep - head
    return (
        "".join(points[:head])
        + marker
        + ("".join(points[len(points) - tail :]) if tail > 0 else "")
    )


__all__ = [
    "code_point_length",
    "shrink_arguments",
    "truncate_result",
    "truncation_marker",
    "wrap_raw",
]
