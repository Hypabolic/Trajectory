#!/usr/bin/env python3
"""Two-column Python pack-smoke (PY-14a).

Normative steps (docs/python-implementation-spec.md §6):
  1. prepare (overwrite) then build sdist + wheel from python/
  2. Assert sdist column members (after stripping versioned top-level dir)
  3. Assert wheel column members
  4. Core dep audit: fail only on unconditional Requires-Dist for
     opentelemetry-* or sqlite drivers; allow extra == "otel" markers
  5. Isolated temp dir without monorepo contracts: install sdist, import,
     open interior contracts + runtime-capabilities via importlib.resources
  6. No console scripts in published wheel METADATA

Usage (repo root):
  python3 tools/python_package_smoke.py
  python3 tools/python_package_smoke.py --outdir artifacts/ci/pypi
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Forbidden / required path patterns (two-column matrix)
# ---------------------------------------------------------------------------

FORBIDDEN_PREFIXES = (
    "tests/",
    "samples/",
    "tools/",
    "__pycache__/",
    ".pytest_cache/",
    ".venv/",
)

# Distribution names (normalized: lower, _ → -) that must not be bare deps.
SQLITE_DIST_NAMES = frozenset(
    {
        "sqlite",
        "sqlite3",
        "pysqlite",
        "pysqlite3",
        "pysqlite3-binary",
        "pysqlite3-wheels",
        "apsw",
    }
)
OTEL_DIST_RE = re.compile(r"(?i)^opentelemetry([._-]|$)")


def _normalize_dist_name(name: str) -> str:
    """PEP 503 normalize: lower case; runs of ``-``, ``_``, ``.`` → single ``-``."""
    return re.sub(r"[-_.]+", "-", name.strip().lower())



def _is_forbidden_core_dist(name: str) -> bool:
    n = _normalize_dist_name(name)
    if OTEL_DIST_RE.match(n):
        return True
    if n in SQLITE_DIST_NAMES:
        return True
    # Prefix variants: pysqlite3-*, sqlite-* drivers
    if n.startswith("pysqlite3-") or n.startswith("pysqlite-"):
        return True
    return False



def fail(msg: str) -> None:
    print(f"python_package_smoke: FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def info(msg: str) -> None:
    print(f"python_package_smoke: {msg}")


def run(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None) -> None:
    display = " ".join(cmd)
    info(f"$ {display}" + (f"  (cwd={cwd})" if cwd else ""))
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def python_version_tuple(executable: str) -> tuple[int, int] | None:
    try:
        out = subprocess.check_output(
            [executable, "-c", "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        major_s, minor_s = out.split(".", 1)
        return int(major_s), int(minor_s)
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None


def resolve_python311(root: Path, explicit: str | None) -> str:
    """Pick a Python >=3.11 for venv/build (package Requires-Python)."""
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    # Prefer current interpreter when it already satisfies Requires-Python.
    candidates.append(sys.executable)
    # Common local toolchains (uv, homebrew, package venv).
    candidates.extend(
        [
            str(root / "python" / ".venv" / "bin" / "python"),
            str(root / "python" / ".venv" / "bin" / "python3"),
            "python3.13",
            "python3.12",
            "python3.11",
            "python3",
        ]
    )
    seen: set[str] = set()
    for cand in candidates:
        if not cand or cand in seen:
            continue
        seen.add(cand)
        # Resolve PATH lookups
        resolved = cand
        if "/" not in cand and "\\" not in cand:
            which = shutil.which(cand)
            if not which:
                continue
            resolved = which
        elif not Path(resolved).exists():
            continue
        ver = python_version_tuple(resolved)
        if ver is not None and ver >= (3, 11):
            info(f"using Python {ver[0]}.{ver[1]} at {resolved}")
            return resolved
    fail(
        "need Python >=3.11 for pack-smoke (Requires-Python). "
        "Pass --python /path/to/python3.11+ or create python/.venv."
    )
    raise AssertionError("unreachable")



def strip_sdist_prefix(name: str) -> str | None:
    """Strip single top-level ``{normalized_name}-{version}/`` prefix.

    Returns the interior path, or None if the entry is the top-level dir itself.
    """
    parts = name.split("/", 1)
    if len(parts) == 1:
        # Directory entry for the versioned root, or a bare file (unexpected).
        return None if name.endswith("/") or "/" not in name.rstrip("/") else name
    # Standard: hypabolic_trajectory-0.1.0/...
    return parts[1]


def list_sdist_members(sdist: Path) -> set[str]:
    members: set[str] = set()
    with tarfile.open(sdist, "r:gz") as tf:
        for m in tf.getmembers():
            name = m.name
            # Normalise to forward slashes without leading ./
            name = name.lstrip("./")
            interior = strip_sdist_prefix(name)
            if interior is None or interior == "":
                continue
            # Drop trailing slash for directories so we can check file paths
            members.add(interior.rstrip("/"))
    return members


def list_wheel_members(wheel: Path) -> set[str]:
    members: set[str] = set()
    with zipfile.ZipFile(wheel) as zf:
        for name in zf.namelist():
            name = name.lstrip("./")
            members.add(name.rstrip("/"))
    return members


def read_wheel_metadata(wheel: Path) -> str:
    with zipfile.ZipFile(wheel) as zf:
        meta_names = [
            n
            for n in zf.namelist()
            if n.endswith(".dist-info/METADATA") and n.count("/") == 1
        ]
        if len(meta_names) != 1:
            fail(f"expected exactly one *.dist-info/METADATA in {wheel.name}, got {meta_names}")
        return zf.read(meta_names[0]).decode("utf-8")


def parse_metadata_headers(text: str) -> dict[str, list[str]]:
    """Parse RFC 822-style METADATA headers (multi-value aware)."""
    headers: dict[str, list[str]] = {}
    # Headers end at the first blank line.
    header_blob, _, _body = text.partition("\n\n")
    if header_blob == text:
        header_blob, _, _body = text.partition("\r\n\r\n")
    current_key: str | None = None
    for raw in header_blob.splitlines():
        if not raw:
            continue
        if raw[0] in " \t" and current_key is not None:
            # Continuation
            headers[current_key][-1] += " " + raw.strip()
            continue
        if ":" not in raw:
            continue
        key, _, value = raw.partition(":")
        key = key.strip()
        value = value.strip()
        headers.setdefault(key, []).append(value)
        current_key = key
    return headers


def assert_sdist_column(members: set[str], schema_names: list[str]) -> None:
    required = {
        "src/hypabolic_trajectory/contracts/compatibility.json",
        "src/hypabolic_trajectory/runtime-capabilities.json",
        "src/hypabolic_trajectory/py.typed",
        "LICENSE",
        "README.md",
        "pyproject.toml",
    }
    for schema in schema_names:
        required.add(f"src/hypabolic_trajectory/contracts/schemas/{schema}")
    missing = sorted(required - members)
    if missing:
        fail(f"sdist missing required members: {missing}")

    # Forbidden prefixes under the stripped tree.
    bad: list[str] = []
    for m in members:
        for prefix in FORBIDDEN_PREFIXES:
            if m == prefix.rstrip("/") or m.startswith(prefix):
                bad.append(m)
                break
        # Also ban nested caches
        if "/__pycache__/" in f"/{m}/" or m.endswith(".pyc"):
            bad.append(m)
    if bad:
        fail(f"sdist contains forbidden members: {sorted(set(bad))}")


def assert_wheel_column(members: set[str], schema_names: list[str], meta: str) -> None:
    required = {
        "hypabolic_trajectory/contracts/compatibility.json",
        "hypabolic_trajectory/runtime-capabilities.json",
        "hypabolic_trajectory/py.typed",
    }
    for schema in schema_names:
        required.add(f"hypabolic_trajectory/contracts/schemas/{schema}")
    missing = sorted(required - members)
    if missing:
        fail(f"wheel missing required members: {missing}")

    # LICENSE must live under *.dist-info/licenses/ (PEP 639), not package root.
    license_ok = any(
        re.search(r"(^|/)\S+\.dist-info/licenses/LICENSE$", m) is not None
        for m in members
    )
    if not license_ok:
        fail(
            "wheel missing PEP 639 License-File path "
            "('*.dist-info/licenses/LICENSE')"
        )
    if "hypabolic_trajectory/LICENSE" in members:
        fail("wheel must not ship bare package-root LICENSE (use dist-info/licenses/)")


    bad: list[str] = []
    for m in members:
        # Forbidden under package payload
        for prefix in ("hypabolic_trajectory/tests/", "tests/", "samples/", "tools/"):
            if m.startswith(prefix):
                bad.append(m)
        if "/__pycache__/" in f"/{m}/" or m.endswith(".pyc"):
            bad.append(m)
    if bad:
        fail(f"wheel contains forbidden members: {sorted(set(bad))}")

    # Tag: py3-none-any only (checked via filename by caller).

    headers = parse_metadata_headers(meta)

    # Summary non-empty
    summary = (headers.get("Summary") or [""])[0].strip()
    if not summary:
        fail("wheel METADATA Summary is empty")

    # Description / content type from README
    desc_type = (headers.get("Description-Content-Type") or [""])[0].strip()
    if not desc_type:
        fail("wheel METADATA missing Description-Content-Type")
    # Body after headers should be non-empty description
    body = meta.split("\n\n", 1)
    if len(body) < 2 or not body[1].strip():
        # Some hatch versions put description only in headers; also accept
        # multi-line Description header.
        desc_headers = headers.get("Description") or []
        if not any(d.strip() for d in desc_headers):
            fail("wheel METADATA missing Description derived from README")

    # SPDX License-Expression: MIT only (PEP 639 / hatchling>=1.27). No License: fallback.
    license_exprs = headers.get("License-Expression") or []
    if len(license_exprs) != 1 or license_exprs[0].strip() != "MIT":
        fail(
            "wheel METADATA must have exactly License-Expression: MIT "
            f"(got {license_exprs!r})"
        )


    # License-File entry must reference LICENSE (path may be relative)
    license_files = headers.get("License-File") or []
    if not any(re.search(r"(^|/)LICENSE$", lf.strip()) for lf in license_files):
        fail(f"wheel METADATA missing License-File for LICENSE (got {license_files})")

    # Console scripts in METADATA (if declared). Empty Entry-Points keys alone are rare;
    # fail on explicit console_scripts section text.
    if re.search(r"(?im)^\[\s*console_scripts\s*\]", meta) or re.search(
        r"(?im)^Entry-Points:\s*.*console_scripts", meta
    ):
        fail("wheel METADATA declares console_scripts entry points (forbidden)")


def _marker_true_for_bare_install(marker: str) -> bool:
    """Return True when a Requires-Dist marker holds for bare pip install.

    Bare install: Python >=3.11, no extras activated. Spec §6: fail only on
    unconditional Requires-Dist (no marker) or markers true for bare install.

    Positive extra gates (``extra == "otel"``) are bare-false. Negative extra
    gates (``extra != "otel"``) are bare-true and must not hide otel/sqlite.

    Compound markers use packaging.markers when available. Fallback only accepts
    an *exact* positive ``extra == "…"`` gate as bare-false; any other
    compound/unknown form is fail-closed bare-true.
    """
    m = marker.strip()
    if not m:
        return True
    try:
        from packaging.markers import Marker  # type: ignore[import-untyped]

        return bool(
            Marker(m).evaluate(
                {
                    "python_version": "3.11",
                    "python_full_version": "3.11.0",
                    "extra": "",
                }
            )
        )
    except Exception:
        pass

    ml = m.lower().strip()
    # Fallback: ONLY a pure positive extra equality is bare-false.
    if re.fullmatch(r"""extra\s*==\s*['"][^'"]+['"]""", ml):
        return False
    if re.fullmatch(r"""extra\s*!=\s*['"][^'"]+['"]""", ml):
        return True
    if re.fullmatch(
        r"""python_version\s*<\s*["']3\.(0|1|2|3|4|5|6|7|8|9|10)["']""", ml
    ):
        return False
    if re.fullmatch(r"""python_version\s*<\s*["']3["']""", ml):
        return False
    # Default fail-closed (includes compounds like `extra == "otel" or …`).
    return True


def audit_requires_dist(meta: str) -> None:
    """Fail on bare-install Requires-Dist for otel/sqlite; allow extra markers."""
    headers = parse_metadata_headers(meta)
    requires = headers.get("Requires-Dist") or []
    bad: list[str] = []
    for req in requires:
        if ";" in req:
            name_part, marker = req.split(";", 1)
            if not _marker_true_for_bare_install(marker):
                continue
        else:
            name_part = req
        name = name_part.strip().split()[0] if name_part.strip() else ""
        name = re.split(r"[<>=!~\[]", name, maxsplit=1)[0].strip()
        if _is_forbidden_core_dist(name):
            bad.append(req)
    if bad:
        fail(
            "unconditional Requires-Dist must not include opentelemetry-* or "
            f"sqlite drivers (extra-gated ok): {bad}"
        )
    provides = headers.get("Provides-Extra") or []
    if "otel" not in provides:
        fail(f"METADATA must declare Provides-Extra: otel (got {provides})")


def assert_no_console_scripts_entry_points(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as zf:
        for name in zf.namelist():
            if name.endswith(".dist-info/entry_points.txt"):
                content = zf.read(name).decode("utf-8")
                if not content.strip():
                    continue
                if re.search(r"(?im)^\[console_scripts\]\s*$", content):
                    lines = content.splitlines()
                    in_cs = False
                    for line in lines:
                        s = line.strip()
                        if s.startswith("[") and s.endswith("]"):
                            in_cs = s.lower() == "[console_scripts]"
                            continue
                        if in_cs and s and not s.startswith("#"):
                            fail(f"console_scripts entry found in {name}: {s}")


def _clean_env() -> dict[str, str]:
    """Environment for isolated probes: drop PYTHONPATH/PYTHONHOME influence."""
    env = os.environ.copy()
    for key in (
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "PYTHONUSERBASE",
        "PYTHONSAFEPATH",
    ):
        env.pop(key, None)
    return env


def _assert_freeze_clean(pip: Path, label: str) -> None:
    freeze = subprocess.check_output(
        [str(pip), "freeze"], text=True, env=_clean_env()
    )
    for line in freeze.splitlines():
        dist = line.split("==", 1)[0].split("@", 1)[0].strip()
        if _is_forbidden_core_dist(dist):
            fail(f"core {label} install pulled forbidden dist: {line}")


def _import_interiors_probe(
    py: Path, schema_names: list[str], *, cwd: Path
) -> None:
    schema_list = ", ".join(repr(s) for s in schema_names)
    probe = f"""
import importlib.resources as res
import hypabolic_trajectory as ht
root = res.files(ht)
contracts = root.joinpath("contracts")
assert contracts.joinpath("compatibility.json").is_file(), "compatibility.json missing"
for schema in [{schema_list}]:
    p = contracts.joinpath("schemas", schema)
    assert p.is_file(), f"missing schema {{schema}}"
caps = root.joinpath("runtime-capabilities.json")
assert caps.is_file(), "runtime-capabilities.json missing"
assert root.joinpath("py.typed").is_file(), "py.typed missing"
print("isolated-import-ok")
"""
    result = subprocess.run(
        [str(py), "-I", "-c", probe],
        check=True,
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=_clean_env(),
    )
    if "isolated-import-ok" not in result.stdout:
        fail(
            f"isolated import probe failed: stdout={result.stdout!r} "
            f"stderr={result.stderr!r}"
        )


def isolated_sdist_install(
    sdist: Path, schema_names: list[str], python_exe: str
) -> None:
    """Install sdist in a clean venv without monorepo contracts; import interiors."""
    with tempfile.TemporaryDirectory(prefix="py-pack-smoke-sdist-") as tmp:
        tmp_path = Path(tmp)
        venv = tmp_path / "venv"
        run([python_exe, "-m", "venv", str(venv)], env=_clean_env())
        if os.name == "nt":
            pip = venv / "Scripts" / "pip"
            py = venv / "Scripts" / "python"
        else:
            pip = venv / "bin" / "pip"
            py = venv / "bin" / "python"
        run([str(pip), "install", "--upgrade", "pip"], env=_clean_env())
        run([str(pip), "install", str(sdist)], env=_clean_env())
        _assert_freeze_clean(pip, "sdist")
        _import_interiors_probe(py, schema_names, cwd=tmp_path)
        info("isolated sdist install + importlib.resources interiors: ok")


def isolated_wheel_install(wheel: Path, python_exe: str) -> None:
    """Clean venv: pip install core wheel must not pull otel/sqlite (§6 step 4)."""
    with tempfile.TemporaryDirectory(prefix="py-pack-smoke-wheel-") as tmp:
        tmp_path = Path(tmp)
        venv = tmp_path / "venv"
        run([python_exe, "-m", "venv", str(venv)], env=_clean_env())
        if os.name == "nt":
            pip = venv / "Scripts" / "pip"
            py = venv / "Scripts" / "python"
        else:
            pip = venv / "bin" / "pip"
            py = venv / "bin" / "python"
        run([str(pip), "install", "--upgrade", "pip"], env=_clean_env())
        run([str(pip), "install", str(wheel)], env=_clean_env())
        _assert_freeze_clean(pip, "wheel")
        result = subprocess.run(
            [
                str(py),
                "-I",
                "-c",
                "import hypabolic_trajectory as ht; print(ht.__name__)",
            ],
            check=True,
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            env=_clean_env(),
        )
        if "hypabolic_trajectory" not in result.stdout:
            fail(f"wheel import failed: {result.stdout!r} {result.stderr!r}")
        info("isolated core wheel install (no otel/sqlite): ok")



def find_artifacts(outdir: Path) -> tuple[Path, Path]:
    sdists = sorted(outdir.glob("*.tar.gz"))
    wheels = sorted(outdir.glob("*.whl"))
    if len(sdists) != 1:
        fail(f"expected exactly one sdist in {outdir}, found {sdists}")
    if len(wheels) != 1:
        fail(f"expected exactly one wheel in {outdir}, found {wheels}")
    wheel = wheels[0]
    if not wheel.name.endswith("py3-none-any.whl"):
        fail(f"wheel tag must be py3-none-any for first ship, got {wheel.name}")
    return sdists[0], wheel


def schema_file_names(root: Path) -> list[str]:
    schemas = root / "contracts" / "schemas"
    if not schemas.is_dir():
        fail(f"missing {schemas}")
    return sorted(p.name for p in schemas.iterdir() if p.is_file())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=None,
        help="Monorepo root (default: parent of tools/)",
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=None,
        help="Build output directory (default: <root>/artifacts/ci/pypi)",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Skip prepare+build; only inspect existing artifacts in --outdir",
    )
    parser.add_argument(
        "--skip-isolated-install",
        action="store_true",
        help="Skip isolated sdist install (archive asserts + METADATA audit only)",
    )
    parser.add_argument(
        "--python",
        default=None,
        help="Python >=3.11 executable for build/venv (default: auto-detect)",
    )
    args = parser.parse_args()
    root = (
        args.repository_root.resolve()
        if args.repository_root is not None
        else Path(__file__).resolve().parents[1]
    )
    outdir = (
        args.outdir.resolve()
        if args.outdir is not None
        else root / "artifacts" / "ci" / "pypi"
    )
    python_dir = root / "python"
    if not (python_dir / "pyproject.toml").is_file():
        fail(f"missing {python_dir / 'pyproject.toml'}")

    python_exe = resolve_python311(root, args.python)
    schemas = schema_file_names(root)

    if not args.skip_build:
        # 1. prepare (overwrite) then build
        run(
            [
                python_exe,
                str(root / "tools" / "prepare_python_package.py"),
                "--repository-root",
                str(root),
            ]
        )
        # Confirm staged paths are gitignored (when in a git worktree).
        staged_compat = (
            python_dir
            / "src"
            / "hypabolic_trajectory"
            / "contracts"
            / "compatibility.json"
        )
        if not staged_compat.is_file():
            fail(f"prepare did not stage {staged_compat}")
        outdir.mkdir(parents=True, exist_ok=True)
        # Clean previous artifacts so find_artifacts is unique.
        for old in list(outdir.glob("*.tar.gz")) + list(outdir.glob("*.whl")):
            old.unlink()
        # Ensure build frontend is available for the chosen interpreter.
        run([python_exe, "-m", "pip", "install", "-q", "build"])
        run(
            [python_exe, "-m", "build", "--outdir", str(outdir)],
            cwd=python_dir,
        )

    sdist, wheel = find_artifacts(outdir)
    info(f"sdist={sdist.name}")
    info(f"wheel={wheel.name}")

    # 2/3. two-column member asserts
    sdist_members = list_sdist_members(sdist)
    wheel_members = list_wheel_members(wheel)
    assert_sdist_column(sdist_members, schemas)
    meta = read_wheel_metadata(wheel)
    assert_wheel_column(wheel_members, schemas, meta)
    assert_no_console_scripts_entry_points(wheel)

    # 4. Requires-Dist audit
    audit_requires_dist(meta)

    # 5. Isolated sdist install (interiors) + 4b. clean wheel install (deps)
    if not args.skip_isolated_install:
        isolated_sdist_install(sdist, schemas, python_exe)
        isolated_wheel_install(wheel, python_exe)

    print(
        json.dumps(
            {
                "status": "success",
                "sdist": sdist.name,
                "wheel": wheel.name,
                "schemas": schemas,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
