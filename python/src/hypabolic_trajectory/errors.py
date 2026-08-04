"""Domain fatal errors (TrajectoryError).

UNSUPPORTED import path — public surface is root re-export of ``TrajectoryError``.
Authority: contracts/spec/diagnostics.md + docs/python-implementation-spec.md §3.
"""

from __future__ import annotations

from typing import Final, NoReturn

# ---------------------------------------------------------------------------
# Stable fatal error wire codes (diagnostics.md contract version 1)
# ---------------------------------------------------------------------------

FATAL_INVALID_INPUT: Final[str] = "invalid_input"
FATAL_UNKNOWN_SOURCE: Final[str] = "unknown_source"
FATAL_UNKNOWN_OUTPUT_SCHEMA: Final[str] = "unknown_output_schema"
FATAL_MISSING_USER_RECORDS: Final[str] = "missing_user_records"
FATAL_MISSING_ASSISTANT_RECORDS: Final[str] = "missing_assistant_records"
FATAL_INVALID_NORMALIZED_TRANSCRIPT: Final[str] = "invalid_normalized_transcript"
FATAL_LISTING_UNAVAILABLE: Final[str] = "listing_unavailable"
FATAL_SOURCE_GROUP_CONFLICT: Final[str] = "source_group_conflict"
FATAL_SOURCE_GROUP_REQUIRED: Final[str] = "source_group_required"

FATAL_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {
        FATAL_INVALID_INPUT,
        FATAL_UNKNOWN_SOURCE,
        FATAL_UNKNOWN_OUTPUT_SCHEMA,
        FATAL_MISSING_USER_RECORDS,
        FATAL_MISSING_ASSISTANT_RECORDS,
        FATAL_INVALID_NORMALIZED_TRANSCRIPT,
        FATAL_LISTING_UNAVAILABLE,
        FATAL_SOURCE_GROUP_CONFLICT,
        FATAL_SOURCE_GROUP_REQUIRED,
    }
)

# Content-safe fixed messages used by timestamp / dual-field helpers (peer pin).
MSG_TIMESTAMP_OUT_OF_RANGE: Final[str] = "Timestamp is out of range."
MSG_SOURCE_TIMESTAMP_UNAVAILABLE: Final[str] = "Source timestamp is unavailable."


class TrajectoryError(Exception):
    """Domain fatal. Catchable; stable wire code + content-safe message."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"

    def __repr__(self) -> str:
        return f"TrajectoryError(code={self.code!r}, message={self.message!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TrajectoryError):
            return NotImplemented
        return self.code == other.code and self.message == other.message


def raise_trajectory_error(code: str, message: str) -> NoReturn:
    """Raise a domain fatal with no ``__cause__`` / ``__context__`` chain.

    Call this **outside** an ``except`` block when translating low-level
    exceptions so ``__context__`` does not retain transcript fragments, paths,
    or raw payloads (normative content-safety pin).

    Pattern::

        domain: TrajectoryError | None = None
        try:
            ...
        except SomeLowLevelError:
            domain = TrajectoryError(code, message)
        if domain is not None:
            raise_trajectory_error(domain.code, domain.message)
            # or: raise domain from None  # still outside except
    """
    raise TrajectoryError(code, message) from None
