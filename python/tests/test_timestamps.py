"""PY-03 unit: format_ms, jsonl +00:00, OTEL Z→0000+00:00 pad, dual-field helpers."""

from __future__ import annotations

import pytest

from hypabolic_trajectory import TrajectoryError
from hypabolic_trajectory.timestamps import (
    clamp_span_ms,
    format_ms,
    format_ms_jsonl,
    format_ms_otel_pad,
    format_ms_z,
    otel_span_time,
)


def test_format_ms_epoch() -> None:
    assert format_ms(0) == "1970-01-01T00:00:00.000Z"
    assert format_ms(1) == "1970-01-01T00:00:00.001Z"
    assert format_ms(999) == "1970-01-01T00:00:00.999Z"
    assert format_ms(1000) == "1970-01-01T00:00:01.000Z"


def test_format_ms_negative_and_pre1970() -> None:
    """Pre-1970 must be portable (no fromtimestamp platform gaps)."""
    assert format_ms(-1) == "1969-12-31T23:59:59.999Z"
    assert format_ms(-1000) == "1969-12-31T23:59:59.000Z"
    # 1969-12-31T00:00:00.000Z = -86400000 ms
    assert format_ms(-86_400_000) == "1969-12-31T00:00:00.000Z"
    # 1900-01-01T00:00:00.000Z (well before 1970; Windows fromtimestamp gap)
    assert format_ms(-2_208_988_800_000) == "1900-01-01T00:00:00.000Z"


def test_format_ms_post_2038() -> None:
    """Post-Y2038 instants must format on 32-bit-era platforms too."""
    # 2100-01-01T00:00:00.000Z
    assert format_ms(4_102_444_800_000) == "2100-01-01T00:00:00.000Z"


def test_format_ms_known_instant() -> None:
    # 2023-11-14T22:13:20.000Z
    assert format_ms(1_700_000_000_000) == "2023-11-14T22:13:20.000Z"
    # with fractional ms
    assert format_ms(1_700_000_000_123) == "2023-11-14T22:13:20.123Z"


def test_format_ms_z_alias() -> None:
    assert format_ms_z(0) == format_ms(0)


def test_format_ms_jsonl_replaces_z_with_plus00() -> None:
    assert format_ms_jsonl(0) == "1970-01-01T00:00:00.000+00:00"
    assert format_ms_jsonl(1_700_000_000_123) == "2023-11-14T22:13:20.123+00:00"
    # three fractional digits only — not seven
    assert format_ms_jsonl(0).count(".") == 1
    assert format_ms_jsonl(0).endswith("+00:00")
    assert "0000+00:00" not in format_ms_jsonl(0)


def test_format_ms_otel_pad_z_to_seven_digit() -> None:
    """Peer formula: format_ms then replace trailing Z with 0000+00:00."""
    assert format_ms_otel_pad(0) == "1970-01-01T00:00:00.0000000+00:00"
    assert format_ms_otel_pad(1) == "1970-01-01T00:00:00.0010000+00:00"
    assert format_ms_otel_pad(1_700_000_000_123) == "2023-11-14T22:13:20.1230000+00:00"
    # Shape: three digits from ms + four pad zeros before +00:00
    assert format_ms_otel_pad(0).endswith("0000000+00:00")


def test_otel_span_time_prefers_precise_unchanged() -> None:
    precise = "2024-06-01T12:34:56.7890123+00:00"
    assert (
        otel_span_time(precise=precise, ms=0) == precise
    ), "precise must not be re-padded"
    # Even weird precise text is returned as-is (peer: use unchanged).
    weird = "not-a-real-timestamp-but-source-native"
    assert otel_span_time(precise=weird, ms=None) == weird


def test_otel_span_time_pads_from_ms_when_precise_absent() -> None:
    assert otel_span_time(precise=None, ms=0) == "1970-01-01T00:00:00.0000000+00:00"
    assert (
        otel_span_time(precise=None, ms=1_700_000_000_123)
        == "2023-11-14T22:13:20.1230000+00:00"
    )


def test_otel_span_time_missing_both_raises() -> None:
    with pytest.raises(TrajectoryError) as excinfo:
        otel_span_time(precise=None, ms=None)
    err = excinfo.value
    assert err.code == "invalid_input"
    assert err.message == "Source timestamp is unavailable."
    assert err.__cause__ is None
    assert err.__context__ is None


def test_format_ms_out_of_range() -> None:
    with pytest.raises(TrajectoryError) as excinfo:
        format_ms(2**63)  # beyond signed int64
    assert excinfo.value.code == "invalid_input"
    assert excinfo.value.message == "Timestamp is out of range."
    assert excinfo.value.__cause__ is None

    with pytest.raises(TrajectoryError) as excinfo:
        format_ms(-(2**63) - 1)
    assert excinfo.value.code == "invalid_input"


def test_format_ms_rejects_bool_and_non_int() -> None:
    with pytest.raises(TypeError):
        format_ms(True)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        format_ms(1.5)  # type: ignore[arg-type]


def test_clamp_span_ms() -> None:
    start = 1_700_000_000_000
    earlier = 1_600_000_000_000
    later = 1_800_000_000_000
    assert clamp_span_ms(start, later) == (start, later)
    assert clamp_span_ms(start, earlier) == (start, start)
    assert clamp_span_ms(start, start) == (start, start)
    # Numeric clamp is independent of how the same instants would render under
    # differing RFC-3339 offsets (peer compares ms before formatting).
    assert clamp_span_ms(0, -1) == (0, 0)
