"""Path poll helpers → complete lines → core apply only (LS-09)."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from hypabolic_trajectory._enums import TrajectorySource
from hypabolic_trajectory.streaming.apply import TrajectoryStream
from hypabolic_trajectory.streaming.framing import split_complete_lines
from hypabolic_trajectory.streaming.types import StreamOptions, StreamUpdate

HOST_ROOT_REQUIRED = "root_required"
HOST_PATH_REQUIRED = "path_required"
HOST_PATH_OUTSIDE_ROOT = "path_outside_root"
HOST_IO_PERMISSION = "io_permission"
HOST_IO_NOT_FOUND = "io_not_found"
HOST_IO_ERROR = "io_error"

_MSG_ROOT_REQUIRED = "File stream root is required."
_MSG_PATH_REQUIRED = "File stream path is required."
_MSG_PATH_OUTSIDE_ROOT = "File stream path is outside the explicit root."
_MSG_IO_PERMISSION = "File stream could not read the path (permission denied)."
_MSG_IO_NOT_FOUND = "File stream path was not found."
_MSG_IO_ERROR = "File stream I/O failed."


class FileStreamHostError(Exception):
    """Host-side I/O or configuration error (not a stream diagnostic)."""

    def __init__(self, code: str, message: str, *, path: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        # Path is for the calling process only; never copy into StreamUpdate.
        self.path = path


@dataclass(frozen=True, slots=True, kw_only=True)
class FileStreamOptions:
    """Explicit-root file follow options."""

    root: str | Path
    path: str | Path
    source: TrajectorySource | str
    group_id: str | None = None
    stream: StreamOptions | None = None
    poll_interval: float = 0.05
    reconcile_every: int = 0  # 0 = disabled; N = full snapshot every N polls after first
    source_revision: str = "file-0"


class FileTrajectoryStream:
    """Poll a single JSONL path and apply complete segments to core streaming."""

    def __init__(
        self,
        *,
        root: Path,
        path: Path,
        stream: TrajectoryStream,
        poll_interval: float,
        reconcile_every: int,
        source_revision: str,
    ) -> None:
        self._root = root
        self._path = path
        self._stream = stream
        self._poll_interval = poll_interval
        self._reconcile_every = reconcile_every
        self._source_revision = source_revision
        self._file_offset = 0
        self._host_pending = b""
        self._first = True
        self._polls = 0
        self._closed = False
        self._identity: tuple[int, int, int] | None = None

    @classmethod
    def open(cls, options: FileStreamOptions) -> FileTrajectoryStream:
        root_raw = options.root
        path_raw = options.path
        if root_raw is None or (isinstance(root_raw, str) and not root_raw.strip()):
            raise FileStreamHostError(HOST_ROOT_REQUIRED, _MSG_ROOT_REQUIRED)
        if path_raw is None or (isinstance(path_raw, str) and not path_raw.strip()):
            raise FileStreamHostError(HOST_PATH_REQUIRED, _MSG_PATH_REQUIRED)

        root = Path(root_raw).expanduser().resolve()
        path = Path(path_raw).expanduser().resolve()
        if not _is_under_root(root, path):
            raise FileStreamHostError(
                HOST_PATH_OUTSIDE_ROOT,
                _MSG_PATH_OUTSIDE_ROOT,
                path=str(path),
            )

        stream_opts = options.stream
        if stream_opts is None:
            stream_opts = StreamOptions(source=options.source, group_id=options.group_id)
        elif options.group_id is not None and stream_opts.group_id is None:
            stream_opts = StreamOptions(
                source=stream_opts.source,
                group_id=options.group_id,
                delivery=stream_opts.delivery,
                include_provisional=stream_opts.include_provisional,
                require_complete_lines=stream_opts.require_complete_lines,
                finalize_on_close=stream_opts.finalize_on_close,
                reorder=stream_opts.reorder,
                reset_policy=stream_opts.reset_policy,
                max_pending_bytes=stream_opts.max_pending_bytes,
                max_line_bytes=stream_opts.max_line_bytes,
                normalize=stream_opts.normalize,
                ahp_protocol_version=stream_opts.ahp_protocol_version,
            )

        return cls(
            root=root,
            path=path,
            stream=TrajectoryStream.create(stream_opts),
            poll_interval=max(0.0, float(options.poll_interval)),
            reconcile_every=max(0, int(options.reconcile_every)),
            source_revision=options.source_revision,
        )

    @property
    def path(self) -> Path:
        return self._path

    @property
    def root(self) -> Path:
        return self._root

    @property
    def stream(self) -> TrajectoryStream:
        return self._stream

    @property
    def cursor(self):
        return self._stream.cursor

    def poll(self) -> StreamUpdate | None:
        """Read growth once; return a core StreamUpdate or None if unchanged."""
        if self._closed:
            return None
        size, identity = self._stat_identity()
        if size < self._file_offset:
            return self._snapshot_full(size, identity)
        if self._first:
            return self._snapshot_full(size, identity)
        if self._identity_changed(identity, size=size):
            # Inode/dev change or same-size rewrite (mtime) → full prefix.
            return self._snapshot_full(size, identity)
        if size > self._file_offset:
            return self._append_growth(size, identity)
        self._polls += 1
        if (
            self._reconcile_every > 0
            and self._polls % self._reconcile_every == 0
            and size >= 0
        ):
            return self._reconcile_snapshot(size, identity)
        self._identity = identity
        return None

    def follow(self, *, interval: float | None = None) -> Iterator[StreamUpdate]:
        """Yield non-empty updates until the process stops iterating.

        Caller owns lifetime (not a daemon). Use break/return to stop.
        """
        wait = self._poll_interval if interval is None else max(0.0, float(interval))
        while not self._closed:
            update = self.poll()
            if update is not None and update.kind != "unchanged":
                yield update
            if wait > 0:
                time.sleep(wait)

    def finish(self) -> StreamUpdate:
        """Finish the underlying core stream (does not close the file handle).

        Forwards any host-held incomplete line into core pending first so
        ``finish`` can commit a final unterminated line (core finish only sees
        core ``pending_bytes``).

        Host pending is retained until core ``apply_append`` succeeds. On
        non-success (error / reset-required / sequence-gap / resync-required),
        the pending buffer is kept, the failed update is returned, and
        ``finish`` is not applied.
        """
        if self._host_pending:
            update = self._stream.apply_append(
                self._host_pending, source_revision=self._source_revision
            )
            if update.kind not in ("updated", "unchanged"):
                return update
            self._host_pending = b""
        return self._stream.finish()

    def close(self) -> None:
        self._closed = True

    def __enter__(self) -> FileTrajectoryStream:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _snapshot_full(
        self, size: int, identity: tuple[int, int, int]
    ) -> StreamUpdate:
        material = self._read_range(0, size)
        self._file_offset = size
        complete, pending = split_complete_lines(material)
        self._host_pending = pending
        self._first = False
        self._polls += 1
        self._identity = identity
        return self._stream.apply_snapshot(
            complete, source_revision=self._source_revision
        )

    def _reconcile_snapshot(
        self, size: int, identity: tuple[int, int, int]
    ) -> StreamUpdate | None:
        material = self._read_range(0, size)
        complete, pending = split_complete_lines(material)
        # Reconcile only when committed complete prefix is stable under host framing.
        self._host_pending = pending
        self._file_offset = size
        self._identity = identity
        update = self._stream.apply_snapshot(
            complete, source_revision=self._source_revision
        )
        if update.kind == "unchanged":
            return None
        return update

    def _append_growth(
        self, size: int, identity: tuple[int, int, int]
    ) -> StreamUpdate | None:
        chunk = self._read_range(self._file_offset, size)
        self._file_offset = size
        buf = self._host_pending + chunk
        complete, pending = split_complete_lines(buf)
        self._host_pending = pending
        self._polls += 1
        self._identity = identity
        if not complete:
            return None
        update = self._stream.apply_append(
            complete, source_revision=self._source_revision
        )
        if update.kind == "unchanged":
            return None
        return update

    def _identity_changed(self, identity: tuple[int, int, int], *, size: int) -> bool:
        if self._identity is None:
            return False
        # Inode/device change is authoritative (atomic replace). Same-size
        # in-place rewrite typically keeps the inode but updates mtime_ns.
        prev_dev, prev_ino, prev_mtime = self._identity
        dev, ino, mtime_ns = identity
        if (dev, ino) != (prev_dev, prev_ino):
            return True
        return size == self._file_offset and mtime_ns != prev_mtime

    def _stat_identity(self) -> tuple[int, tuple[int, int, int]]:
        try:
            st = os.stat(self._path)
            mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
            return int(st.st_size), (int(st.st_dev), int(st.st_ino), int(mtime_ns))
        except FileNotFoundError as exc:
            raise FileStreamHostError(
                HOST_IO_NOT_FOUND, _MSG_IO_NOT_FOUND, path=str(self._path)
            ) from exc
        except PermissionError as exc:
            raise FileStreamHostError(
                HOST_IO_PERMISSION, _MSG_IO_PERMISSION, path=str(self._path)
            ) from exc
        except OSError as exc:
            raise FileStreamHostError(
                HOST_IO_ERROR, _MSG_IO_ERROR, path=str(self._path)
            ) from exc

    def _read_range(self, start: int, end: int) -> bytes:
        if end <= start:
            return b""
        try:
            with open(self._path, "rb") as handle:
                handle.seek(start)
                return handle.read(end - start)
        except FileNotFoundError as exc:
            raise FileStreamHostError(
                HOST_IO_NOT_FOUND, _MSG_IO_NOT_FOUND, path=str(self._path)
            ) from exc
        except PermissionError as exc:
            raise FileStreamHostError(
                HOST_IO_PERMISSION, _MSG_IO_PERMISSION, path=str(self._path)
            ) from exc
        except OSError as exc:
            raise FileStreamHostError(
                HOST_IO_ERROR, _MSG_IO_ERROR, path=str(self._path)
            ) from exc


def _is_under_root(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
