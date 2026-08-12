#!/usr/bin/env python3
"""Validate LS-01 streaming schema vectors (valid must pass, invalid must fail).

Uses jsonschema when available; otherwise exits with guidance to install
``jsonschema`` (python dev extra) or run the .NET StreamingSchemaVectorTests.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "contracts" / "schemas"
VECTORS = ROOT / "contracts" / "vectors" / "streaming"

PRIVACY_SENTINELS = (
    "SECRET_TOKEN_xyz",
    "/Users/real-user/",
)

# Doc-only keys ignored when comparing embedded fragments across schema files.
_STRIP_KEYS = frozenset({"description", "title", "$comment", "comment"})


def _strip_docs(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            k: _strip_docs(v)
            for k, v in node.items()
            if k not in _STRIP_KEYS
        }
    if isinstance(node, list):
        return [_strip_docs(x) for x in node]
    return node


def _load_schema(name: str) -> dict[str, Any]:
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


def _assert_fragment_equiv(
    errors: list[str],
    label: str,
    left: Any,
    right: Any,
) -> None:
    a = _strip_docs(left)
    b = _strip_docs(right)
    if a != b:
        errors.append(
            f"schema fragment drift ({label}): embedded copies differ after "
            "stripping description/title/$comment"
        )


def check_shared_fragments(errors: list[str]) -> None:
    """Guard against drift between standalone cursor/delta and embedded copies.

    Schemas intentionally inline shared defs (offline validation without $ref
    registry). This check keeps structural constraints aligned.
    """
    stream = _load_schema("trajectory-stream-v1.schema.json")
    delta = _load_schema("streaming-delta-v1.schema.json")
    cursor = _load_schema("streaming-cursor-v1.schema.json")
    case = _load_schema("streaming-case-v1.schema.json")
    manifest = _load_schema("compatibility-manifest-v1.schema.json")

    sdefs = stream["$defs"]
    ddefs = delta["$defs"]
    cdefs = cursor["$defs"]

    for key in (
        "bytePosition",
        "ahpServerSeqPosition",
        "snapshotRevisionPosition",
        "hermesRowPosition",
        "sha256",
        "uint64",
        "int64",
        "nonNegativeInt64",
    ):
        if key in sdefs and key in cdefs:
            _assert_fragment_equiv(
                errors, f"cursor/{key} vs stream/{key}", cdefs[key], sdefs[key]
            )
        if key in ddefs and key in cdefs:
            _assert_fragment_equiv(
                errors, f"cursor/{key} vs delta/{key}", cdefs[key], ddefs[key]
            )

    # Full cursor object: standalone root vs embedded streamCursor (ignore root
    # metadata and optional document-level $schema property).
    cursor_root = {
        k: v
        for k, v in cursor.items()
        if k
        not in {
            "$schema",
            "$id",
            "title",
            "description",
            "$defs",
        }
    }
    cursor_root_props = dict(cursor_root.get("properties") or {})
    cursor_root_props.pop("$schema", None)
    cursor_root = {**cursor_root, "properties": cursor_root_props}
    _assert_fragment_equiv(
        errors,
        "streaming-cursor-v1 root vs streamCursor",
        cursor_root,
        sdefs["streamCursor"],
    )
    _assert_fragment_equiv(
        errors,
        "stream streamCursor vs delta streamCursor",
        sdefs["streamCursor"],
        ddefs["streamCursor"],
    )

    for key in (
        "streamDiagnostic",
        "streamRecordBody",
        "streamRecord",
        "streamReset",
        "streamRevision",
        "recordStatus",
    ):
        if key in sdefs and key in ddefs:
            _assert_fragment_equiv(
                errors, f"stream/{key} vs delta/{key}", sdefs[key], ddefs[key]
            )

    cap_case = case["$defs"]["streamCapability"]["enum"]
    cap_manifest = manifest["$defs"]["capabilityName"]["enum"]
    if cap_case != cap_manifest:
        errors.append(
            "capability enum drift: streaming-case-v1 streamCapability.enum "
            "must equal compatibility-manifest-v1 capabilityName.enum"
        )


def main() -> int:
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        print(
            "jsonschema not installed; install with "
            "`pip install 'jsonschema>=4'` or run "
            "dotnet test --filter StreamingSchemaVectorTests",
            file=sys.stderr,
        )
        return 2

    schema_cache: dict[str, Draft202012Validator] = {}

    def validator_for(name: str) -> Draft202012Validator:
        if name not in schema_cache:
            path = SCHEMAS / name
            schema = json.loads(path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
            schema_cache[name] = Draft202012Validator(schema)
        return schema_cache[name]

    errors: list[str] = []

    check_shared_fragments(errors)

    valid_dir = VECTORS / "valid"
    invalid_dir = VECTORS / "invalid"
    valid_files = sorted(valid_dir.glob("*.json"))
    invalid_files = sorted(invalid_dir.glob("*.json"))
    if not valid_files or not invalid_files:
        errors.append(f"missing vectors under {VECTORS}")
        print("\n".join(errors), file=sys.stderr)
        return 1

    for path in valid_files:
        doc = json.loads(path.read_text(encoding="utf-8"))
        schema_name = doc["schema"]
        instance = doc["instance"]
        v = validator_for(schema_name)
        errs = sorted(v.iter_errors(instance), key=lambda e: list(e.absolute_path))
        if errs:
            errors.append(f"VALID expected pass: {path.name}: {errs[0].message}")
        # Privacy: valid fixtures must not embed sentinel secrets (except listed
        # as forbidden_substrings expectations in case vectors).
        text = path.read_text(encoding="utf-8")
        if path.name != "case-minimal-sequence.json":
            for sentinel in PRIVACY_SENTINELS:
                if sentinel in text:
                    errors.append(
                        f"privacy: {path.name} contains forbidden sentinel {sentinel!r}"
                    )

    for path in invalid_files:
        doc = json.loads(path.read_text(encoding="utf-8"))
        schema_name = doc["schema"]
        instance = doc["instance"]
        v = validator_for(schema_name)
        errs = list(v.iter_errors(instance))
        if not errs:
            errors.append(f"INVALID expected fail: {path.name}")

    # Checked-in compatibility.json must still validate and must not claim stream-*.
    manifest_path = ROOT / "contracts" / "compatibility.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    v = validator_for("compatibility-manifest-v1.schema.json")
    man_errs = list(v.iter_errors(manifest))
    if man_errs:
        errors.append(f"compatibility.json invalid: {man_errs[0].message}")
    for section in ("required", "optional"):
        for cap in manifest["capabilities"][section]:
            if isinstance(cap, str) and cap.startswith("stream-"):
                errors.append(
                    f"compatibility.json must not claim stream capability yet: {cap}"
                )

    if errors:
        print(f"{len(errors)} failure(s):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print(
        f"ok: {len(valid_files)} valid, {len(invalid_files)} invalid vectors; "
        "compatibility.json has no stream-* claims; shared fragments aligned"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
