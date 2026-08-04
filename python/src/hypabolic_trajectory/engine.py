"""TrajectoryEngine registry sugar (UNSUPPORTED direct import path).

PY-12 delivers a working ``create_default`` / ``project`` / ``add_output_adapter``.
This module freezes the free-function/engine **binding isolation pin** (PY-04a):

1. Import alone registers built-ins; free functions need no engine.
2. Free functions ALWAYS use built-in adapters/projectors only — never observe
   ``add_output_adapter`` mutations on any engine instance.
3. Each ``create_default()`` returns an independent engine.
4. ``engine.normalize_to_ir`` uses the same built-in source set as free functions.
5. ``engine.project`` dispatches built-ins + custom adapters on **that** instance.

Intermediate development builds may ship a non-functional stub; the first public
tag must not list ``TrajectoryEngine`` in root ``__all__`` without working methods.
"""

from __future__ import annotations

from collections.abc import Callable

from hypabolic_trajectory._json_types import JsonObject, JsonValue
from hypabolic_trajectory._schema import SchemaId
from hypabolic_trajectory.dto import NormalizeRequest
from hypabolic_trajectory.errors import FATAL_INVALID_INPUT, TrajectoryError
from hypabolic_trajectory.ir.models import TrajectoryIR
from hypabolic_trajectory.normalize.core import normalize_to_ir as free_normalize_to_ir

_MSG_ENGINE_NOT_IMPLEMENTED = "TrajectoryEngine is not fully implemented yet (PY-12)."


class TrajectoryEngine:
    """Optional registry sugar; free functions remain the primary surface."""

    def __init__(self) -> None:
        # Custom projectors for this instance only (isolation pin).
        self._custom_projectors: dict[str, Callable[[TrajectoryIR], JsonValue]] = {}

    @classmethod
    def create_default(cls) -> TrajectoryEngine:
        """Return an independent engine (built-in projectors land in PY-12)."""
        return cls()

    def add_output_adapter(
        self,
        schema_id: SchemaId | str,
        projector: Callable[[TrajectoryIR], JsonValue],
    ) -> TrajectoryEngine:
        """Register a custom projector on **this** engine only.

        Duplicate ``schema_id`` → ``ValueError`` (not TrajectoryError).
        Free functions must not observe this registration.
        """
        if type(schema_id) is not str:
            raise TypeError("schema_id must be str")
        if not callable(projector):
            raise TypeError("projector must be callable")
        key = schema_id
        if key in self._custom_projectors:
            raise ValueError(f"duplicate output adapter schema_id: {key!r}")
        self._custom_projectors[key] = projector
        return self

    def normalize_to_ir(self, request: NormalizeRequest) -> TrajectoryIR:
        """Same built-in source set as free ``normalize_to_ir`` (isolation pin)."""
        return free_normalize_to_ir(request)

    def project(self, trajectory: TrajectoryIR, schema_id: SchemaId | str) -> JsonValue:
        """Project using built-ins + this instance's custom adapters (PY-12).

        Custom adapters registered via ``add_output_adapter`` are consulted only
        on **this** engine; free-function ``project_*`` never observe them.
        """
        if type(schema_id) is not str:
            raise TypeError("schema_id must be str")
        if not isinstance(trajectory, TrajectoryIR):
            raise TypeError("trajectory must be TrajectoryIR")
        custom = self._custom_projectors.get(schema_id)
        if custom is not None:
            return custom(trajectory)
        raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_ENGINE_NOT_IMPLEMENTED) from None

    def normalize_to_letta(self, request: NormalizeRequest) -> JsonObject:
        _ = request
        raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_ENGINE_NOT_IMPLEMENTED) from None

    def normalize_to_canonical(self, request: NormalizeRequest) -> JsonObject:
        _ = request
        raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_ENGINE_NOT_IMPLEMENTED) from None

    def normalize_to_hypabolic(self, request: NormalizeRequest) -> JsonObject:
        _ = request
        raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_ENGINE_NOT_IMPLEMENTED) from None
