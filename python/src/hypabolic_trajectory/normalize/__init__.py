"""Normalization package (UNSUPPORTED public import path).

PY-04a: ``normalize_to_ir`` skeleton (entry validation + adapter dispatch shell).
PY-04b: full normalization behaviour (group/linking/bounds/identity/timestamps).
"""

from __future__ import annotations

from hypabolic_trajectory.normalize.core import (
    map_model_invocation,
    normalize_decoded,
    normalize_to_ir,
    plan_events,
    resolve_group_id,
)

__all__ = [
    "map_model_invocation",
    "normalize_decoded",
    "normalize_to_ir",
    "plan_events",
    "resolve_group_id",
]
