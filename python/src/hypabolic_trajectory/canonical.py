"""Trajectory canonical JSON, compact/relaxed emit, and shared string escape.

Authority: contracts/spec/canonical-json.md (normalizer 0.2.0) and
docs/python-implementation-spec.md §3 (escape algorithm, TypeError policy).

This is deliberately not RFC 8785/JCS. Object keys sort by unsigned UTF-16
code-unit order. The shared escape is used by ``canonical_json``, later
``serialize_projection`` (PY-07a), and ``project_minimal_jsonl`` (PY-07b).
"""

from __future__ import annotations

import json
import math
from typing import Any

from hypabolic_trajectory._json_types import JsonValue

# Signed int64 bounds — Trajectory integers (contracts/spec/canonical-json.md).
# Values outside this range are invalid for canonical_json / compact_json emit.
INT64_MIN: int = -(2**63)
INT64_MAX: int = 2**63 - 1


def utf16_code_units(value: str) -> list[int]:
    """Return unsigned UTF-16 code units for *value* (JS / .NET Ordinal order)."""
    units: list[int] = []
    for ch in value:
        code = ord(ch)
        if code > 0xFFFF:
            code -= 0x10000
            units.append(0xD800 + (code >> 10))
            units.append(0xDC00 + (code & 0x3FF))
        else:
            units.append(code)
    return units


def utf16_compare(left: str, right: str) -> int:
    """Compare strings by unsigned UTF-16 code-unit lexicographic order.

    Returns negative / zero / positive like ``(a > b) - (a < b)``.
    """
    left_units = utf16_code_units(left)
    right_units = utf16_code_units(right)
    n = min(len(left_units), len(right_units))
    for i in range(n):
        a = left_units[i]
        b = right_units[i]
        if a != b:
            return -1 if a < b else 1
    if len(left_units) == len(right_units):
        return 0
    return -1 if len(left_units) < len(right_units) else 1


def escape_json_string(value: str) -> str:
    """Escape *value* with the Trajectory string-escape algorithm (quoted).

    Walk UTF-16 code units. Short forms for quote, reverse solidus, and
    common controls; ``\\uXXXX`` (four uppercase hex) for remaining controls,
    BMP private-use ``U+E000–F8FF``, ``U+2028``/``U+2029``, and surrogates;
    otherwise UTF-8. Does not escape solidus; does not normalize Unicode.
    """
    parts: list[str] = ['"']
    for unit in utf16_code_units(value):
        if unit == 0x22:
            parts.append('\\"')
        elif unit == 0x5C:
            parts.append("\\\\")
        elif unit == 0x08:
            parts.append("\\b")
        elif unit == 0x09:
            parts.append("\\t")
        elif unit == 0x0A:
            parts.append("\\n")
        elif unit == 0x0C:
            parts.append("\\f")
        elif unit == 0x0D:
            parts.append("\\r")
        elif (
            unit <= 0x1F
            or 0xE000 <= unit <= 0xF8FF
            or unit in (0x2028, 0x2029)
            or 0xD800 <= unit <= 0xDFFF
        ):
            parts.append(f"\\u{unit:04X}")
        else:
            # BMP scalar that is not escaped above — emit as UTF-8.
            parts.append(chr(unit))
    parts.append('"')
    return "".join(parts)


def _emit_int(value: int) -> str:
    """Invariant decimal formatting with no exponent notation.

    Rejects integers outside signed int64 range (canonical-json.md).
    """
    if value < INT64_MIN or value > INT64_MAX:
        raise TypeError(
            "canonical_json integer outside signed int64 range "
            f"[{INT64_MIN}, {INT64_MAX}]"
        )
    return str(value)


def _emit_float(value: float) -> str:
    if not math.isfinite(value):
        raise TypeError(
            "canonical_json does not accept non-finite floats (nan/inf/-inf)"
        )
    # JSON-compatible finite float text (peer runtimes use platform number emit).
    return json.dumps(value, allow_nan=False)


def _write_value(
    value: Any,
    *,
    sort_objects: bool,
    active: set[int],
) -> str:
    if value is None:
        return "null"
    # Exact-type checks only — reject subclasses that can override __str__ /
    # comparisons (adversarial int) or expand JsonValue beyond list/dict.
    # bool is a subclass of int; check bool before int via type identity.
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return _emit_int(value)
    if type(value) is float:
        return _emit_float(value)
    if type(value) is str:
        return escape_json_string(value)
    if type(value) is dict:
        obj_id = id(value)
        if obj_id in active:
            raise TypeError("canonical_json does not accept cyclic JSON trees")
        active.add(obj_id)
        try:
            items: list[tuple[str, Any]] = []
            for key, item in value.items():
                if type(key) is not str:
                    raise TypeError(
                        f"JSON object keys must be str, got {type(key).__name__}"
                    )
                items.append((key, item))
            if sort_objects:
                items.sort(key=lambda kv: utf16_code_units(kv[0]))
            body = ",".join(
                f"{escape_json_string(k)}:{_write_value(v, sort_objects=sort_objects, active=active)}"
                for k, v in items
            )
            return "{" + body + "}"
        finally:
            active.discard(obj_id)
    if type(value) is list:
        list_id = id(value)
        if list_id in active:
            raise TypeError("canonical_json does not accept cyclic JSON trees")
        active.add(list_id)
        try:
            body = ",".join(
                _write_value(item, sort_objects=sort_objects, active=active)
                for item in value
            )
            return "[" + body + "]"
        finally:
            active.discard(list_id)
    raise TypeError(
        f"value is not JSON-serializable for Trajectory emit: {type(value).__name__}"
    )


def canonical_json(value: JsonValue) -> str:
    """Serialize *value* with Trajectory canonical JSON 0.2.0.

    Object keys are ordered by unsigned UTF-16 code units. Compact UTF-8,
    no insignificant whitespace, shared string-escape algorithm.

    Raises:
        TypeError: non-JSON-serializable values, non-finite floats,
        integers outside signed int64 range, or cyclic trees.
    """
    return _write_value(value, sort_objects=True, active=set())


def compact_json(value: JsonValue) -> str:
    """Compact JSON retaining object insertion order (tip ``relaxed_json``).

    For JSON **arrays** (identity tuples, trajectory_id inputs), byte-equivalent
    to ``canonical_json`` and later ``serialize_projection`` compact emit.
    """
    return _write_value(value, sort_objects=False, active=set())


# Tip alias used by Rust/TS naming.
relaxed_json = compact_json


def write_json_string(value: str) -> str:
    """Public name for the shared quoted escape (used by product serializers)."""
    return escape_json_string(value)


__all__ = [
    "INT64_MIN",
    "INT64_MAX",
    "canonical_json",
    "compact_json",
    "escape_json_string",
    "relaxed_json",
    "utf16_code_units",
    "utf16_compare",
    "write_json_string",
]
