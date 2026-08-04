"""Public free-function surface (UNSUPPORTED direct import path).

Re-exported from the package root under the PY-04a export-owner role through
PY-12. Free functions always invoke built-in adapters/projectors only and must
not observe ``TrajectoryEngine.add_output_adapter`` mutations.

Authority: docs/python-implementation-spec.md §3 free functions + isolation pin.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from hypabolic_trajectory._enums import TrajectorySource
from hypabolic_trajectory._json_types import JsonObject
from hypabolic_trajectory.dto import NormalizeRequest, TrajectoryListingPage
from hypabolic_trajectory.errors import FATAL_LISTING_UNAVAILABLE, TrajectoryError
from hypabolic_trajectory.listing.common import decode_cursor, validate_limit
from hypabolic_trajectory.listing.protocol import get_lister
from hypabolic_trajectory.normalize.core import (
    normalize_to_ir as normalize_to_ir,
    resolve_source,
)
from hypabolic_trajectory.project.core import (
    project_canonical as project_canonical,
    project_hypabolic as project_hypabolic,
    project_letta as project_letta,
    project_minimal_jsonl as project_minimal_jsonl,
    project_openai as project_openai,
    serialize_projection as serialize_projection,
)
from hypabolic_trajectory.project.otel_genai import (
    project_otel_genai as project_otel_genai,
)

# Peer pin (.NET TrajectoryEngine.ListTrajectoriesAsync) — content-safe fixed text.
_MSG_LISTING_UNAVAILABLE: Final[str] = (
    "No trajectory lister is registered for '{source}'."
)


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


def list_trajectories(
    *,
    source: TrajectorySource | str,
    root: str | Path,
    cursor: str | None = None,
    limit: int = 50,
) -> TrajectoryListingPage:
    """List trajectories for a source under an explicit root (PY-09b).

    Dispatches **only** by the listing registry. Per-source discovery lives in
    registered listers; this free function never hard-codes source layouts or
    reads default home directories.

    Raises:
        TypeError: programmer type mistakes on ``root`` / ``cursor`` / ``source``.
        TrajectoryError: ``unknown_source``, ``listing_unavailable``, or
            ``invalid_input`` (bad limit/cursor at free-function entry).
    """
    # Programmer type mistakes before domain work (content-safety / peer pin).
    if not isinstance(root, (str, Path)):
        raise TypeError("root must be str or Path")
    if cursor is not None and type(cursor) is not str:
        raise TypeError("cursor must be str or None")

    resolved = resolve_source(source)
    lister = get_lister(resolved.value)
    if lister is None:
        raise TrajectoryError(
            FATAL_LISTING_UNAVAILABLE,
            _MSG_LISTING_UNAVAILABLE.format(source=resolved.value),
        ) from None

    # Free-function entry policy: invalid limit/cursor → invalid_input for every
    # source (including stubs that would otherwise ignore cursor). Listers may
    # re-validate via paginate; double-check is intentional and cheap.
    validate_limit(limit)
    if cursor is not None:
        decode_cursor(cursor)

    return lister.list_page(root=root, cursor=cursor, limit=limit)
