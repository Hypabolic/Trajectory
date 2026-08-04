"""Projection layer (UNSUPPORTED import path).

Maps immutable TrajectoryIR to public schema trees and product serializers.
Exclusive free-function owners land here; ``api.py`` re-exports them.
"""

from __future__ import annotations

from hypabolic_trajectory.project.core import (
    project_canonical,
    project_hypabolic,
    project_letta,
    project_minimal_jsonl,
    project_openai,
    serialize_projection,
    to_letta_record,
)

__all__ = [
    "project_canonical",
    "project_hypabolic",
    "project_letta",
    "project_minimal_jsonl",
    "project_openai",
    "serialize_projection",
    "to_letta_record",
]
