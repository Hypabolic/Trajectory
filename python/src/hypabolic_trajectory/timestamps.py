"""Timestamp format helpers (public message clocks + OTEL pad formula).

UNSUPPORTED import path — internal helpers for normalize/project layers.
Authority:
  - contracts/spec/timestamps.md
  - docs/python-implementation-spec.md §3 / §4 (OTEL span-bound time formula)

Peer pin (Rust ``format_ms`` / ``precise_record`` / ``precise_invocation``):

1. ``format_ms(ms)`` → ``yyyy-MM-ddTHH:mm:ss.fffZ`` (three fractional digits, UTC).
2. Public message / listing clocks use the ``Z`` form from filled ``timestamp_ms``.
3. jsonl-minimal: replace trailing ``Z`` with ``+00:00`` (still three fractional digits).
4. OTEL pad: if precise string present, use it **unchanged**; else ``format_ms`` then
   replace trailing ``Z`` with ``0000+00:00`` → ``yyyy-MM-ddTHH:mm:ss.fff0000+00:00``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from hypabolic_trajectory.errors import (
    FATAL_INVALID_INPUT,
    MSG_SOURCE_TIMESTAMP_UNAVAILABLE,
    MSG_TIMESTAMP_OUT_OF_RANGE,
    TrajectoryError,
    raise_trajectory_error,
)

# Signed int64 bounds (lossless epoch-ms range for trajectory fields).
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1

# Fixed UTC epoch — avoid datetime.fromtimestamp (platform-dependent pre-1970 /
# post-2038 behaviour on some OSes, notably Windows).
_UNIX_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _utc_from_unix_ms(milliseconds: int) -> datetime:
    """UTC datetime from epoch milliseconds via integer timedelta (portable).

    Module-level for test patching of production content-safety paths.
    """
    # timedelta accepts days/seconds/microseconds — keep arithmetic exact.
    # milliseconds may be negative; timedelta handles negative components.
    return _UNIX_EPOCH_UTC + timedelta(milliseconds=milliseconds)


def _require_int64(milliseconds: int) -> None:
    if not isinstance(milliseconds, int) or isinstance(milliseconds, bool):
        raise TypeError("timestamp milliseconds must be int")
    if milliseconds < _INT64_MIN or milliseconds > _INT64_MAX:
        raise_trajectory_error(FATAL_INVALID_INPUT, MSG_TIMESTAMP_OUT_OF_RANGE)


def format_ms(milliseconds: int) -> str:
    """Format epoch milliseconds as ``yyyy-MM-ddTHH:mm:ss.fffZ`` (UTC, invariant).

    Sub-millisecond precision is truncated by conversion to Unix milliseconds
    before this helper is called; this function does not round.

    Uses fixed-epoch + ``timedelta`` arithmetic so pre-1970 and post-2038
    instants are deterministic across platforms (not ``fromtimestamp``).

    Raises:
        TrajectoryError: ``invalid_input`` when the instant is outside the
            representable UTC range (peer: "Timestamp is out of range.").
        TypeError: when ``milliseconds`` is not an ``int``.
    """
    _require_int64(milliseconds)
    # Residual ms for three-digit fractional field (floor toward -inf).
    # divmod(-1, 1000) == (-1, 999) → 1969-12-31T23:59:59.999Z
    _seconds, frac_ms = divmod(milliseconds, 1000)
    # Translate low-level range failures without retaining __context__.
    domain: TrajectoryError | None = None
    dt: Any = None
    try:
        dt = _utc_from_unix_ms(milliseconds)
    except (OverflowError, OSError, ValueError):
        domain = TrajectoryError(FATAL_INVALID_INPUT, MSG_TIMESTAMP_OUT_OF_RANGE)
    if domain is not None:
        raise_trajectory_error(domain.code, domain.message)
    assert dt is not None
    # Explicit fields (not strftime) for invariant zero-padded UTC shape.
    return (
        f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}"
        f"T{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"
        f".{frac_ms:03d}Z"
    )


def format_ms_z(milliseconds: int) -> str:
    """Public message / listing clock: ``...fffZ`` from filled ms (alias of format_ms)."""
    return format_ms(milliseconds)


def format_ms_jsonl(milliseconds: int) -> str:
    """jsonl-minimal body clock: ``yyyy-MM-ddTHH:mm:ss.fff+00:00`` from filled ms only."""
    value = format_ms(milliseconds)
    if value.endswith("Z"):
        return value[:-1] + "+00:00"
    return value


def format_ms_otel_pad(milliseconds: int) -> str:
    """OTEL pad from ms only: ``format_ms`` then ``Z`` → ``0000+00:00``.

    Yields ``yyyy-MM-ddTHH:mm:ss.fff0000+00:00``. Does **not** reformat precise
    strings — callers must prefer precise via :func:`otel_span_time`.
    """
    value = format_ms(milliseconds)
    if value.endswith("Z"):
        return value[:-1] + "0000+00:00"
    return value


def otel_span_time(*, precise: str | None, ms: int | None) -> str:
    """Dual-field OTEL span bound (peer ``precise_record`` / ``precise_invocation``).

    1. If ``precise`` is present (non-None), return it **unchanged** (do not re-pad).
    2. Else require ``ms`` and return :func:`format_ms_otel_pad`.
    3. Missing both → ``TrajectoryError(invalid_input, Source timestamp is unavailable.)``.
    """
    if precise is not None:
        return precise
    if ms is None:
        raise_trajectory_error(FATAL_INVALID_INPUT, MSG_SOURCE_TIMESTAMP_UNAVAILABLE)
    return format_ms_otel_pad(ms)


def clamp_span_ms(start_ms: int, end_ms: int) -> tuple[int, int]:
    """Clamp span bounds using epoch-millisecond values (peer pin).

    If ``end_ms < start_ms``, return ``(start_ms, start_ms)``. Callers format
    after clamping — never compare rendered precise/offset strings for order,
    because source-native precise text may use differing RFC-3339 offsets.
    """
    if not isinstance(start_ms, int) or isinstance(start_ms, bool):
        raise TypeError("start_ms must be int")
    if not isinstance(end_ms, int) or isinstance(end_ms, bool):
        raise TypeError("end_ms must be int")
    if end_ms < start_ms:
        return start_ms, start_ms
    return start_ms, end_ms
