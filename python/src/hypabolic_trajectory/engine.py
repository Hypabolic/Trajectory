"""TrajectoryEngine registry sugar (UNSUPPORTED direct import path).

Public surface: re-exported from the package root as ``TrajectoryEngine``
(PY-12 first-ship pin). Free-function / engine **binding isolation pin**:

1. Import alone registers built-ins; free functions need no engine.
2. Free functions ALWAYS use built-in adapters/projectors only — never observe
   ``add_output_adapter`` mutations on any engine instance.
3. Each ``create_default()`` returns an independent engine.
4. ``engine.normalize_to_ir`` uses the same built-in source set as free functions.
5. ``engine.project`` dispatches projectors registered on **that** instance only
   (built-ins from ``create_default`` plus any custom adapters).

Authority: docs/python-implementation-spec.md §3 TrajectoryEngine + isolation pin.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final, cast

from hypabolic_trajectory._json_types import JsonObject, JsonValue
from hypabolic_trajectory._schema import (
    HYPABOLIC_TRAJECTORY_V1,
    JSONL_MINIMAL,
    LETTA_CANONICAL_V1,
    LETTA_TRAJECTORY_V1,
    OPENAI_CHAT_MESSAGES,
    OTEL_GENAI_SPANS_V1,
    SchemaId,
)
from hypabolic_trajectory.dto import NormalizeRequest
from hypabolic_trajectory.errors import FATAL_UNKNOWN_OUTPUT_SCHEMA, TrajectoryError
from hypabolic_trajectory.ir.models import TrajectoryIR
from hypabolic_trajectory.normalize.core import normalize_to_ir as free_normalize_to_ir
from hypabolic_trajectory.project.core import (
    project_canonical as free_project_canonical,
    project_hypabolic as free_project_hypabolic,
    project_letta as free_project_letta,
    project_minimal_jsonl as free_project_minimal_jsonl,
    project_openai as free_project_openai,
)
from hypabolic_trajectory.project.otel_genai import (
    project_otel_genai as free_project_otel_genai,
)

# Peer pin (TS / .NET): fixed content-safe messages; schema_id is a contract id.
_MSG_DUPLICATE_ADAPTER: Final[str] = (
    "An output adapter for schema '{schema_id}' is already registered."
)
_MSG_UNKNOWN_OUTPUT_SCHEMA: Final[str] = (
    "No output adapter is registered for schema '{schema_id}'."
)

_OutputProjector = Callable[[TrajectoryIR], JsonValue]


class TrajectoryEngine:
    """Optional registry sugar; free functions remain the primary surface."""

    def __init__(self) -> None:
        # Projectors for this instance only (isolation pin). Built-ins land via
        # create_default(); custom adapters via add_output_adapter().
        self._projectors: dict[str, _OutputProjector] = {}

    @classmethod
    def create_default(cls) -> TrajectoryEngine:
        """Register all tip-matrix built-in projectors on a fresh engine.

        Includes pure ``otel-genai-spans-v1``. Independent of free functions and
        of every other engine instance.
        """
        return (
            cls()
            .add_output_adapter(LETTA_TRAJECTORY_V1, free_project_letta)
            .add_output_adapter(LETTA_CANONICAL_V1, free_project_canonical)
            .add_output_adapter(HYPABOLIC_TRAJECTORY_V1, free_project_hypabolic)
            .add_output_adapter(OPENAI_CHAT_MESSAGES, free_project_openai)
            .add_output_adapter(JSONL_MINIMAL, free_project_minimal_jsonl)
            .add_output_adapter(OTEL_GENAI_SPANS_V1, free_project_otel_genai)
        )

    def add_output_adapter(
        self,
        schema_id: SchemaId | str,
        projector: Callable[[TrajectoryIR], JsonValue],
    ) -> TrajectoryEngine:
        """Register a projector on **this** engine only.

        Duplicate ``schema_id`` (including built-ins from ``create_default``) →
        ``ValueError`` (not TrajectoryError). Free functions never observe this
        registration.
        """
        if type(schema_id) is not str:
            raise TypeError("schema_id must be str")
        if not callable(projector):
            raise TypeError("projector must be callable")
        if schema_id in self._projectors:
            raise ValueError(_MSG_DUPLICATE_ADAPTER.format(schema_id=schema_id))
        self._projectors[schema_id] = projector
        return self

    def normalize_to_ir(self, request: NormalizeRequest) -> TrajectoryIR:
        """Same built-in source set as free ``normalize_to_ir`` (isolation pin)."""
        return free_normalize_to_ir(request)

    def project(self, trajectory: TrajectoryIR, schema_id: SchemaId | str) -> JsonValue:
        """Project using projectors registered on **this** engine instance.

        Built-ins come from ``create_default``; custom adapters from
        ``add_output_adapter``. Unknown schema →
        ``TrajectoryError(code="unknown_output_schema")``.
        """
        if type(schema_id) is not str:
            raise TypeError("schema_id must be str")
        if not isinstance(trajectory, TrajectoryIR):
            raise TypeError("trajectory must be TrajectoryIR")
        projector = self._projectors.get(schema_id)
        if projector is None:
            raise TrajectoryError(
                FATAL_UNKNOWN_OUTPUT_SCHEMA,
                _MSG_UNKNOWN_OUTPUT_SCHEMA.format(schema_id=schema_id),
            ) from None
        return projector(trajectory)

    def normalize_to_letta(self, request: NormalizeRequest) -> JsonObject:
        """Convenience: ``normalize_to_ir`` + project ``letta-trajectory-v1``."""
        return cast(
            JsonObject,
            self.project(self.normalize_to_ir(request), LETTA_TRAJECTORY_V1),
        )

    def normalize_to_canonical(self, request: NormalizeRequest) -> JsonObject:
        """Convenience: ``normalize_to_ir`` + project ``letta-canonical-v1``."""
        return cast(
            JsonObject,
            self.project(self.normalize_to_ir(request), LETTA_CANONICAL_V1),
        )

    def normalize_to_hypabolic(self, request: NormalizeRequest) -> JsonObject:
        """Convenience: ``normalize_to_ir`` + project ``hypabolic-trajectory-v1``."""
        return cast(
            JsonObject,
            self.project(self.normalize_to_ir(request), HYPABOLIC_TRAJECTORY_V1),
        )
