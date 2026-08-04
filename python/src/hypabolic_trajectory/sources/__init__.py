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

# Built-in adapters (self-register on import). Root package also imports these.
from hypabolic_trajectory.sources import ahp as _ahp  # noqa: E402, F401
from hypabolic_trajectory.sources import claude_code as _claude_code  # noqa: E402, F401
from hypabolic_trajectory.sources import codex as _codex  # noqa: E402, F401
from hypabolic_trajectory.sources import hermes as _hermes  # noqa: E402, F401
from hypabolic_trajectory.sources import openclaw as _openclaw  # noqa: E402, F401
from hypabolic_trajectory.sources import pi as _pi  # noqa: E402, F401


__all__ = [
    "DecodedEvent",
    "DecodedModelInvocation",
    "DecodedSession",
    "SourceAdapter",
    "get_source_adapter",
    "register_source_adapter",
    "registered_source_names",
]
