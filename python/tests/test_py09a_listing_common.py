"""PY-09a unit tests: listing sort / cursor / limit + empty registry shell."""

from __future__ import annotations

import base64

import pytest

from hypabolic_trajectory.dto import TrajectoryListing, TrajectoryListingPage
from hypabolic_trajectory.errors import FATAL_INVALID_INPUT, TrajectoryError
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
    _LISTERS,
    clear_listers_for_tests,
    get_lister,
    register_lister,
    registered_lister_names,
)


def _item(
    item_id: str,
    *,
    updated_at: str | None = None,
    path: str | None = None,
) -> TrajectoryListing:
    return TrajectoryListing(
        id=item_id,
        path=path if path is not None else f"/tmp/{item_id}.jsonl",
        updated_at=updated_at,
    )


# ---------------------------------------------------------------------------
# Limit
# ---------------------------------------------------------------------------


def test_default_and_bound_constants() -> None:
    assert DEFAULT_LISTING_LIMIT == 50
    assert MIN_LISTING_LIMIT == 1
    assert MAX_LISTING_LIMIT == 1000


@pytest.mark.parametrize("limit", [1, 50, 1000])
def test_validate_limit_accepts_bounds(limit: int) -> None:
    validate_limit(limit)  # does not raise


@pytest.mark.parametrize("limit", [0, -1, 1001, 10_000])
def test_validate_limit_rejects_out_of_range(limit: int) -> None:
    with pytest.raises(TrajectoryError) as ei:
        validate_limit(limit)
    assert ei.value.code == FATAL_INVALID_INPUT
    assert ei.value.message == MSG_INVALID_LIMIT


def test_validate_limit_rejects_bool() -> None:
    with pytest.raises(TypeError):
        validate_limit(True)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Cursor codec
# ---------------------------------------------------------------------------


def test_encode_decode_cursor_roundtrip() -> None:
    cursor = encode_cursor("session-abc", 7)
    item_id, index = decode_cursor(cursor)
    assert item_id == "session-abc"
    assert index == 7


def test_encode_cursor_is_base64url_no_pad_of_v1_payload() -> None:
    cursor = encode_cursor("id1", 0)
    expected = base64.urlsafe_b64encode(b"1\n0\nid1").rstrip(b"=").decode("ascii")
    assert cursor == expected
    assert "=" not in cursor
    assert "+" not in cursor
    assert "/" not in cursor


def test_decode_cursor_accepts_peer_payload_with_padding_stripped() -> None:
    raw = base64.urlsafe_b64encode(b"1\n12\nmy-session-id").rstrip(b"=").decode("ascii")
    item_id, index = decode_cursor(raw)
    assert item_id == "my-session-id"
    assert index == 12


def test_encode_decode_cursor_id_with_newlines() -> None:
    item_id = "a\nb\nc"
    cursor = encode_cursor(item_id, 3)
    got_id, got_index = decode_cursor(cursor)
    assert got_id == item_id
    assert got_index == 3


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not-base64!!!",
        base64.urlsafe_b64encode(b"2\n0\nid").rstrip(b"=").decode("ascii"),  # bad version
        base64.urlsafe_b64encode(b"1\nx\nid").rstrip(b"=").decode("ascii"),  # non-int index
        base64.urlsafe_b64encode(b"1\n-1\nid").rstrip(b"=").decode("ascii"),  # negative
        base64.urlsafe_b64encode(b"1\n0").rstrip(b"=").decode("ascii"),  # missing id part
        base64.urlsafe_b64encode(b"hello").rstrip(b"=").decode("ascii"),
        # Padded / standard-alphabet spellings are not valid opaque cursors.
        base64.urlsafe_b64encode(b"1\n0\nid1").decode("ascii"),  # with padding '='
        base64.b64encode(b"1\n0\n>>").decode("ascii").rstrip("="),  # contains '+'
        # Non-canonical trailing bits (same payload as MQowCmlkMQ but alias form).
        "MQowCmlkMR",
    ],
)
def test_decode_cursor_rejects_malformed(bad: str) -> None:
    with pytest.raises(TrajectoryError) as ei:
        decode_cursor(bad)
    assert ei.value.code == FATAL_INVALID_INPUT
    assert ei.value.message == MSG_INVALID_CURSOR


def test_decode_cursor_rejects_non_str() -> None:
    with pytest.raises(TypeError):
        decode_cursor(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Sort
# ---------------------------------------------------------------------------


def test_sort_listings_updated_at_desc_then_id_ordinal() -> None:
    items = [
        _item("b", updated_at="2026-01-01T00:00:00.000Z"),
        _item("a", updated_at="2026-01-02T00:00:00.000Z"),
        _item("c", updated_at="2026-01-02T00:00:00.000Z"),
        _item("z", updated_at="2025-12-31T00:00:00.000Z"),
    ]
    ordered = sort_listings(items)
    assert [i.id for i in ordered] == ["a", "c", "b", "z"]


def test_sort_listings_null_updated_at_last() -> None:
    items = [
        _item("missing", updated_at=None),
        _item("present", updated_at="2026-01-01T00:00:00.000Z"),
        _item("also-missing", updated_at=None),
    ]
    ordered = sort_listings(items)
    assert ordered[0].id == "present"
    assert [i.id for i in ordered[1:]] == ["also-missing", "missing"]


def test_compare_listing_items_utf16_id_tiebreak() -> None:
    left = _item("\U00010000", updated_at="2026-01-01T00:00:00.000Z")
    right = _item("\ue000", updated_at="2026-01-01T00:00:00.000Z")
    assert compare_listing_items(left, right) < 0
    assert compare_listing_items(right, left) > 0


# ---------------------------------------------------------------------------
# Paginate
# ---------------------------------------------------------------------------


def test_paginate_first_page_and_next_cursor() -> None:
    items = [
        _item("newer", updated_at="2026-01-02T00:00:00.000Z"),
        _item("older", updated_at="2026-01-01T00:00:00.000Z"),
    ]
    page = paginate(items, cursor=None, limit=1)
    assert isinstance(page, TrajectoryListingPage)
    assert len(page.items) == 1
    assert page.items[0].id == "newer"
    assert page.next_cursor is not None

    page2 = paginate(items, cursor=page.next_cursor, limit=1)
    assert len(page2.items) == 1
    assert page2.items[0].id == "older"
    assert page2.next_cursor is None


def test_paginate_limit_covers_all_yields_null_cursor() -> None:
    items = [_item("a", updated_at="2026-01-01T00:00:00.000Z")]
    page = paginate(items, cursor=None, limit=50)
    assert page.items[0].id == "a"
    assert page.next_cursor is None


def test_paginate_empty_items() -> None:
    page = paginate([], cursor=None, limit=10)
    assert page.items == ()
    assert page.next_cursor is None


def test_paginate_invalid_limit() -> None:
    with pytest.raises(TrajectoryError) as ei:
        paginate([], cursor=None, limit=0)
    assert ei.value.code == FATAL_INVALID_INPUT


def test_paginate_cursor_when_item_disappeared_uses_index_fallback() -> None:
    remaining = [
        _item("a", updated_at="2026-01-03T00:00:00.000Z"),
        _item("b", updated_at="2026-01-02T00:00:00.000Z"),
        _item("c", updated_at="2026-01-01T00:00:00.000Z"),
    ]
    ghost_cursor = encode_cursor("ghost", 0)
    page = paginate(remaining, cursor=ghost_cursor, limit=10)
    # start = min(0+1, 3) = 1 → items after sort a,b,c → b, c
    assert [i.id for i in page.items] == ["b", "c"]
    assert page.next_cursor is None


def test_paginate_cursor_after_matching_id() -> None:
    items = [
        _item("a", updated_at="2026-01-03T00:00:00.000Z"),
        _item("b", updated_at="2026-01-02T00:00:00.000Z"),
        _item("c", updated_at="2026-01-01T00:00:00.000Z"),
    ]
    cursor = encode_cursor("a", 0)
    page = paginate(items, cursor=cursor, limit=1)
    assert page.items[0].id == "b"
    assert page.next_cursor is not None
    page2 = paginate(items, cursor=page.next_cursor, limit=1)
    assert page2.items[0].id == "c"
    assert page2.next_cursor is None


def test_paginate_resume_matches_first_id_like_peers() -> None:
    """Peer pin: resume after the **first** current item with the cursor id.

    Distinguishes first-ID-match from index-first: cursor claims absolute index 1
    (second ``dup``), but peers resume after the first ``dup`` (start=1), not
    after index 1 (start=2). Paths distinguish the two ``dup`` rows.
    """
    items = [
        TrajectoryListing(id="dup", path="/first", updated_at="2026-01-03T00:00:00.000Z"),
        TrajectoryListing(id="dup", path="/second", updated_at="2026-01-02T00:00:00.000Z"),
        TrajectoryListing(id="tail", path="/tail", updated_at="2026-01-01T00:00:00.000Z"),
    ]
    # Cursor as if previous page ended on the second dup (index 1).
    cursor = encode_cursor("dup", 1)
    page = paginate(items, cursor=cursor, limit=10)
    # First-id-match → start = 0+1 = 1 → second dup + tail.
    # Index-first would yield start = 1+1 = 2 → tail only.
    assert [i.path for i in page.items] == ["/second", "/tail"]
    assert page.next_cursor is None


# ---------------------------------------------------------------------------
# Empty registry shell
# ---------------------------------------------------------------------------


def test_registry_is_empty_shell_and_register_roundtrip() -> None:
    # Empty-shell acceptance: no built-in listers registered by this package yet.
    # Snapshot so concurrent/shared process state is restored (do not rely on
    # clearing first to "prove" emptiness).
    snapshot = dict(_LISTERS)
    try:
        assert registered_lister_names() == frozenset(), (
            "PY-09a registry shell must be empty before per-source listers land; "
            f"found: {sorted(registered_lister_names())}"
        )
        assert get_lister("pi") is None

        class _Stub:
            @property
            def source(self):  # noqa: ANN201 — test stub
                from hypabolic_trajectory._enums import TrajectorySource

                return TrajectorySource.PI

            def list_page(self, *, root, cursor, limit):  # noqa: ANN001,ANN201
                _ = (root, cursor, limit)
                return TrajectoryListingPage(items=(), next_cursor=None)

        register_lister(_Stub())
        assert "pi" in registered_lister_names()
        assert get_lister("pi") is not None
        clear_listers_for_tests()
        assert registered_lister_names() == frozenset()
    finally:
        _LISTERS.clear()
        _LISTERS.update(snapshot)


def test_listing_dto_fields() -> None:
    item = TrajectoryListing(
        id="x",
        path="/p",
        updated_at="2026-01-01T00:00:00.000Z",
        title="t",
        size_bytes=12,
    )
    page = TrajectoryListingPage(items=(item,), next_cursor=None)
    assert page.next_cursor is None
    assert page.items[0].size_bytes == 12
    assert page.items[0].title == "t"
