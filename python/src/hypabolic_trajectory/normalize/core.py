"""``normalize_to_ir`` skeleton (PY-04a) — entry validation + dispatch shell.

Full group/linking/bounds/identity behaviour lands in PY-04b.
Authority: docs/python-implementation-spec.md §3 free functions + validation boundary.
"""

from __future__ import annotations

from hypabolic_trajectory._enums import TrajectorySource
from hypabolic_trajectory.dto import (
    Bounds,
    Filters,
    NormalizeOptions,
    NormalizeRequest,
    SourceContext,
    ToolArgumentBounds,
    ToolResultBounds,
)
from hypabolic_trajectory.errors import (
    FATAL_INVALID_INPUT,
    FATAL_UNKNOWN_SOURCE,
    TrajectoryError,
    raise_trajectory_error,
)
from hypabolic_trajectory.ir.models import TrajectoryIR
from hypabolic_trajectory.sources.protocol import get_source_adapter

# Signed int64 range for domain entry checks (base_byte_offset, sequences, …).
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1

_MSG_INVALID_BASE_BYTE_OFFSET = "base_byte_offset is out of range."
_MSG_INVALID_TRANSCRIPT_ENCODE = "Transcript could not be encoded as UTF-8."
_MSG_INVALID_ARGUMENT_BOUNDS = "Tool argument max_characters is invalid."
_MSG_INVALID_RESULT_BOUNDS = "Tool result max_characters is invalid."
_MSG_INVALID_FILTERS = "filters.tool_results is invalid."
_MSG_UNKNOWN_SOURCE = "Unknown source."
_MSG_NORMALIZE_NOT_IMPLEMENTED = (
    "normalize_to_ir behaviour is not implemented for this source yet."
)


def _is_exact_int(value: object) -> bool:
    """True for exact ``int`` (not bool subclass)."""
    return type(value) is int


def _is_signed_int64(value: object) -> bool:
    return _is_exact_int(value) and _INT64_MIN <= value <= _INT64_MAX  # type: ignore[operator]


def resolve_source(source: TrajectorySource | str) -> TrajectorySource:
    """Resolve request source to a ``TrajectorySource`` enum member.

    Unknown wire names raise ``TrajectoryError(code="unknown_source")``.
    """
    if isinstance(source, TrajectorySource):
        return source
    if type(source) is not str:
        raise TypeError("source must be TrajectorySource or str")
    # Construct domain error outside ``except`` so ``__context__`` is not set
    # (content-safety pin — no low-level exception chain).
    domain: TrajectoryError | None = None
    try:
        return TrajectorySource(source)
    except ValueError:
        domain = TrajectoryError(FATAL_UNKNOWN_SOURCE, _MSG_UNKNOWN_SOURCE)
    if domain is not None:
        raise_trajectory_error(domain.code, domain.message)


def encode_transcript(transcript: bytes | str) -> bytes:
    """Return UTF-8 transcript bytes. ``str`` is encoded once as UTF-8 strict."""
    if type(transcript) is bytes:
        return transcript
    if type(transcript) is not str:
        raise TypeError("transcript must be bytes or str")
    # Encode failure may retain the transcript in UnicodeEncodeError; raise
    # domain error outside the handler so ``__context__`` stays empty.
    domain: TrajectoryError | None = None
    encoded: bytes | None = None
    try:
        encoded = transcript.encode("utf-8")
    except UnicodeEncodeError:
        domain = TrajectoryError(FATAL_INVALID_INPUT, _MSG_INVALID_TRANSCRIPT_ENCODE)
    if domain is not None:
        raise_trajectory_error(domain.code, domain.message)
    assert encoded is not None
    return encoded

def validate_normalize_entry(request: NormalizeRequest) -> tuple[TrajectorySource, bytes]:
    """Domain entry checks for normalize free functions / engine.

    Raises:
        TypeError: programmer type mistakes on request fields (checked first).
        TrajectoryError: domain contract failures (int64, bounds, unknown source).
    """
    if not isinstance(request, NormalizeRequest):
        raise TypeError("request must be NormalizeRequest")

    # ---- Python type boundary (before domain work) ----
    ctx = request.source_context
    if not isinstance(ctx, SourceContext):
        raise TypeError("source_context must be SourceContext")
    options = request.options
    if not isinstance(options, NormalizeOptions):
        raise TypeError("options must be NormalizeOptions")
    if not isinstance(options.bounds, Bounds):
        raise TypeError("options.bounds must be Bounds")
    if not isinstance(options.filters, Filters):
        raise TypeError("options.filters must be Filters")
    if not isinstance(options.bounds.tool_arguments, ToolArgumentBounds):
        raise TypeError("tool_arguments must be ToolArgumentBounds")
    if not isinstance(options.bounds.tool_results, ToolResultBounds):
        raise TypeError("tool_results must be ToolResultBounds")

    if ctx.group_id is not None and type(ctx.group_id) is not str:
        raise TypeError("source_context.group_id must be str or None")
    if type(ctx.partial) is not bool:
        raise TypeError("source_context.partial must be bool")
    if type(ctx.base_byte_offset) is not int:
        raise TypeError("source_context.base_byte_offset must be int")

    # Source Python type during type pass (domain unknown_source still later).
    if not isinstance(request.source, TrajectorySource) and type(request.source) is not str:
        raise TypeError("source must be TrajectorySource or str")

    arg_max = options.bounds.tool_arguments.max_characters
    if arg_max is not None and type(arg_max) is not int:
        raise TypeError("tool_arguments.max_characters must be int or None")
    res_max = options.bounds.tool_results.max_characters
    if res_max is not None and type(res_max) is not int:
        raise TypeError("tool_results.max_characters must be int or None")
    strategy = options.bounds.tool_results.strategy
    if type(strategy) is not str:
        raise TypeError("tool_results.strategy must be str")
    tool_results_filter = options.filters.tool_results
    if type(tool_results_filter) is not str:
        raise TypeError("filters.tool_results must be str")

    # Transcript type before source domain (mixed-invalid: TypeError before unknown_source).
    transcript = encode_transcript(request.transcript)

    # ---- Domain contract ----
    source = resolve_source(request.source)

    if not _is_signed_int64(ctx.base_byte_offset):
        raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_INVALID_BASE_BYTE_OFFSET) from None

    # Argument max_characters == 1 or <= 0 (non-None) → invalid_input
    if arg_max is not None and (arg_max == 1 or arg_max <= 0):
        raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_INVALID_ARGUMENT_BOUNDS) from None

    # Result max_characters <= 0 (non-None) → invalid_input
    if res_max is not None and res_max <= 0:
        raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_INVALID_RESULT_BOUNDS) from None

    if strategy not in ("head", "head-tail"):
        raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_INVALID_RESULT_BOUNDS) from None

    if tool_results_filter not in ("include", "omit"):
        raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_INVALID_FILTERS) from None

    return source, transcript


def normalize_to_ir(request: NormalizeRequest) -> TrajectoryIR:
    """Normalize a native transcript into immutable ``TrajectoryIR``.

    PY-04a skeleton: validates free-function entry (source, transcript, int64,
    bounds), resolves the source adapter registry, then raises domain
    ``TrajectoryError`` until PY-04b lands full behaviour.

    Free functions always use built-in adapters only (engine isolation pin).
    """
    source, transcript = validate_normalize_entry(request)
    adapter = get_source_adapter(source.value)
    if adapter is None:
        # Known wire source but no built-in adapter registered yet (wave C0).
        # Prefer domain fatal over NotImplementedError so conformance/runner
        # mapping stays on TrajectoryError.
        raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_NORMALIZE_NOT_IMPLEMENTED) from None

    # Decode path reserved for PY-04b + per-source adapters (PY-05*/06-*).
    _ = (adapter, transcript, request)
    raise TrajectoryError(FATAL_INVALID_INPUT, _MSG_NORMALIZE_NOT_IMPLEMENTED) from None
