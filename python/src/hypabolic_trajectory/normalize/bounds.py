"""Tool-argument shrinking and tool-result truncation (Unicode scalar lengths).

Authority: contracts/spec/normalization.md + tip Rust/TS normalizers.
Goldens (e.g. pi/unicode-boundaries) pin the ellipsis truncation marker.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from hypabolic_trajectory.canonical import compact_json


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


def shrink_arguments(
    raw_input: str | None,
    limit: int | None,
) -> tuple[str, bool, bool]:
    """Return ``(arguments_json, reshaped, truncated)`` for a tool call.

    Empty / non-object arguments become a JSON object with ``_raw``. Object
    arguments over *limit* zero out the longest string leaves until they fit
    (tip Rust/TS algorithm). Falls back to bounded ``_raw`` when still too large.
    """
    raw = raw_input if raw_input else "{}"
    parsed: Any = None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        parsed = None

    if not isinstance(parsed, dict):
        full = wrap_raw(raw)
        if limit is None or code_point_length(full) <= limit:
            return full, True, False
        return wrap_raw(raw, limit), True, True

    if limit is None or code_point_length(raw) <= limit:
        return raw, False, False

    # Deep-copy via round-trip so mutations don't affect caller state.
    clone: dict[str, Any] = json.loads(raw)
    leaves: list[tuple[Any, Any]] = []
    _collect_string_leaves(clone, leaves)
    lengths = [
        code_point_length(parent[key]) if isinstance(parent[key], str) else 0
        for parent, key in leaves
    ]

    serialized = compact_json(clone)
    while code_point_length(serialized) > limit:
        # Rust max_by_key(|(index, length)| (**length, Reverse(*index))):
        # largest length wins; for equal lengths, smaller index wins.
        candidates = [(i, length) for i, length in enumerate(lengths) if length > 0]
        if not candidates:
            break
        largest = max(candidates, key=lambda pair: (pair[1], -pair[0]))[0]
        parent, key = leaves[largest]
        parent[key] = ""
        lengths[largest] = 0
        serialized = compact_json(clone)

    if code_point_length(serialized) <= limit:
        return serialized, False, True
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
