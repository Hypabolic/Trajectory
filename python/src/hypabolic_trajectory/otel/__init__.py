"""Public OpenTelemetry boundary — always present in the core wheel.

``SpanSetSink`` and ``emit_to`` require **no** ``opentelemetry-*`` packages.
Concrete SDK sink helpers (if added later) raise ``ImportError`` with an
install hint until ``pip install 'hypabolic-trajectory[otel]'``.

Authority: docs/python-implementation-spec.md §3 otel public surface + import matrix.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from hypabolic_trajectory._json_types import JsonObject
from hypabolic_trajectory.ir.models import TrajectoryIR
from hypabolic_trajectory.project.otel_genai import project_otel_genai

__all__ = (
    "SpanSetSink",
    "emit_to",
)


@runtime_checkable
class SpanSetSink(Protocol):
    """Application-owned bridge that receives one pure span-set projection."""

    def emit(self, span_set: JsonObject) -> None:
        """Deliver a complete deterministic ``otel-genai-spans-v1`` tree."""
        ...


def emit_to(sink: SpanSetSink, trajectory: TrajectoryIR) -> None:
    """Project via core ``project_otel_genai`` and deliver the span set to *sink*.

    Does not require ``opentelemetry-*`` packages. Domain fatals from projection
    raise ``TrajectoryError``; sink ``emit`` errors propagate as ordinary exceptions.
    """
    span_set = project_otel_genai(trajectory)
    sink.emit(span_set)
