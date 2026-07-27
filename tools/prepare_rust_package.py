#!/usr/bin/env python3
"""Copy authoritative contract assets into the generated Rust package staging path."""

from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[1]
destination = root / "rust/crates/hypabolic-trajectory/contracts"
shutil.rmtree(destination, ignore_errors=True)
destination.mkdir(parents=True)
shutil.copy2(
    root / "contracts/compatibility.json",
    destination / "compatibility.json",
)
shutil.copytree(
    root / "contracts/schemas",
    destination / "schemas",
)
print(destination)
