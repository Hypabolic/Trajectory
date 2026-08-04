"""Schema id Literal alias (leaf — no package-root imports).

Kept separate so ``engine`` and other internal modules can annotate
``SchemaId | str`` without importing the package root (import-cycle safety).
"""

from __future__ import annotations

from typing import Final, Literal, TypeAlias

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
