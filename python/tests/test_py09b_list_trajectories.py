"""PY-09b unit tests: ``list_trajectories`` free-function dispatcher.

Acceptance:
- Dispatch by registry only (no per-source body branches).
- ``unknown_source`` for unknown wire names.
- ``listing_unavailable`` when no lister is registered.
- ``invalid_input`` for bad limit/cursor (via lister/paginate).
- Explicit ``root`` required; no home-dir defaults.
- Missing store → empty page.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import hypabolic_trajectory as ht
from hypabolic_trajectory._enums import TrajectorySource
from hypabolic_trajectory.dto import TrajectoryListing, TrajectoryListingPage
from hypabolic_trajectory.errors import (
    FATAL_INVALID_INPUT,
    FATAL_LISTING_UNAVAILABLE,
    FATAL_UNKNOWN_SOURCE,
    TrajectoryError,
)
from hypabolic_trajectory.listing.common import (
    DEFAULT_LISTING_LIMIT,
    MSG_INVALID_CURSOR,
    MSG_INVALID_LIMIT,
    decode_cursor,
)
from hypabolic_trajectory.listing.protocol import (
    clear_listers_for_tests,
    get_lister,
    register_lister,
    registered_lister_names,
)


# ---------------------------------------------------------------------------
# Signature / surface
# ---------------------------------------------------------------------------


def test_list_trajectories_is_root_export() -> None:
    assert "list_trajectories" in ht.__all__
    assert ht.list_trajectories is not None


def test_default_limit_is_contract_default() -> None:
    import inspect

    sig = inspect.signature(ht.list_trajectories)
    assert sig.parameters["limit"].default == DEFAULT_LISTING_LIMIT == 50


# ---------------------------------------------------------------------------
# unknown_source / listing_unavailable
# ---------------------------------------------------------------------------


def test_unknown_source_string_raises() -> None:
    with pytest.raises(TrajectoryError) as ei:
        ht.list_trajectories(source="not-a-source", root="/tmp")
    assert ei.value.code == FATAL_UNKNOWN_SOURCE
    assert ei.value.__cause__ is None
    assert ei.value.__context__ is None


def test_listing_unavailable_when_lister_unregistered() -> None:
    """Known enum source with no registry entry → listing_unavailable."""
    snapshot = {name: get_lister(name) for name in registered_lister_names()}
    try:
        clear_listers_for_tests()
        assert get_lister("pi") is None
        with pytest.raises(TrajectoryError) as ei:
            ht.list_trajectories(source=TrajectorySource.PI, root="/tmp")
        assert ei.value.code == FATAL_LISTING_UNAVAILABLE
        assert "pi" in ei.value.message
        assert ei.value.__cause__ is None
        assert ei.value.__context__ is None
    finally:
        clear_listers_for_tests()
        for lister in snapshot.values():
            if lister is not None:
                register_lister(lister)


def test_dispatch_uses_registry_not_hardcoded_branches(tmp_path: Path) -> None:
    """A custom registered lister is invoked; no source-name branching needed."""

    class FakeLister:
        @property
        def source(self) -> TrajectorySource:
            return TrajectorySource.PI

        def list_page(
            self,
            *,
            root: str | Path,
            cursor: str | None,
            limit: int,
        ) -> TrajectoryListingPage:
            _ = (cursor, limit)
            return TrajectoryListingPage(
                items=(
                    TrajectoryListing(
                        id="fake",
                        path=str(Path(root) / "fake.jsonl"),
                    ),
                ),
                next_cursor=None,
            )

    snapshot = {name: get_lister(name) for name in registered_lister_names()}
    try:
        clear_listers_for_tests()
        register_lister(FakeLister())
        page = ht.list_trajectories(source="pi", root=tmp_path)
        assert len(page.items) == 1
        assert page.items[0].id == "fake"
        assert page.items[0].path.endswith("fake.jsonl")
    finally:
        clear_listers_for_tests()
        for lister in snapshot.values():
            if lister is not None:
                register_lister(lister)


# ---------------------------------------------------------------------------
# invalid_input (limit / cursor) at free-function entry
# ---------------------------------------------------------------------------


def test_invalid_limit_raises_invalid_input(tmp_path: Path) -> None:
    with pytest.raises(TrajectoryError) as ei:
        ht.list_trajectories(source="pi", root=tmp_path, limit=0)
    assert ei.value.code == FATAL_INVALID_INPUT
    assert ei.value.message == MSG_INVALID_LIMIT


def test_invalid_limit_too_large(tmp_path: Path) -> None:
    with pytest.raises(TrajectoryError) as ei:
        ht.list_trajectories(source="pi", root=tmp_path, limit=1001)
    assert ei.value.code == FATAL_INVALID_INPUT
    assert ei.value.message == MSG_INVALID_LIMIT


def test_invalid_cursor_raises_invalid_input(tmp_path: Path) -> None:
    with pytest.raises(TrajectoryError) as ei:
        ht.list_trajectories(source="pi", root=tmp_path, cursor="not-a-cursor")
    assert ei.value.code == FATAL_INVALID_INPUT
    assert ei.value.message == MSG_INVALID_CURSOR


def test_bool_limit_typeerror(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        ht.list_trajectories(source="pi", root=tmp_path, limit=True)  # type: ignore[arg-type]


def test_bad_root_type_typeerror() -> None:
    with pytest.raises(TypeError):
        ht.list_trajectories(source="pi", root=123)  # type: ignore[arg-type]


def test_bad_cursor_type_typeerror(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        ht.list_trajectories(source="pi", root=tmp_path, cursor=b"x")  # type: ignore[arg-type]


def test_bad_source_type_typeerror(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        ht.list_trajectories(source=1, root=tmp_path)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Happy path: built-in pi lister via free function
# ---------------------------------------------------------------------------


def test_missing_store_empty_page(tmp_path: Path) -> None:
    page = ht.list_trajectories(
        source=TrajectorySource.PI,
        root=tmp_path / "no-such",
    )
    assert page.items == ()
    assert page.next_cursor is None


def test_pi_listing_via_free_function(tmp_path: Path) -> None:
    project = tmp_path / "sessions" / "proj"
    project.mkdir(parents=True)
    older = project / "older.jsonl"
    newer = project / "newer.jsonl"
    older.write_text("{}\n", encoding="utf-8")
    newer.write_text("{}\n", encoding="utf-8")
    os.utime(older, (1_704_067_200, 1_704_067_200))
    os.utime(newer, (1_704_153_600, 1_704_153_600))

    page1 = ht.list_trajectories(source="pi", root=tmp_path, limit=1)
    assert len(page1.items) == 1
    assert page1.items[0].id == "newer"
    assert page1.next_cursor is not None
    item_id, index = decode_cursor(page1.next_cursor)
    assert item_id == "newer"
    assert index == 0

    page2 = ht.list_trajectories(
        source="pi", root=str(tmp_path), cursor=page1.next_cursor, limit=1
    )
    assert len(page2.items) == 1
    assert page2.items[0].id == "older"
    assert page2.next_cursor is None


def test_enum_and_str_source_equivalent(tmp_path: Path) -> None:
    page_str = ht.list_trajectories(source="hermes", root=tmp_path)
    page_enum = ht.list_trajectories(source=TrajectorySource.HERMES, root=tmp_path)
    assert page_str == page_enum
    assert page_str.items == ()
    assert page_str.next_cursor is None


def test_ahp_empty_stub_via_dispatcher(tmp_path: Path) -> None:
    page = ht.list_trajectories(source="ahp", root=tmp_path, limit=10)
    assert page.items == ()
    assert page.next_cursor is None


def test_ahp_invalid_cursor_rejected_at_free_function_entry(tmp_path: Path) -> None:
    """Dispatcher applies invalid_input even when the AHP stub ignores cursor."""
    with pytest.raises(TrajectoryError) as ei:
        ht.list_trajectories(source="ahp", root=tmp_path, cursor="not-a-cursor")
    assert ei.value.code == FATAL_INVALID_INPUT
    assert ei.value.message == MSG_INVALID_CURSOR


def test_all_builtin_sources_have_registered_listers() -> None:
    for wire in (
        "pi",
        "claude-code",
        "codex",
        "openclaw",
        "hermes",
        "ahp",
    ):
        assert get_lister(wire) is not None, wire
        # Free function must not raise listing_unavailable for built-ins.
        page = ht.list_trajectories(source=wire, root="/no/such/path/for/list")
        assert isinstance(page, TrajectoryListingPage)


def test_no_home_dir_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Library path never consults HOME; root is always explicit."""
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    (fake_home / ".pi" / "agent" / "sessions" / "p").mkdir(parents=True)
    planted = fake_home / ".pi" / "agent" / "sessions" / "p" / "planted.jsonl"
    planted.write_text("{}\n", encoding="utf-8")

    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("PI_CODING_AGENT_DIR", raising=False)

    # Explicit empty-ish unrelated root — must not discover planted under HOME.
    empty_root = tmp_path / "explicit-empty"
    empty_root.mkdir()
    page = ht.list_trajectories(source="pi", root=empty_root)
    assert page.items == ()

    # Calling with the fake agent root still works when explicit.
    page2 = ht.list_trajectories(source="pi", root=fake_home / ".pi" / "agent")
    assert any(i.id == "planted" for i in page2.items)
