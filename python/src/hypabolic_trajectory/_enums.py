"""Shared public enums used by root, IR, and DTO modules.

Leaf module — must not import package root or ir/dto (avoids import cycles).
"""

from __future__ import annotations

from enum import StrEnum


class TrajectorySource(StrEnum):
    """Canonical public source type. Values equal wire names."""

    PI = "pi"
    CLAUDE_CODE = "claude-code"
    CODEX = "codex"
    OPENCLAW = "openclaw"
    HERMES = "hermes"
    AHP = "ahp"
    GROK_BUILD = "grok-build"
    CURSOR = "cursor"
