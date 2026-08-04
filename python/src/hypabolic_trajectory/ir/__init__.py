"""Public multi-project IR surface (``hypabolic_trajectory.ir``).

Only names listed in ``__all__`` are semver-stable.
"""

from __future__ import annotations

from hypabolic_trajectory.diagnostics import Diagnostic
from hypabolic_trajectory.ir.models import (
    AppliedBounds,
    AppliedConfig,
    AppliedFilters,
    IrRecord,
    ModelInvocation,
    ModelTokenUsage,
    Provenance,
    RecordHashes,
    RecordKind,
    SourceAnchorKind,
    SourceIdentityKind,
    ToolCall,
    TrajectoryExecution,
    TrajectoryIR,
    TrajectoryRole,
    WorkflowInvocation,
)

__all__ = [
    "TrajectoryIR",
    "IrRecord",
    "RecordKind",
    "TrajectoryRole",
    "ToolCall",
    "Provenance",
    "SourceIdentityKind",
    "SourceAnchorKind",
    "RecordHashes",
    "AppliedConfig",
    "AppliedBounds",
    "AppliedFilters",
    "TrajectoryExecution",
    "ModelInvocation",
    "ModelTokenUsage",
    "WorkflowInvocation",
    "Diagnostic",
]
