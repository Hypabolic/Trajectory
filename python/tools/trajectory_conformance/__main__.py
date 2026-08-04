"""CLI entry: ``python -m trajectory_conformance``.

Reads one protocol-v1 request JSON from stdin (or a single file path argument),
executes the declared operation, and writes exactly one response JSON object to
stdout. Logs go to stderr only.

Exit codes:
  0 — success or domain fatal-error
  2 — protocol-error only
"""

from __future__ import annotations

import sys

from trajectory_conformance.runner import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
