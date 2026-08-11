"""Public request / listing DTO tree (frozen dataclasses).

UNSUPPORTED as a direct import path — re-exported from the package root.
Authority: docs/python-implementation-spec.md §3 request/options DTO tree.

Construction never raises domain ``TrajectoryError``; domain validation runs at
free-function / engine entry only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from hypabolic_trajectory._enums import TrajectorySource


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceContext:
    group_id: str | None = None
    base_byte_offset: int = 0  # signed int64 checked at free-function entry
    partial: bool = False
    # Grok Build: project encrypted_content into reasoning text when true.
    include_encrypted_reasoning: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolArgumentBounds:
    max_characters: int | None = 20_000


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolResultBounds:
    max_characters: int | None = 2_500
    strategy: Literal["head", "head-tail"] = "head-tail"


@dataclass(frozen=True, slots=True, kw_only=True)
class Bounds:
    tool_arguments: ToolArgumentBounds = field(default_factory=ToolArgumentBounds)
    tool_results: ToolResultBounds = field(default_factory=ToolResultBounds)


@dataclass(frozen=True, slots=True, kw_only=True)
class Filters:
    tool_results: Literal["include", "omit"] = "include"


@dataclass(frozen=True, slots=True, kw_only=True)
class NormalizeOptions:
    bounds: Bounds = field(default_factory=Bounds)
    filters: Filters = field(default_factory=Filters)


@dataclass(frozen=True, slots=True, kw_only=True)
class NormalizeRequest:
    source: TrajectorySource | str  # required
    transcript: bytes | str  # required — prefer bytes
    source_context: SourceContext = field(default_factory=SourceContext)
    options: NormalizeOptions = field(default_factory=NormalizeOptions)


@dataclass(frozen=True, slots=True, kw_only=True)
class TrajectoryListing:
    id: str
    path: str
    updated_at: str | None = None
    title: str | None = None
    size_bytes: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TrajectoryListingPage:
    items: tuple[TrajectoryListing, ...]
    next_cursor: str | None = None  # ALWAYS present on wire as string or null
