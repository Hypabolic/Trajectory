"""SourceAdapter Protocol + built-in adapter registry shell.

UNSUPPORTED import path. Adapters self-register by wire name without editing
the normalizer dispatcher.

Authority: docs/python-implementation-spec.md §4.1 adapter registry Protocol.
"""

from __future__ import annotations

from typing import Protocol

from hypabolic_trajectory._enums import TrajectorySource
from hypabolic_trajectory.dto import SourceContext
from hypabolic_trajectory.sources.decoded import DecodedSession

# Wire-name → adapter. Built-ins register on package import as free functions land.
_ADAPTERS: dict[str, SourceAdapter] = {}


class SourceAdapter(Protocol):
    @property
    def source(self) -> TrajectorySource: ...

    def decode(
        self,
        transcript: bytes,
        *,
        source_context: SourceContext,
    ) -> DecodedSession: ...


def register_source_adapter(adapter: SourceAdapter) -> None:
    """Register a built-in source adapter by wire name (idempotent replace)."""
    _ADAPTERS[adapter.source.value] = adapter


def get_source_adapter(wire_name: str) -> SourceAdapter | None:
    """Return the registered adapter for ``wire_name``, or None."""
    return _ADAPTERS.get(wire_name)


def registered_source_names() -> frozenset[str]:
    """Wire names with a registered adapter (for tests / introspection)."""
    return frozenset(_ADAPTERS.keys())
