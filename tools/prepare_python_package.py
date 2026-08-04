#!/usr/bin/env python3
"""Copy authoritative contract assets into the Python package staging tree.

Always overwrites staged interiors when monorepo sources exist (rmtree + recopy),
matching Rust prepare semantics. Existence-shaped no-ops are forbidden.

Staged paths (gitignored):
  python/src/hypabolic_trajectory/contracts/
  python/src/hypabolic_trajectory/runtime-capabilities.json
  python/LICENSE  (copy of repo-root LICENSE for hatchling / PEP 639)

Usage:
  python3 tools/prepare_python_package.py
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path


def _trees_equal(src: Path, dst: Path) -> bool:
    """Return True if dst exists and is byte-identical to src tree/file.

    Uses deep (content) comparison only — never size/mtime short-circuit.
    """
    if not dst.exists():
        return False
    if src.is_file() and dst.is_file():
        return filecmp.cmp(src, dst, shallow=False)
    if src.is_dir() and dst.is_dir():
        src_files: list[Path] = sorted(p for p in src.rglob("*") if p.is_file())
        dst_files: list[Path] = sorted(p for p in dst.rglob("*") if p.is_file())
        src_rels = [p.relative_to(src).as_posix() for p in src_files]
        dst_rels = [p.relative_to(dst).as_posix() for p in dst_files]
        if src_rels != dst_rels:
            return False
        for sp, dp in zip(src_files, dst_files):
            if not filecmp.cmp(sp, dp, shallow=False):
                return False
        return True
    return False


def prepare(root: Path) -> list[str]:
    """Stage contracts, capabilities, and LICENSE into python/.

    When monorepo sources are absent (sdist-only tree), no-op with a note.
    When present: always overwrite unless already byte-identical (optional short-circuit).
    Partial monorepo trees (some sources present, others missing) exit non-zero.
    """
    changed: list[str] = []
    python_root = root / "python"
    package_src = python_root / "src" / "hypabolic_trajectory"

    contracts_src = root / "contracts"
    compatibility_src = contracts_src / "compatibility.json"
    schemas_src = contracts_src / "schemas"
    caps_src = python_root / "runtime-capabilities.json"
    license_src = root / "LICENSE"

    required = {
        "contracts/compatibility.json": compatibility_src.is_file(),
        "contracts/schemas/": schemas_src.is_dir(),
        "python/runtime-capabilities.json": caps_src.is_file(),
        "LICENSE": license_src.is_file(),
        "python/src/hypabolic_trajectory/": package_src.is_dir(),
    }
    present_count = sum(1 for ok in required.values() if ok)
    if present_count == 0:
        # Sdist-only / isolated install: monorepo sources fully absent.
        print(
            json_status(
                "skipped",
                "monorepo sources absent; leaving staged package interiors unchanged",
                [],
            )
        )
        return []
    if present_count != len(required):
        missing = [path for path, ok in required.items() if not ok]
        raise SystemExit(
            "prepare_python_package: incomplete monorepo sources "
            f"(missing: {missing}). Refusing existence-shaped partial skip."
        )


    contracts_dst = package_src / "contracts"
    caps_dst = package_src / "runtime-capabilities.json"
    license_dst = python_root / "LICENSE"

    # --- contracts tree: always rmtree + recopy unless byte-identical ---
    staged_contracts_tmp = package_src / ".contracts-staging"
    if staged_contracts_tmp.exists():
        shutil.rmtree(staged_contracts_tmp)
    staged_contracts_tmp.mkdir(parents=True)
    shutil.copy2(compatibility_src, staged_contracts_tmp / "compatibility.json")
    shutil.copytree(schemas_src, staged_contracts_tmp / "schemas")

    if _trees_equal(staged_contracts_tmp, contracts_dst):
        shutil.rmtree(staged_contracts_tmp)
    else:
        if contracts_dst.exists():
            shutil.rmtree(contracts_dst)
        staged_contracts_tmp.rename(contracts_dst)
        changed.append(str(contracts_dst.relative_to(root)))

    # --- runtime-capabilities.json ---
    if not caps_dst.is_file() or not filecmp.cmp(caps_src, caps_dst, shallow=False):
        shutil.copy2(caps_src, caps_dst)
        changed.append(str(caps_dst.relative_to(root)))

    # --- LICENSE at python/ project root ---
    if not license_dst.is_file() or not filecmp.cmp(license_src, license_dst, shallow=False):
        shutil.copy2(license_src, license_dst)
        changed.append(str(license_dst.relative_to(root)))

    print(json_status("prepared", "overwrite semantics", changed))
    return changed


def json_status(status: str, message: str, files: list[str]) -> str:
    import json

    return json.dumps(
        {"status": status, "message": message, "files": files},
        indent=2,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=None)
    args = parser.parse_args()
    if args.repository_root is not None:
        root = args.repository_root.resolve()
    else:
        root = Path(__file__).resolve().parents[1]
    try:
        prepare(root)
    except OSError as exc:
        print(f"prepare_python_package failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
