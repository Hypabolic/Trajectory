"""Identity digests and record-id helpers built on canonical/compact JSON.

Authority: contracts/spec/canonical-json.md (hash inputs) and
contracts/spec/identity.md. Digests are always 64 lowercase hex characters
of SHA-256 over exact UTF-8 bytes.

Full normalize-time identity resolution (group resolution, source-order IDs,
model-invocation formula wiring into IR) lands in PY-04b; this module owns
the pure digest primitives that those paths call.
"""

from __future__ import annotations

import hashlib
from typing import Final

from hypabolic_trajectory.canonical import compact_json

# Component keys from contracts/spec/identity.md (reference set; not exhaustive
# for open-ended tool-call ids which include a call-id suffix).
COMPONENT_KEY_META: Final[str] = "meta"
COMPONENT_KEY_MODEL_INVOCATION: Final[str] = "model-invocation"


def sha256_hex(data: str | bytes) -> str:
    """SHA-256 of exact UTF-8 bytes (*str* encoded as UTF-8) → 64 lowercase hex."""
    if isinstance(data, str):
        payload = data.encode("utf-8")
    else:
        payload = data
    return hashlib.sha256(payload).hexdigest()


def record_id(
    source_group_id: str,
    stable_source_record_id: str,
    component_key: str,
) -> str:
    """Public record identity: ``sha256(utf8(canonical/compact array tuple))``.

    Tuple shape: ``[source_group_id, stable_source_record_id, component_key]``.
    Array form has no object keys, so compact and canonical emit are identical.
    """
    return sha256_hex(
        compact_json([source_group_id, stable_source_record_id, component_key])
    )


def location_identity(
    source_group_id: str,
    anchor_kind: str,
    offset: int,
) -> str:
    """Location fallback identity (contracts/spec/canonical-json.md).

    ``sha256(utf8(group + "|" + lower_case_anchor_kind + "|" + decimal_offset))``.
    """
    literal = f"{source_group_id}|{anchor_kind.lower()}|{int(offset)}"
    return sha256_hex(literal)


def content_hash_envelope(record_type: str, semantic_content: object) -> str:
    """``content_sha256`` envelope: canonical ``{"content": …, "type": …}``.

    Keys are sorted by UTF-16 order (``content`` before ``type``). Callers pass
    the already-resolved semantic content tree (JSON-serializable).
    """
    from hypabolic_trajectory.canonical import canonical_json

    # Explicit construction; canonical_json sorts keys.
    return sha256_hex(
        canonical_json({"content": semantic_content, "type": record_type})  # type: ignore[arg-type]
    )


def trajectory_id(source_wire_name: str, group_id: str) -> str:
    """Hypabolic ``trajectory_id``: ``sha256(utf8(compact_json([source, group])))``."""
    return sha256_hex(compact_json([source_wire_name, group_id]))


def model_invocation_id(group_id: str, identity: str) -> str:
    """Model-invocation span/record id seed (3-element array, compact JSON)."""
    return record_id(group_id, identity, COMPONENT_KEY_MODEL_INVOCATION)


__all__ = [
    "COMPONENT_KEY_META",
    "COMPONENT_KEY_MODEL_INVOCATION",
    "content_hash_envelope",
    "location_identity",
    "model_invocation_id",
    "record_id",
    "sha256_hex",
    "trajectory_id",
]
