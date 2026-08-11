"""Listing package (UNSUPPORTED public import path).

PY-09a: common helpers (sort/cursor/limit/paginate), listing DTOs (root
re-exports from ``dto``), empty TrajectoryLister registry shell.
Per-source listers register only (PY-05a/05b/06-*); dispatcher is PY-09b.
"""

from __future__ import annotations

from hypabolic_trajectory.listing.common import (
    DEFAULT_LISTING_LIMIT,
    MAX_LISTING_LIMIT,
    MIN_LISTING_LIMIT,
    MSG_INVALID_CURSOR,
    MSG_INVALID_LIMIT,
    compare_listing_items,
    decode_cursor,
    encode_cursor,
    paginate,
    sort_listings,
    validate_limit,
)
from hypabolic_trajectory.listing.protocol import (
    TrajectoryLister,
    get_lister,
    register_lister,
    registered_lister_names,
)

# Built-in listers (self-register on import). Root package also imports these.
from hypabolic_trajectory.listing import ahp as _ahp  # noqa: E402, F401
from hypabolic_trajectory.listing import claude_code as _claude_code  # noqa: E402, F401
from hypabolic_trajectory.listing import codex as _codex  # noqa: E402, F401
from hypabolic_trajectory.listing import grok_build as _grok_build  # noqa: E402, F401
from hypabolic_trajectory.listing import hermes as _hermes  # noqa: E402, F401
from hypabolic_trajectory.listing import openclaw as _openclaw  # noqa: E402, F401
from hypabolic_trajectory.listing import pi as _pi  # noqa: E402, F401


__all__ = [
    "DEFAULT_LISTING_LIMIT",
    "MAX_LISTING_LIMIT",
    "MIN_LISTING_LIMIT",
    "MSG_INVALID_CURSOR",
    "MSG_INVALID_LIMIT",
    "TrajectoryLister",
    "compare_listing_items",
    "decode_cursor",
    "encode_cursor",
    "get_lister",
    "paginate",
    "register_lister",
    "registered_lister_names",
    "sort_listings",
    "validate_limit",
]
