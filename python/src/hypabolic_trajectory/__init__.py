"""hypabolic-trajectory — normalize coding-agent transcripts into Trajectory contracts.

Only names listed in root ``__all__`` (and ``ir.__all__`` / ``otel.__all__`` once
landed) are semver-stable. Other import paths are unsupported.

**Export owner (PY-04a through PY-12):** this module is the single owner of root
``__all__`` and ``api.py`` re-export merge. Parallel free-function/source issues
land implementations in internal modules and update root exports only under this
owner's review. Built-in source/lister registration hooks run on package import
as free functions land.
"""

from __future__ import annotations

from typing import Final

from hypabolic_trajectory._enums import TrajectorySource
from hypabolic_trajectory._json_types import JsonObject, JsonPrimitive, JsonValue
from hypabolic_trajectory._schema import (
    HYPABOLIC_TRAJECTORY_V1,
    JSONL_MINIMAL,
    LETTA_CANONICAL_V1,
    LETTA_TRAJECTORY_V1,
    OPENAI_CHAT_MESSAGES,
    OTEL_GENAI_SPANS_V1,
    SCHEMA_IDS,
    SchemaId,
)
from hypabolic_trajectory._version import (
    NORMALIZER_CONTRACT_VERSION,
    WIRE_PACKAGE_VERSION,
    resolve_package_version,
)

# ---------------------------------------------------------------------------
# Version / constants (normative pins — docs/python-implementation-spec.md §3)
# ---------------------------------------------------------------------------

# Re-exported from cycle-safe leaf `_version` (single source of truth).
PACKAGE_VERSION: Final[str] = resolve_package_version()
__version__ = PACKAGE_VERSION  # single public alias; not a second hand-maintained string

# Extension points (custom adapters) use SchemaId | str at the parameter site.

IMPLEMENTED_SOURCES: Final[tuple[str, ...]] = (
    "pi",
    "claude-code",
    "codex",
    "openclaw",
    "hermes",
    "ahp",
)

# ---------------------------------------------------------------------------
# PY-02 / PY-03 / PY-04a public surface
# ---------------------------------------------------------------------------

from hypabolic_trajectory.canonical import canonical_json
from hypabolic_trajectory.diagnostics import Diagnostic
from hypabolic_trajectory.dto import (
    Bounds,
    Filters,
    NormalizeOptions,
    NormalizeRequest,
    SourceContext,
    ToolArgumentBounds,
    ToolResultBounds,
    TrajectoryListing,
    TrajectoryListingPage,
)
from hypabolic_trajectory.errors import TrajectoryError
from hypabolic_trajectory.ir import (
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
from hypabolic_trajectory.api import (
    list_trajectories,
    normalize_to_canonical,
    normalize_to_hypabolic,
    normalize_to_ir,
    normalize_to_letta,
    project_canonical,
    project_hypabolic,
    project_letta,
    project_minimal_jsonl,
    project_openai,
    project_otel_genai,
    serialize_projection,
)

# TrajectoryEngine: intermediate stub lands in engine.py; listed in root __all__
# only when methods work (first-ship pin — PY-12). Free-function isolation pin is
# documented on engine.py and enforced by free functions never reading engine state.
# from hypabolic_trajectory.engine import TrajectoryEngine  # PY-12

# Import-time registration hooks for built-in sources/listers.
# Per-source owners self-register from submodules imported here (side effect).
from hypabolic_trajectory.listing import ahp as _ahp_listing  # noqa: F401
from hypabolic_trajectory.listing import claude_code as _claude_code_listing  # noqa: F401
from hypabolic_trajectory.listing import codex as _codex_listing  # noqa: F401
from hypabolic_trajectory.listing import hermes as _hermes_listing  # noqa: F401
from hypabolic_trajectory.listing import openclaw as _openclaw_listing  # noqa: F401
from hypabolic_trajectory.listing import pi as _pi_listing  # noqa: F401
from hypabolic_trajectory.sources import ahp as _ahp_source  # noqa: F401
from hypabolic_trajectory.sources import claude_code as _claude_code_source  # noqa: F401
from hypabolic_trajectory.sources import codex as _codex_source  # noqa: F401
from hypabolic_trajectory.sources import hermes as _hermes_source  # noqa: F401
from hypabolic_trajectory.sources import openclaw as _openclaw_source  # noqa: F401
from hypabolic_trajectory.sources import pi as _pi_source  # noqa: F401

# Exhaustive inventory target: docs/python-implementation-spec.md §3 root __all__.
# Progressive: names land with their owning issues under this export owner.
__all__ = [
    # Version / constants
    "NORMALIZER_CONTRACT_VERSION",
    "PACKAGE_VERSION",
    "__version__",
    "WIRE_PACKAGE_VERSION",
    "LETTA_TRAJECTORY_V1",
    "LETTA_CANONICAL_V1",
    "HYPABOLIC_TRAJECTORY_V1",
    "OPENAI_CHAT_MESSAGES",
    "JSONL_MINIMAL",
    "OTEL_GENAI_SPANS_V1",
    "SCHEMA_IDS",
    "SchemaId",
    "IMPLEMENTED_SOURCES",
    "TrajectorySource",
    # JSON aliases
    "JsonPrimitive",
    "JsonValue",
    "JsonObject",
    # Request / listing DTOs
    "SourceContext",
    "ToolArgumentBounds",
    "ToolResultBounds",
    "Bounds",
    "Filters",
    "NormalizeOptions",
    "NormalizeRequest",
    "TrajectoryListing",
    "TrajectoryListingPage",
    # Diagnostics / errors
    "Diagnostic",
    "TrajectoryError",
    # Free functions (stubs for exclusive owners still open; normalize_to_ir = skeleton)
    "normalize_to_ir",
    "normalize_to_letta",
    "normalize_to_canonical",
    "normalize_to_hypabolic",
    "project_letta",
    "project_canonical",
    "project_hypabolic",
    "project_openai",
    "project_minimal_jsonl",
    "project_otel_genai",
    "list_trajectories",
    "serialize_projection",
    "canonical_json",
    # IR re-exports (ir.__all__ multi-project subset)
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
]
