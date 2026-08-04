"""PY-02 unit vectors: canonical JSON, shared escape, identity digests."""

from __future__ import annotations

import math

import pytest

from hypabolic_trajectory import canonical_json
from hypabolic_trajectory.canonical import (
    compact_json,
    escape_json_string,
    relaxed_json,
    utf16_code_units,
    utf16_compare,
)
from hypabolic_trajectory.identity import (
    model_invocation_id,
    record_id,
    sha256_hex,
    trajectory_id,
)


# ---------------------------------------------------------------------------
# Golden bytes: Rust tip vector (canonical.rs sorts_utf16_and_uses_contract_escaping)
# ---------------------------------------------------------------------------

RUST_GOLDEN = r'{"\uD800\uDC00":"\uD83D\uDE00","\uE000":"\u2028"}'


def test_rust_unicode_surrogate_and_private_use_golden() -> None:
    # Key U+10000 (supplementary → surrogate pair D800 DC00), value 😀 (U+1F600).
    # Key U+E000 (BMP private-use), value U+2028 (line separator).
    value = {
        "\U00010000": "\U0001f600",
        "\ue000": "\u2028",
    }
    assert canonical_json(value) == RUST_GOLDEN
    # Insertion order already matches UTF-16 sort for this map, so relaxed matches.
    assert compact_json(value) == RUST_GOLDEN
    assert relaxed_json(value) == RUST_GOLDEN


def test_utf16_key_sort_differs_from_unicode_scalar_order() -> None:
    # U+10000 sorts before U+E000 in UTF-16 (D800… < E000) even though
    # scalar order has U+E000 < U+10000.
    value = {
        "\ue000": 1,
        "\U00010000": 2,
    }
    # Insertion order is private-use first; canonical must re-order.
    assert compact_json(value) == r'{"\uE000":1,"\uD800\uDC00":2}'
    assert canonical_json(value) == r'{"\uD800\uDC00":2,"\uE000":1}'


# ---------------------------------------------------------------------------
# Shared escape algorithm unit vectors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", '""'),
        ("hello", '"hello"'),
        ('a"b', r'"a\"b"'),
        ("a\\b", r'"a\\b"'),
        ("a/b", '"a/b"'),  # solidus not escaped
        ("a\bb", r'"a\bb"'),
        ("a\tb", r'"a\tb"'),
        ("a\nb", r'"a\nb"'),
        ("a\fb", r'"a\fb"'),
        ("a\rb", r'"a\rb"'),
        ("a\x00b", r'"a\u0000b"'),
        ("a\x1fb", r'"a\u001Fb"'),
        ("\u2028", r'"\u2028"'),
        ("\u2029", r'"\u2029"'),
        ("\ue000", r'"\uE000"'),
        ("\uf8ff", r'"\uF8FF"'),
        ("😀", r'"\uD83D\uDE00"'),
        ("\U00010000", r'"\uD800\uDC00"'),
    ],
)
def test_escape_json_string_vectors(raw: str, expected: str) -> None:
    assert escape_json_string(raw) == expected


def test_escape_does_not_normalize_unicode() -> None:
    # Combining sequence e + combining acute must not NFC-collapse.
    decomposed = "e\u0301"
    composed = "\u00e9"
    assert decomposed != composed
    assert escape_json_string(decomposed) == '"e\u0301"'
    assert escape_json_string(composed) == '"\u00e9"'
    assert escape_json_string(decomposed) != escape_json_string(composed)


# ---------------------------------------------------------------------------
# Primitives / structure / TypeError policy
# ---------------------------------------------------------------------------


def test_null_bool_number_array_object() -> None:
    assert canonical_json(None) == "null"
    assert canonical_json(True) == "true"
    assert canonical_json(False) == "false"
    assert canonical_json(0) == "0"
    assert canonical_json(-1) == "-1"
    assert canonical_json(9223372036854775807) == "9223372036854775807"
    assert canonical_json(-9223372036854775808) == "-9223372036854775808"
    assert canonical_json([]) == "[]"
    assert canonical_json([1, "a", None]) == '[1,"a",null]'
    assert canonical_json({}) == "{}"
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_bool_not_emitted_as_int() -> None:
    assert canonical_json(True) == "true"
    assert canonical_json([True, 1]) == "[true,1]"


def test_compact_preserves_insertion_order() -> None:
    value = {"z": 1, "a": 2}
    assert compact_json(value) == '{"z":1,"a":2}'
    assert canonical_json(value) == '{"a":2,"z":1}'


def test_array_compact_equals_canonical() -> None:
    arr = ["pi", "unicode-session"]
    assert compact_json(arr) == canonical_json(arr) == '["pi","unicode-session"]'


def test_no_insignificant_whitespace_or_bom() -> None:
    out = canonical_json({"a": [1, 2], "b": {"c": None}})
    assert " " not in out
    assert "\n" not in out
    assert not out.startswith("\ufeff")


def test_typeerror_on_non_finite_float() -> None:
    with pytest.raises(TypeError):
        canonical_json(math.nan)
    with pytest.raises(TypeError):
        canonical_json(math.inf)
    with pytest.raises(TypeError):
        canonical_json(-math.inf)
    with pytest.raises(TypeError):
        compact_json(math.nan)


def test_typeerror_on_invalid_trees() -> None:
    from collections import UserDict

    with pytest.raises(TypeError):
        canonical_json(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        canonical_json(b"bytes")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        canonical_json({1: "bad"})  # type: ignore[dict-item]
    # JsonValue arrays/objects are list/dict only — reject other containers.
    with pytest.raises(TypeError):
        canonical_json((1, 2))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        canonical_json(range(2))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        canonical_json(UserDict({"a": 1}))  # type: ignore[arg-type]


def test_int64_bounds() -> None:
    assert canonical_json(9223372036854775807) == "9223372036854775807"
    assert canonical_json(-9223372036854775808) == "-9223372036854775808"
    with pytest.raises(TypeError):
        canonical_json(9223372036854775808)
    with pytest.raises(TypeError):
        canonical_json(-9223372036854775809)
    with pytest.raises(TypeError):
        compact_json([9223372036854775808])  # type: ignore[list-item]


def test_typeerror_on_cyclic_trees() -> None:
    cyclic_list: list[object] = []
    cyclic_list.append(cyclic_list)
    with pytest.raises(TypeError, match="cyclic"):
        canonical_json(cyclic_list)  # type: ignore[arg-type]

    cyclic_dict: dict[str, object] = {}
    cyclic_dict["self"] = cyclic_dict
    with pytest.raises(TypeError, match="cyclic"):
        canonical_json(cyclic_dict)  # type: ignore[arg-type]

    # Shared non-cyclic subtrees must still work (diamond, not cycle).
    shared = {"x": 1}
    diamond = {"a": shared, "b": shared}
    assert canonical_json(diamond) == '{"a":{"x":1},"b":{"x":1}}'


def test_rejects_int_subclass() -> None:
    class EvilInt(int):
        def __str__(self) -> str:  # pragma: no cover - must not be called
            return "null"

    with pytest.raises(TypeError):
        canonical_json(EvilInt(1))  # type: ignore[arg-type]


def test_runtime_type_hints_resolve() -> None:
    import typing

    from hypabolic_trajectory.canonical import compact_json as cj

    hints = typing.get_type_hints(canonical_json)
    assert "value" in hints
    assert "return" in hints
    hints2 = typing.get_type_hints(cj)
    assert "value" in hints2


def test_finite_float_emits() -> None:
    # Finite floats are accepted; exact text is platform JSON-compatible.
    out = canonical_json(1.5)
    assert out == "1.5"


# ---------------------------------------------------------------------------
# UTF-16 helpers
# ---------------------------------------------------------------------------


def test_utf16_code_units_surrogate_pair() -> None:
    assert utf16_code_units("😀") == [0xD83D, 0xDE00]
    assert utf16_code_units("\ue000") == [0xE000]
    assert utf16_code_units("A") == [0x0041]


def test_utf16_compare_matches_js_ordinal() -> None:
    assert utf16_compare("a", "b") < 0
    assert utf16_compare("b", "a") > 0
    assert utf16_compare("a", "a") == 0
    # Surrogate pair key vs private-use: D800… < E000
    assert utf16_compare("\U00010000", "\ue000") < 0
    assert utf16_compare("\ue000", "\U00010000") > 0


# ---------------------------------------------------------------------------
# Identity digests
# ---------------------------------------------------------------------------


def test_trajectory_id_pi_unicode_session_golden() -> None:
    # From conformance/cases/pi/unicode-boundaries/expected.hypabolic.json
    expected = "0eebe426da67a5b5fa61a85dbfe11660a18048ed88213efdce142a19e173ce2f"
    assert trajectory_id("pi", "unicode-session") == expected
    assert len(expected) == 64
    assert expected == expected.lower()


def test_sha256_hex_and_record_id_shape() -> None:
    digest = sha256_hex("abc")
    assert digest == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    rid = record_id("grp", "stable", "meta")
    assert rid == sha256_hex(compact_json(["grp", "stable", "meta"]))
    assert len(rid) == 64


def test_model_invocation_id_formula() -> None:
    mid = model_invocation_id("g", "native-1")
    assert mid == record_id("g", "native-1", "model-invocation")


def test_public_export_from_package_root() -> None:
    import hypabolic_trajectory as ht

    assert ht.canonical_json(["x"]) == '["x"]'
    assert "canonical_json" in ht.__all__
