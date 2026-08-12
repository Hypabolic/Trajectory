"""Optional file I/O for live session streaming (LS-09).

Poll/watch helpers that only call core apply APIs. Install intent:
``pip install hypabolic-trajectory[io]`` (stdlib only).

Not part of the pure core: host errors are distinct from stream diagnostics.
"""

from __future__ import annotations

from hypabolic_trajectory.io.file_stream import (
    FileStreamHostError,
    FileStreamOptions,
    FileTrajectoryStream,
    HOST_IO_ERROR,
    HOST_IO_NOT_FOUND,
    HOST_IO_PERMISSION,
    HOST_PATH_OUTSIDE_ROOT,
    HOST_PATH_REQUIRED,
    HOST_ROOT_REQUIRED,
)

__all__ = [
    "HOST_IO_ERROR",
    "HOST_IO_NOT_FOUND",
    "HOST_IO_PERMISSION",
    "HOST_PATH_OUTSIDE_ROOT",
    "HOST_PATH_REQUIRED",
    "HOST_ROOT_REQUIRED",
    "FileStreamHostError",
    "FileStreamOptions",
    "FileTrajectoryStream",
]
