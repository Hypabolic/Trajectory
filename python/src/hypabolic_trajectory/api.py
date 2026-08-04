"""Public free-function surface (UNSUPPORTED direct import path).

Re-exported from the package root under the PY-04a export-owner role through
PY-12. Free functions always invoke built-in adapters/projectors only and must
not observe ``TrajectoryEngine.add_output_adapter`` mutations.

Authority: docs/python-implementation-spec.md §3 free functions + isolation pin.
"""

from __future__ import annotations

from pathlib import Path

from hypabolic_trajectory._json_types import JsonObject, JsonValue
from hypabolic_trajectory.dto import NormalizeRequest
from hypabolic_trajectory.errors import FATAL_INVALID_INPUT, TrajectoryError
from hypabolic_trajectory.ir.models import TrajectoryIR
from hypabolic_trajectory.normalize.core import normalize_to_ir as normalize_to_ir
from hypabolic_trajectory.dto import TrajectoryListingPage
from hypabolic_trajectory._enums import TrajectorySource
from hypabolic_trajectory.project.core import (
    project_canonical as project_canonical,
    project_hypabolic as project_hypabolic,
    project_letta as project_letta,
    serialize_projection as serialize_projection,
)

_MSG_NOT_IMPLEMENTED = "This free function is not implemented yet."


def normalize_to_letta(request: NormalizeRequest) -> JsonObject:
    """Convenience: ``normalize_to_ir`` + ``project_letta``."""
    return project_letta(normalize_to_ir(request))


def normalize_to_canonical(request: NormalizeRequest) -> JsonObject:
    """Convenience: ``normalize_to_ir`` + ``project_canonical``.

    May raise ``source_group_required`` for unresolved codex groups even when
    ``normalize_to_ir`` succeeded.
    """
    return project_canonical(normalize_to_ir(request))


def normalize_to_hypabolic(request: NormalizeRequest) -> JsonObject:
    """Convenience: ``normalize_to_ir`` + ``project_hypabolic``."""
    return project_hypabolic(normalize_to_ir(request))


def project_openai(trajectory: TrajectoryIR) -> list[JsonObject]:
    """Project IR to openai-chat-messages (PY-07b)."""
    _ = trajectory
    raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_NOT_IMPLEMENTED) from None


def project_minimal_jsonl(trajectory: TrajectoryIR) -> str:
    """Project IR to jsonl-minimal document string (PY-07b)."""
    _ = trajectory
    raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_NOT_IMPLEMENTED) from None


def project_otel_genai(trajectory: TrajectoryIR) -> JsonObject:
    """Project IR to otel-genai-spans-v1 pure tree (PY-08)."""
    _ = trajectory
    raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_NOT_IMPLEMENTED) from None


def list_trajectories(
    *,
    source: TrajectorySource | str,
    root: str | Path,
    cursor: str | None = None,
    limit: int = 50,
) -> TrajectoryListingPage:
    """List trajectories for a source under an explicit root (PY-09b)."""
    _ = (source, root, cursor, limit)
    raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_NOT_IMPLEMENTED) from None
