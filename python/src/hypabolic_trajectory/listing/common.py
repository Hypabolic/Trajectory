"""Shared listing helpers: limit validation, cursor codec, sort, paginate.

UNSUPPORTED import path. Per-source listers (PY-05a/05b/06-*) call these
helpers; ``list_trajectories`` (PY-09b) only dispatches by registry.

Authority:
- contracts/spec/listing.md (limit 1–1000, opaque v1 cursor, sort order)
- Peer: Rust ``listing.rs`` paginate/encode_cursor; .NET ``TrajectoryPagination``
- docs/python-implementation-spec.md PY-09a
"""

from __future__ import annotations

import base64
import binascii
import re
from functools import cmp_to_key
from typing import Final, Sequence

from hypabolic_trajectory.canonical import utf16_compare
from hypabolic_trajectory.dto import TrajectoryListing, TrajectoryListingPage
from hypabolic_trajectory.errors import FATAL_INVALID_INPUT, TrajectoryError

# ---------------------------------------------------------------------------
# Constants (contract pin)
# ---------------------------------------------------------------------------

DEFAULT_LISTING_LIMIT: Final[int] = 50
MIN_LISTING_LIMIT: Final[int] = 1
MAX_LISTING_LIMIT: Final[int] = 1_000

MSG_INVALID_LIMIT: Final[str] = "Listing limit must be between 1 and 1000."
MSG_INVALID_CURSOR: Final[str] = "Cursor is not a valid trajectory-listing cursor."

# Cursor payload: "1\\n{index}\\n{id}" (version 1).
_CURSOR_VERSION: Final[str] = "1"
# Strict base64url alphabet, no padding (Rust URL_SAFE_NO_PAD).
_BASE64URL_NO_PAD: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]*$")


def validate_limit(limit: int) -> None:
    """Raise ``invalid_input`` when *limit* is outside 1..1000 inclusive.

    Callers must pass a real ``int`` (not bool). Pure type mistakes may raise
    ``TypeError`` before domain work.
    """
    if type(limit) is not int:
        raise TypeError("Listing limit must be an int.")
    if not (MIN_LISTING_LIMIT <= limit <= MAX_LISTING_LIMIT):
        raise TrajectoryError(FATAL_INVALID_INPUT, MSG_INVALID_LIMIT) from None


def encode_cursor(item_id: str, index: int) -> str:
    """Encode a v1 opaque listing cursor (base64url, no padding).

    Payload is UTF-8 ``1\\n{index}\\n{id}`` matching tip Rust/TS/.NET codecs.
    The id portion may contain newlines; decode splits at most twice.
    """
    if type(item_id) is not str:
        raise TypeError("Cursor id must be a str.")
    if type(index) is not int or index < 0:
        raise TypeError("Cursor index must be a non-negative int.")
    payload = f"{_CURSOR_VERSION}\n{index}\n{item_id}".encode("utf-8")
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def decode_cursor(value: str) -> tuple[str, int]:
    """Decode a v1 opaque listing cursor to ``(id, index)``.

    Raises ``TrajectoryError(code="invalid_input", ...)`` for malformed input.
    """
    if type(value) is not str:
        raise TypeError("Cursor must be a str.")
    decoded = _try_decode_cursor_bytes(value)
    if decoded is None:
        raise TrajectoryError(FATAL_INVALID_INPUT, MSG_INVALID_CURSOR) from None
    return decoded


def _try_decode_cursor_bytes(value: str) -> tuple[str, int] | None:
    # Reject padding and standard base64 alphabet; only unpadded base64url.
    if not _BASE64URL_NO_PAD.fullmatch(value):
        return None
    try:
        padded = value + ("=" * (-len(value) % 4))
        standard = padded.translate(str.maketrans("-_", "+/"))
        raw = base64.b64decode(standard, validate=True)
        # Canonicalize: reject non-zero trailing-bit aliases (Rust URL_SAFE_NO_PAD).
        reencoded = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
        if reencoded != value:
            return None
        text = raw.decode("utf-8")
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return None
    # At most two splits so id may contain embedded newlines (.NET parity).
    parts = text.split("\n", 2)
    if len(parts) != 3 or parts[0] != _CURSOR_VERSION:
        return None
    try:
        index = int(parts[1], 10)
    except ValueError:
        return None
    if index < 0:
        return None
    return parts[2], index


def compare_listing_items(left: TrajectoryListing, right: TrajectoryListing) -> int:
    """Compare two listings: ``updated_at`` descending, then ``id`` ordinal ASC.

    Missing ``updated_at`` sorts after any present timestamp (nulls last on DESC),
    matching .NET ``OrderByDescending`` null placement for optional timestamps.
    ID tie-break uses unsigned UTF-16 code-unit order (peer ordinal).
    """
    left_ts = left.updated_at
    right_ts = right.updated_at
    if left_ts is None and right_ts is not None:
        return 1
    if left_ts is not None and right_ts is None:
        return -1
    if left_ts is not None and right_ts is not None and left_ts != right_ts:
        # ISO-8601 ``...fffZ`` strings sort lexicographically by instant.
        return -1 if left_ts > right_ts else 1
    return utf16_compare(left.id, right.id)


def sort_listings(items: Sequence[TrajectoryListing]) -> list[TrajectoryListing]:
    """Return a new list sorted by contract order (updated_at DESC, id ordinal)."""
    return sorted(items, key=cmp_to_key(compare_listing_items))


def _resume_start(ordered: Sequence[TrajectoryListing], item_id: str, prev_index: int) -> int:
    """Compute the next page start from a v1 cursor against *ordered* items.

    Peer pin (Rust/TS/.NET): resume after the **first** item whose id matches
    the cursor id. If no item still has that id (disappeared), resume at
    ``min(previous_index + 1, current_count)`` per listing.md.

    Source listers emit unique native ids within a store (file stems / session
    ids); duplicate-id stores are outside the tip conformance surface.
    """
    n = len(ordered)
    for i, item in enumerate(ordered):
        if item.id == item_id:
            return i + 1
    return min(prev_index + 1, n)


def paginate(
    items: Sequence[TrajectoryListing],
    *,
    cursor: str | None,
    limit: int,
) -> TrajectoryListingPage:
    """Sort, apply cursor window, emit next_cursor.

    Contract / peer pin:
    - Validate limit 1..1000.
    - Cursor is opaque; resume after the first item matching the cursor id, or
      ``min(previous_index + 1, current_count)`` if the cursor item disappeared.
    - ``next_cursor`` is set only when more items remain after this page.
    """
    validate_limit(limit)
    ordered = sort_listings(items)

    start = 0
    if cursor is not None:
        item_id, prev_index = decode_cursor(cursor)
        start = _resume_start(ordered, item_id, prev_index)

    end = min(start + limit, len(ordered))
    page = tuple(ordered[start:end])
    next_cursor: str | None = None
    if end < len(ordered) and page:
        next_cursor = encode_cursor(page[-1].id, end - 1)
    return TrajectoryListingPage(items=page, next_cursor=next_cursor)


__all__ = [
    "DEFAULT_LISTING_LIMIT",
    "MAX_LISTING_LIMIT",
    "MIN_LISTING_LIMIT",
    "MSG_INVALID_CURSOR",
    "MSG_INVALID_LIMIT",
    "compare_listing_items",
    "decode_cursor",
    "encode_cursor",
    "paginate",
    "sort_listings",
    "validate_limit",
]
