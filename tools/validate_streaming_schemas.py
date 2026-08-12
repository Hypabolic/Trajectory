#!/usr/bin/env python3
"""Validate LS-01 streaming schema vectors (valid must pass, invalid must fail).

Uses jsonschema when available; otherwise exits with guidance to install
``jsonschema`` (python dev extra) or run the .NET StreamingSchemaVectorTests.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "contracts" / "schemas"
VECTORS = ROOT / "contracts" / "vectors" / "streaming"

PRIVACY_SENTINELS = (
    "SECRET_TOKEN_xyz",
    "/Users/real-user/",
)


def main() -> int:
    try:
        import jsonschema
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
        "compatibility.json has no stream-* claims"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
