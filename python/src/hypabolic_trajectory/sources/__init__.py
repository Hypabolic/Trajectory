"""Source adapters package (UNSUPPORTED public import path).

Decode seam freeze (PY-04a): Decoded* types + SourceAdapter Protocol.
Per-source adapters land in PY-05*/06-* and self-register here.
"""

from __future__ import annotations

from hypabolic_trajectory.sources.decoded import (
    DecodedEvent,
    DecodedModelInvocation,
    DecodedSession,
)
from hypabolic_trajectory.sources.protocol import (
    SourceAdapter,
    get_source_adapter,
    register_source_adapter,
    registered_source_names,
)

__all__ = [
    "DecodedEvent",
    "DecodedModelInvocation",
    "DecodedSession",
    "SourceAdapter",
    "get_source_adapter",
    "register_source_adapter",
    "registered_source_names",
]
