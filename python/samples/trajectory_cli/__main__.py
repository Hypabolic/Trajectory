"""CLI entry: ``python -m trajectory_cli``.

Local sample TUI only — not installed by the published wheel.
"""

from __future__ import annotations

import sys

from trajectory_cli.cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
