"""hypabolic-trajectory — normalize coding-agent transcripts into Trajectory contracts.

Only names listed in root ``__all__`` (and, once landed, ``ir.__all__`` /
``otel.__all__``) are semver-stable. Other import paths are unsupported.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final, Literal, TypeAlias

from hypabolic_trajectory._version import resolve_package_version
from hypabolic_trajectory.diagnostics import Diagnostic
from hypabolic_trajectory.errors import TrajectoryError

# ---------------------------------------------------------------------------
# Version / constants (normative pins — docs/python-implementation-spec.md §3)
# ---------------------------------------------------------------------------

NORMALIZER_CONTRACT_VERSION: Final[str] = "0.2.0"  # wire / identity contract

PACKAGE_VERSION: Final[str] = resolve_package_version()
__version__ = PACKAGE_VERSION  # single public alias; not a second hand-maintained string

# Embedded wire version used for Hypabolic envelope normalizer.version and
# OTEL instrumentation_version. MUST match other tip runtimes on the same git
# tag. Today tip + goldens pin "0.1.0". Do NOT unilaterally bind this to
# PACKAGE_VERSION until all runtimes + goldens move.
WIRE_PACKAGE_VERSION: Final[str] = "0.1.0"

LETTA_TRAJECTORY_V1: Final[str] = "letta-trajectory-v1"
LETTA_CANONICAL_V1: Final[str] = "letta-canonical-v1"
HYPABOLIC_TRAJECTORY_V1: Final[str] = "hypabolic-trajectory-v1"
OPENAI_CHAT_MESSAGES: Final[str] = "openai-chat-messages"
JSONL_MINIMAL: Final[str] = "jsonl-minimal"
OTEL_GENAI_SPANS_V1: Final[str] = "otel-genai-spans-v1"

SCHEMA_IDS: Final[frozenset[str]] = frozenset(
    {
        LETTA_TRAJECTORY_V1,
        LETTA_CANONICAL_V1,
        HYPABOLIC_TRAJECTORY_V1,
        OPENAI_CHAT_MESSAGES,
        JSONL_MINIMAL,
        OTEL_GENAI_SPANS_V1,
    }
)

# Built-in schema ids only — do NOT union with str (that collapses the Literal).
SchemaId: TypeAlias = Literal[
    "letta-trajectory-v1",
    "letta-canonical-v1",
    "hypabolic-trajectory-v1",
    "openai-chat-messages",
    "jsonl-minimal",
    "otel-genai-spans-v1",
]
# Extension points (custom adapters) use SchemaId | str at the parameter site.

IMPLEMENTED_SOURCES: Final[tuple[str, ...]] = (
    "pi",
    "claude-code",
    "codex",
    "openclaw",
    "hermes",
    "ahp",
)


class TrajectorySource(StrEnum):
    """Canonical public source type. Values equal wire names."""

    PI = "pi"
    CLAUDE_CODE = "claude-code"
    CODEX = "codex"
    OPENCLAW = "openclaw"
    HERMES = "hermes"
    AHP = "ahp"


# ---------------------------------------------------------------------------
# Public JSON type aliases (py.typed — normative)
# ---------------------------------------------------------------------------

JsonPrimitive: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]

# Progressive root exports: PY-01 constants + PY-03 Diagnostic / TrajectoryError.
# Free functions, DTOs, IR, engine, and otel land in later issues.
# Root ``__all__`` grows under the PY-04a export-owner role through PY-12.
__all__ = [
    "NORMALIZER_CONTRACT_VERSION",
    "PACKAGE_VERSION",
    "__version__",
    "WIRE_PACKAGE_VERSION",
    "LETTA_TRAJECTORY_V1",
    "LETTA_CANONICAL_V1",
    "HYPABOLIC_TRAJECTORY_V1",
    "OPENAI_CHAT_MESSAGES",
    "JSONL_MINIMAL",
    "OTEL_GENAI_SPANS_V1",
    "SCHEMA_IDS",
    "SchemaId",
    "IMPLEMENTED_SOURCES",
    "TrajectorySource",
    "JsonPrimitive",
    "JsonValue",
    "JsonObject",
    "Diagnostic",
    "TrajectoryError",
]
