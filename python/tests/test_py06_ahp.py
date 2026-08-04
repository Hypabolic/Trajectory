"""PY-06-ahp unit vectors: Shape A snapshot decode + empty listing stub.

Authority: contracts/spec/sources/ahp.md + docs/python-implementation-spec.md.
Peer pin: .NET AhpJsonSourceAdapter / Rust decode_ahp / TS decodeAhp.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypabolic_trajectory import TrajectorySource
from hypabolic_trajectory.diagnostics import (
    DIAG_AHP_ACTIVE_TURN_OMITTED,
    DIAG_AHP_INPUT_REQUEST_SKIPPED,
    DIAG_AHP_SYSTEM_AS_ASSISTANT,
    DIAG_AHP_UNKNOWN_MESSAGE_ORIGIN,
    DIAG_AHP_UNRESOLVED_CONTENT_REF,
    DIAG_AHP_VERSION_MISSING,
)
from hypabolic_trajectory.dto import SourceContext
from hypabolic_trajectory.errors import FATAL_INVALID_INPUT, TrajectoryError
from hypabolic_trajectory.ir.models import SourceAnchorKind, TrajectoryRole
from hypabolic_trajectory.listing.ahp import AhpTrajectoryLister
from hypabolic_trajectory.listing.common import MSG_INVALID_LIMIT
from hypabolic_trajectory.listing.protocol import (
    clear_listers_for_tests,
    get_lister,
    registered_lister_names,
    register_lister,
)
from hypabolic_trajectory.sources.ahp import (
    AhpSourceAdapter,
    decode_ahp_snapshot,
    is_compatible_ahp_version,
)
from hypabolic_trajectory.sources.protocol import (
    get_source_adapter,
    registered_source_names,
    register_source_adapter,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AHP_CASES = _REPO_ROOT / "conformance" / "cases" / "ahp"
_PIN = (_REPO_ROOT / "conformance" / "vendor" / "ahp" / "PROTOCOL_VERSION").read_text(
    encoding="utf-8"
).strip()


def _bytes(obj: object) -> bytes:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _fixture(case_id: str) -> bytes:
    return (_AHP_CASES / case_id / "input.json").read_bytes()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_ahp_adapter_registers_on_package_import() -> None:
    import hypabolic_trajectory  # noqa: F401

    adapter = get_source_adapter("ahp")
    assert adapter is not None
    assert adapter.source is TrajectorySource.AHP
    assert "ahp" in registered_source_names()


def test_ahp_lister_registers_on_package_import() -> None:
    import hypabolic_trajectory  # noqa: F401

    lister = get_lister("ahp")
    assert lister is not None
    assert lister.source is TrajectorySource.AHP
    assert "ahp" in registered_lister_names()


def test_register_is_idempotent_replace() -> None:
    register_source_adapter(AhpSourceAdapter())
    register_source_adapter(AhpSourceAdapter())
    assert get_source_adapter("ahp") is not None
    register_lister(AhpTrajectoryLister())
    assert get_lister("ahp") is not None


# ---------------------------------------------------------------------------
# Protocol version
# ---------------------------------------------------------------------------


def test_protocol_pin_is_0_7_0() -> None:
    assert _PIN == "0.7.0"
    assert is_compatible_ahp_version(_PIN)
    assert is_compatible_ahp_version("0.7.1")
    assert is_compatible_ahp_version("0.7.0-beta")
    assert is_compatible_ahp_version("0.7")  # major.minor only is still 0.7.x core
    assert not is_compatible_ahp_version("0.6.0")
    assert not is_compatible_ahp_version("1.0.0")
    assert not is_compatible_ahp_version("")
    assert not is_compatible_ahp_version("0.7.")
    assert not is_compatible_ahp_version("0.70.0")


def test_missing_protocol_version_emits_diagnostic() -> None:
    payload = {
        "chat": {
            "resource": "ahp-chat:/g1",
            "turns": [
                {
                    "id": "t1",
                    "startedAt": "2026-03-15T12:00:00.000Z",
                    "message": {"text": "hi", "origin": {"kind": "user"}},
                    "responseParts": [
                        {"kind": "markdown", "id": "m1", "content": "hello"}
                    ],
                }
            ],
        }
    }
    session = decode_ahp_snapshot(_bytes(payload))
    codes = [d.code for d in session.diagnostics]
    assert DIAG_AHP_VERSION_MISSING in codes


def test_incompatible_protocol_version_is_fatal() -> None:
    payload = {
        "ahpProtocolVersion": "0.6.0",
        "chat": {"resource": "ahp-chat:/g1", "turns": []},
    }
    with pytest.raises(TrajectoryError) as ei:
        decode_ahp_snapshot(_bytes(payload))
    assert ei.value.code == FATAL_INVALID_INPUT
    assert "0.6.0" in ei.value.message
    assert "0.7.x" in ei.value.message


def test_non_string_protocol_version_is_fatal() -> None:
    payload = {"ahpProtocolVersion": 7, "chat": {"turns": []}}
    with pytest.raises(TrajectoryError) as ei:
        decode_ahp_snapshot(_bytes(payload))
    assert ei.value.code == FATAL_INVALID_INPUT
    assert "must be a string" in ei.value.message


# ---------------------------------------------------------------------------
# Invalid containers
# ---------------------------------------------------------------------------


def test_invalid_json_is_fatal() -> None:
    with pytest.raises(TrajectoryError) as ei:
        decode_ahp_snapshot(b"not-json")
    assert ei.value.code == FATAL_INVALID_INPUT
    assert "Shape A" in ei.value.message


def test_root_array_is_fatal() -> None:
    with pytest.raises(TrajectoryError) as ei:
        decode_ahp_snapshot(b"[]")
    assert ei.value.code == FATAL_INVALID_INPUT


def test_missing_chat_is_fatal() -> None:
    with pytest.raises(TrajectoryError) as ei:
        decode_ahp_snapshot(_bytes({"ahpProtocolVersion": "0.7.0"}))
    assert ei.value.code == FATAL_INVALID_INPUT


def test_non_utf8_is_fatal() -> None:
    with pytest.raises(TrajectoryError) as ei:
        decode_ahp_snapshot(b"\xff\xfe")
    assert ei.value.code == FATAL_INVALID_INPUT


def test_nan_and_infinity_json_constants_are_fatal() -> None:
    for body in (b'{"chat":{"x":NaN}}', b'{"chat":{"x":Infinity}}', b'{"chat":{"x":-Infinity}}'):
        with pytest.raises(TrajectoryError) as ei:
            decode_ahp_snapshot(body)
        assert ei.value.code == FATAL_INVALID_INPUT
        assert "Shape A" in ei.value.message


def test_json_recursion_error_is_domain_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import hypabolic_trajectory.sources.ahp as ahp_mod

    def _boom(*_a: object, **_k: object) -> object:
        raise RecursionError("simulated deep JSON")

    monkeypatch.setattr(ahp_mod.json, "loads", _boom)
    with pytest.raises(TrajectoryError) as ei:
        decode_ahp_snapshot(b'{"chat":{}}')
    assert ei.value.code == FATAL_INVALID_INPUT
    assert ei.value.__cause__ is None


def test_turn_id_with_lone_surrogate_sorts_without_raise() -> None:
    # Escaped lone surrogate in JSON id (not encodable as strict UTF-8).
    body = (
        b'{"ahpProtocolVersion":"0.7.0","chat":{"turns":['
        b'{"id":"\\uD800","message":{"text":"a","origin":{"kind":"user"}},'
        b'"responseParts":[{"kind":"markdown","id":"m","content":"x"}]},'
        b'{"id":"normal","message":{"text":"b","origin":{"kind":"user"}},'
        b'"responseParts":[{"kind":"markdown","id":"m","content":"y"}]}'
        b"]}}"
    )
    session = decode_ahp_snapshot(body)
    assert len(session.events) >= 2


def test_unicode_digit_version_rejected() -> None:
    # Arabic-Indic digit in patch component — peers require [0-9] ASCII only.
    assert not is_compatible_ahp_version("0.7.\u0660")
    payload = {
        "ahpProtocolVersion": "0.7.\u0660",
        "chat": {"turns": []},
    }
    with pytest.raises(TrajectoryError) as ei:
        decode_ahp_snapshot(_bytes(payload))
    assert ei.value.code == FATAL_INVALID_INPUT


def test_transcript_must_be_bytes() -> None:
    with pytest.raises(TypeError):
        decode_ahp_snapshot("{ }")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Conformance fixtures (decode-level unit vectors)
# ---------------------------------------------------------------------------


def test_tool_calls_fixture_decode() -> None:
    session = decode_ahp_snapshot(_fixture("tool-calls"))
    assert session.source is TrajectorySource.AHP
    assert session.source_name == "ahp"
    assert session.group_id == "ahp-chat:/00000000-0000-4000-8000-0000000000a1"
    assert session.group_resolved is True
    assert session.cwd == "/workspace/demo"
    assert session.model == "synthetic-model-1"
    assert session.created_at_ms is not None
    assert session.created_at_precise == "2026-03-15T12:00:00.000Z"
    assert session.diagnostics == ()

    kinds = [e.kind for e in session.events]
    # user message, markdown, tool-call, tool-result, trailing markdown
    assert kinds == ["message", "message", "tool-call", "tool-result", "message"]

    user = session.events[0]
    assert user.role is TrajectoryRole.USER
    assert user.content == "Check the current directory."
    assert user.native_record_id == "turn-00000000-0000-4000-8000-0000000000t1"
    assert user.source_sequence == 0
    assert user.source_offset == 0
    assert user.source_anchor_kind is SourceAnchorKind.BYTE
    assert user.component_index == 0
    assert user.model == "synthetic-model-1"

    md1 = session.events[1]
    assert md1.role is TrajectoryRole.ASSISTANT
    assert md1.content == "I will inspect the working directory."
    assert md1.native_record_id == "part-md-1"
    assert md1.component_index == 1

    call = session.events[2]
    assert call.kind == "tool-call"
    assert call.tool_call_id == "tc-00000000-0000-4000-8000-0000000000c1"
    assert call.tool_name == "terminal"
    assert call.arguments_json == '{"command":"pwd"}'
    assert call.component_index == 2

    result = session.events[3]
    assert result.kind == "tool-result"
    assert result.role is TrajectoryRole.TOOL
    assert result.content == "/workspace/demo"
    assert result.is_error is False
    assert result.component_index == 3

    md2 = session.events[4]
    assert md2.content == "You are in `/workspace/demo`."
    assert md2.native_record_id == "part-md-2"
    assert md2.component_index == 4

    assert len(session.model_invocations) == 1
    inv = session.model_invocations[0]
    assert inv.native_record_id == "turn-00000000-0000-4000-8000-0000000000t1"
    assert inv.provider == "synthetic-provider"
    assert inv.requested_model == "synthetic-model-1"
    assert inv.response_model == "synthetic-model-1"
    assert inv.input_tokens == 120
    assert inv.output_tokens == 40


def test_multi_turn_fixture_ordering() -> None:
    session = decode_ahp_snapshot(_fixture("multi-turn"))
    assert session.group_id == "ahp-chat:/00000000-0000-4000-8000-0000000000a2"
    assert session.diagnostics == ()
    # Two turns: user+assistant each.
    assert [e.kind for e in session.events] == [
        "message",
        "message",
        "message",
        "message",
    ]
    assert session.events[0].content == "What is 2 + 2?"
    assert session.events[1].content == "2 + 2 equals 4."
    assert session.events[2].content == "And 3 + 5?"
    assert session.events[3].content.startswith("3 + 5 equals 8.")
    assert len(session.model_invocations) == 2
    assert session.model_invocations[0].native_record_id is not None
    assert session.model_invocations[1].native_record_id is not None
    assert (
        session.model_invocations[0].native_record_id
        != session.model_invocations[1].native_record_id
    )


def test_cancelled_turn_fixture_no_invented_success() -> None:
    session = decode_ahp_snapshot(_fixture("cancelled-turn"))
    assert session.diagnostics == ()
    kinds = [e.kind for e in session.events]
    assert kinds == ["message", "message", "tool-call", "tool-result"]
    result = session.events[3]
    assert result.kind == "tool-result"
    assert result.is_error is True
    assert result.content == "User denied the tool call"


# ---------------------------------------------------------------------------
# Mapping edge cases
# ---------------------------------------------------------------------------


def test_contiguous_markdown_concatenated() -> None:
    payload = {
        "ahpProtocolVersion": "0.7.0",
        "chat": {
            "resource": "ahp-chat:/g",
            "turns": [
                {
                    "id": "t1",
                    "startedAt": "2026-03-15T12:00:00.000Z",
                    "message": {"text": "hi", "origin": {"kind": "user"}},
                    "responseParts": [
                        {"kind": "markdown", "id": "a", "content": "Hello "},
                        {"kind": "markdown", "id": "b", "content": "world"},
                    ],
                }
            ],
        },
    }
    session = decode_ahp_snapshot(_bytes(payload))
    assistant = [e for e in session.events if e.role is TrajectoryRole.ASSISTANT]
    assert len(assistant) == 1
    assert assistant[0].content == "Hello world"
    assert assistant[0].native_record_id == "a"


def test_reasoning_whitespace_dropped_without_diagnostic() -> None:
    payload = {
        "ahpProtocolVersion": "0.7.0",
        "chat": {
            "turns": [
                {
                    "id": "t1",
                    "message": {"text": "hi", "origin": {"kind": "user"}},
                    "responseParts": [
                        {"kind": "reasoning", "id": "r1", "content": "   \n"},
                        {"kind": "reasoning", "id": "r2", "content": "think"},
                        {"kind": "markdown", "id": "m1", "content": "ok"},
                    ],
                }
            ],
        },
    }
    session = decode_ahp_snapshot(_bytes(payload))
    reasoning = [e for e in session.events if e.kind == "reasoning"]
    assert len(reasoning) == 1
    assert reasoning[0].content == "think"
    assert reasoning[0].role is TrajectoryRole.REASONING
    assert all(d.code != "ahp_reasoning_omitted" for d in session.diagnostics)


def test_active_turn_whole_mode_omitted() -> None:
    payload = {
        "ahpProtocolVersion": "0.7.0",
        "chat": {
            "turns": [
                {
                    "id": "t1",
                    "startedAt": "2026-03-15T12:00:00.000Z",
                    "message": {"text": "hi", "origin": {"kind": "user"}},
                    "responseParts": [
                        {"kind": "markdown", "id": "m1", "content": "ok"}
                    ],
                }
            ],
            "activeTurn": {
                "id": "t-active",
                "startedAt": "2026-03-15T12:01:00.000Z",
                "message": {"text": "more", "origin": {"kind": "user"}},
            },
        },
    }
    session = decode_ahp_snapshot(_bytes(payload), partial=False)
    codes = [d.code for d in session.diagnostics]
    assert DIAG_AHP_ACTIVE_TURN_OMITTED in codes
    assert all(e.native_record_id != "t-active" for e in session.events)


def test_active_turn_partial_mode_appended() -> None:
    payload = {
        "ahpProtocolVersion": "0.7.0",
        "chat": {
            "turns": [
                {
                    "id": "t1",
                    "startedAt": "2026-03-15T12:00:00.000Z",
                    "message": {"text": "hi", "origin": {"kind": "user"}},
                    "responseParts": [
                        {"kind": "markdown", "id": "m1", "content": "ok"}
                    ],
                }
            ],
            "activeTurn": {
                "id": "t-active",
                "startedAt": "2026-03-15T12:01:00.000Z",
                "message": {"text": "more", "origin": {"kind": "user"}},
            },
        },
    }
    session = decode_ahp_snapshot(_bytes(payload), partial=True)
    assert all(d.code != DIAG_AHP_ACTIVE_TURN_OMITTED for d in session.diagnostics)
    assert any(e.native_record_id == "t-active" for e in session.events)
    # Active turn after completed turns (not re-sorted into middle).
    ids = [e.native_record_id for e in session.events]
    assert ids.index("t-active") > ids.index("t1")


def test_partial_via_base_byte_offset_on_adapter() -> None:
    payload = {
        "ahpProtocolVersion": "0.7.0",
        "chat": {
            "turns": [],
            "activeTurn": {
                "id": "t-active",
                "message": {"text": "more", "origin": {"kind": "user"}},
            },
        },
    }
    adapter = AhpSourceAdapter()
    for offset in (1, -1):
        session = adapter.decode(
            _bytes(payload),
            source_context=SourceContext(base_byte_offset=offset),
        )
        assert any(
            e.native_record_id == "t-active" for e in session.events
        ), f"offset={offset}"


def test_turn_sort_nulls_last_then_utf8_id() -> None:
    payload = {
        "ahpProtocolVersion": "0.7.0",
        "chat": {
            "turns": [
                {
                    "id": "b",
                    "startedAt": "2026-03-15T12:00:10.000Z",
                    "message": {"text": "second", "origin": {"kind": "user"}},
                    "responseParts": [
                        {"kind": "markdown", "id": "m", "content": "x"}
                    ],
                },
                {
                    "id": "a",
                    "startedAt": "2026-03-15T12:00:00.000Z",
                    "message": {"text": "first", "origin": {"kind": "user"}},
                    "responseParts": [
                        {"kind": "markdown", "id": "m", "content": "x"}
                    ],
                },
                {
                    "id": "z-no-ts",
                    "message": {"text": "nulls last", "origin": {"kind": "user"}},
                    "responseParts": [
                        {"kind": "markdown", "id": "m", "content": "x"}
                    ],
                },
                {
                    "id": "m-no-ts",
                    "message": {"text": "null mid", "origin": {"kind": "user"}},
                    "responseParts": [
                        {"kind": "markdown", "id": "m", "content": "x"}
                    ],
                },
            ],
        },
    }
    session = decode_ahp_snapshot(_bytes(payload))
    user_ids = [
        e.native_record_id
        for e in session.events
        if e.role is TrajectoryRole.USER
    ]
    assert user_ids == ["a", "b", "m-no-ts", "z-no-ts"]


def test_unknown_origin_and_system_mapping() -> None:
    payload = {
        "ahpProtocolVersion": "0.7.0",
        "chat": {
            "turns": [
                {
                    "id": "t1",
                    "message": {"text": "sys", "origin": {"kind": "systemNotification"}},
                    "responseParts": [],
                },
                {
                    "id": "t2",
                    "message": {"text": "weird", "origin": {"kind": "robot"}},
                    "responseParts": [],
                },
                {
                    "id": "t3",
                    "message": {"text": "tool-only", "origin": {"kind": "tool"}},
                    "responseParts": [],
                },
            ],
        },
    }
    session = decode_ahp_snapshot(_bytes(payload))
    codes = {d.code for d in session.diagnostics}
    assert DIAG_AHP_SYSTEM_AS_ASSISTANT in codes
    assert DIAG_AHP_UNKNOWN_MESSAGE_ORIGIN in codes
    # system mapped to assistant; robot dropped; tool origin emits no free-standing message
    assert [e.role for e in session.events] == [TrajectoryRole.ASSISTANT]
    assert session.events[0].content == "sys"
    # Fixed diagnostic message — never echoes free-form origin kind.
    for d in session.diagnostics:
        if d.code == DIAG_AHP_UNKNOWN_MESSAGE_ORIGIN:
            assert "robot" not in d.message


def test_resource_and_input_request_diagnostics() -> None:
    payload = {
        "ahpProtocolVersion": "0.7.0",
        "chat": {
            "turns": [
                {
                    "id": "t1",
                    "message": {"text": "hi", "origin": {"kind": "user"}},
                    "responseParts": [
                        {"kind": "inputRequest", "id": "ir1"},
                        {"kind": "resource", "id": "res1"},
                        {"kind": "markdown", "id": "m1", "content": "done"},
                    ],
                }
            ],
        },
    }
    session = decode_ahp_snapshot(_bytes(payload))
    codes = {d.code for d in session.diagnostics}
    assert DIAG_AHP_INPUT_REQUEST_SKIPPED in codes
    assert DIAG_AHP_UNRESOLVED_CONTENT_REF in codes
    assert any(e.content == "done" for e in session.events)


def test_tool_parameters_prefer_structured_over_tool_input() -> None:
    payload = {
        "ahpProtocolVersion": "0.7.0",
        "chat": {
            "turns": [
                {
                    "id": "t1",
                    "message": {"text": "hi", "origin": {"kind": "user"}},
                    "responseParts": [
                        {
                            "kind": "toolCall",
                            "toolCall": {
                                "toolCallId": "tc1",
                                "toolName": "calc",
                                "parameters": {"x": 1, "y": 2},
                                "toolInput": '{"ignored":true}',
                                "status": "completed",
                                "success": True,
                            },
                        }
                    ],
                }
            ],
        },
    }
    session = decode_ahp_snapshot(_bytes(payload))
    call = next(e for e in session.events if e.kind == "tool-call")
    assert call.arguments_json == '{"x":1,"y":2}'


def test_structured_content_uses_canonical_json() -> None:
    payload = {
        "ahpProtocolVersion": "0.7.0",
        "chat": {
            "turns": [
                {
                    "id": "t1",
                    "message": {"text": "hi", "origin": {"kind": "user"}},
                    "responseParts": [
                        {
                            "kind": "toolCall",
                            "toolCall": {
                                "toolCallId": "tc1",
                                "toolName": "meta",
                                "parameters": {},
                                "status": "completed",
                                "success": True,
                                "structuredContent": {"b": 2, "a": 1},
                            },
                        }
                    ],
                }
            ],
        },
    }
    session = decode_ahp_snapshot(_bytes(payload))
    result = next(e for e in session.events if e.kind == "tool-result")
    # Keys sorted by UTF-16 (ASCII same as byte order here).
    assert result.content == '{"a":1,"b":2}'


def test_string_or_markdown_reason_message() -> None:
    payload = {
        "ahpProtocolVersion": "0.7.0",
        "chat": {
            "turns": [
                {
                    "id": "t1",
                    "message": {"text": "hi", "origin": {"kind": "user"}},
                    "responseParts": [
                        {
                            "kind": "toolCall",
                            "toolCall": {
                                "toolCallId": "tc1",
                                "toolName": "x",
                                "status": "denied",
                                "reasonMessage": {"markdown": "No **way**"},
                            },
                        }
                    ],
                }
            ],
        },
    }
    session = decode_ahp_snapshot(_bytes(payload))
    result = next(e for e in session.events if e.kind == "tool-result")
    assert result.is_error is True
    assert result.content == "No **way**"


def test_cancelled_fallback_content() -> None:
    payload = {
        "ahpProtocolVersion": "0.7.0",
        "chat": {
            "turns": [
                {
                    "id": "t1",
                    "message": {"text": "hi", "origin": {"kind": "user"}},
                    "responseParts": [
                        {
                            "kind": "toolCall",
                            "toolCall": {
                                "toolCallId": "tc1",
                                "toolName": "x",
                                "status": "cancelled",
                            },
                        }
                    ],
                }
            ],
        },
    }
    session = decode_ahp_snapshot(_bytes(payload))
    result = next(e for e in session.events if e.kind == "tool-result")
    assert result.is_error is True
    assert result.content == "cancelled"


def test_token_counts_reject_fractional_and_non_finite() -> None:
    payload = {
        "ahpProtocolVersion": "0.7.0",
        "chat": {
            "turns": [
                {
                    "id": "t1",
                    "message": {"text": "hi", "origin": {"kind": "user"}},
                    "responseParts": [
                        {"kind": "markdown", "id": "m", "content": "ok"}
                    ],
                    "usage": {
                        "inputTokens": 1.5,
                        "outputTokens": 2.0,
                        "cacheReadTokens": 3,
                    },
                }
            ],
        },
    }
    session = decode_ahp_snapshot(_bytes(payload))
    inv = session.model_invocations[0]
    assert inv.input_tokens is None  # fractional rejected
    assert inv.output_tokens == 2  # lossless whole float accepted
    assert inv.cache_read_tokens == 3


def test_deeply_nested_parameters_is_domain_error() -> None:
    # Valid JSON tree deep enough that compact_json recursion fails.
    # Build nested JSON text iteratively — json.dumps of a 1200-deep Python
    # dict hits RecursionError on CPython 3.11 before the decoder runs.
    nested = "{}"
    for _ in range(1_200):
        nested = '{"n":' + nested + "}"
    raw = (
        '{"ahpProtocolVersion":"0.7.0","chat":{"turns":[{'
        '"id":"t1",'
        '"message":{"text":"hi","origin":{"kind":"user"}},'
        '"responseParts":[{'
        '"kind":"toolCall",'
        '"toolCall":{'
        '"toolCallId":"tc1",'
        '"toolName":"x",'
        f'"parameters":{nested},'
        '"status":"completed",'
        '"success":true'
        "}}]}]}}"
    )
    with pytest.raises(TrajectoryError) as ei:
        decode_ahp_snapshot(raw.encode("utf-8"))
    assert ei.value.code == FATAL_INVALID_INPUT
    assert ei.value.__cause__ is None


def test_parameters_int64_overflow_is_domain_error() -> None:
    huge = 2**63  # one past signed int64 max
    payload = {
        "ahpProtocolVersion": "0.7.0",
        "chat": {
            "turns": [
                {
                    "id": "t1",
                    "message": {"text": "hi", "origin": {"kind": "user"}},
                    "responseParts": [
                        {
                            "kind": "toolCall",
                            "toolCall": {
                                "toolCallId": "tc1",
                                "toolName": "x",
                                "parameters": {"n": huge},
                                "status": "completed",
                                "success": True,
                            },
                        }
                    ],
                }
            ],
        },
    }
    with pytest.raises(TrajectoryError) as ei:
        decode_ahp_snapshot(_bytes(payload))
    assert ei.value.code == FATAL_INVALID_INPUT
    assert ei.value.__cause__ is None


def test_structured_content_int64_overflow_is_domain_error() -> None:
    huge = 2**63
    payload = {
        "ahpProtocolVersion": "0.7.0",
        "chat": {
            "turns": [
                {
                    "id": "t1",
                    "message": {"text": "hi", "origin": {"kind": "user"}},
                    "responseParts": [
                        {
                            "kind": "toolCall",
                            "toolCall": {
                                "toolCallId": "tc1",
                                "toolName": "x",
                                "status": "completed",
                                "success": True,
                                "structuredContent": {"n": huge},
                            },
                        }
                    ],
                }
            ],
        },
    }
    with pytest.raises(TrajectoryError) as ei:
        decode_ahp_snapshot(_bytes(payload))
    assert ei.value.code == FATAL_INVALID_INPUT


def test_group_resolved_false_without_resource() -> None:
    payload = {
        "ahpProtocolVersion": "0.7.0",
        "chat": {
            "turns": [
                {
                    "id": "t1",
                    "message": {"text": "hi", "origin": {"kind": "user"}},
                    "responseParts": [
                        {"kind": "markdown", "id": "m", "content": "ok"}
                    ],
                }
            ],
        },
    }
    session = decode_ahp_snapshot(_bytes(payload))
    assert session.group_id is None
    assert session.group_resolved is False


def test_cwd_from_session_when_chat_missing() -> None:
    payload = {
        "ahpProtocolVersion": "0.7.0",
        "chat": {"turns": []},
        "session": {"workingDirectories": ["file:///tmp/work"]},
    }
    session = decode_ahp_snapshot(_bytes(payload))
    assert session.cwd == "/tmp/work"


def test_empty_message_text_still_exposes_turn_model() -> None:
    payload = {
        "ahpProtocolVersion": "0.7.0",
        "chat": {
            "turns": [
                {
                    "id": "t1",
                    "message": {
                        "text": "",
                        "origin": {"kind": "user"},
                        "model": {"id": "m-from-empty"},
                    },
                    "responseParts": [
                        {"kind": "markdown", "id": "m", "content": "ok"}
                    ],
                    "usage": {"inputTokens": 1, "outputTokens": 1},
                }
            ],
        },
    }
    session = decode_ahp_snapshot(_bytes(payload))
    assert session.model == "m-from-empty"
    assert session.model_invocations[0].requested_model == "m-from-empty"
    # No user message event for empty text.
    assert not any(e.role is TrajectoryRole.USER for e in session.events)


# ---------------------------------------------------------------------------
# Empty listing stub
# ---------------------------------------------------------------------------


def test_empty_listing_stub_any_root() -> None:
    lister = AhpTrajectoryLister()
    page = lister.list_page(root="/tmp/does-not-exist", cursor=None, limit=50)
    assert page.items == ()
    assert page.next_cursor is None

    page2 = lister.list_page(root=Path("/any"), cursor="ignored", limit=1)
    assert page2.items == ()
    assert page2.next_cursor is None


def test_empty_listing_validates_limit() -> None:
    lister = AhpTrajectoryLister()
    with pytest.raises(TrajectoryError) as ei:
        lister.list_page(root="/tmp", cursor=None, limit=0)
    assert ei.value.code == FATAL_INVALID_INPUT
    assert ei.value.message == MSG_INVALID_LIMIT


def test_listing_registry_snapshot_restore() -> None:
    """Isolation: mutate registry only via snapshot/restore (not delete-only)."""
    snapshot = {name: get_lister(name) for name in registered_lister_names()}
    try:
        clear_listers_for_tests()
        assert "ahp" not in registered_lister_names()
        register_lister(AhpTrajectoryLister())
        assert get_lister("ahp") is not None
    finally:
        clear_listers_for_tests()
        for name, lister in snapshot.items():
            if lister is not None:
                register_lister(lister)
    assert "ahp" in registered_lister_names()
