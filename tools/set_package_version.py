#!/usr/bin/env python3
"""Set the synchronized package version across NuGet, npm, crates, and PyPI metadata.

Single source of truth: repository-root VERSION file (also updated by this tool).

When ``python/pyproject.toml`` exists, rewrites exactly one static
``version = "…"`` under ``[project]`` (PY-14a monorepo stamp lockstep). Does
not touch ``WIRE_PACKAGE_VERSION`` or other source constants.

Does not rewrite conformance goldens. If the normalizer embeds the package
version in identity-bearing outputs, update goldens in the same release commit
before tagging (see docs/publishing.md).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)


def parse_version(raw: str) -> str:
    value = raw.strip()
    if value.startswith("v"):
        value = value[1:]
    if not SEMVER_RE.match(value):
        raise SystemExit(f"Invalid SemVer: {raw!r}")
    return value


def bump_version(current: str, kind: str) -> str:
    base = current.split("-", 1)[0].split("+", 1)[0]
    major, minor, patch = (int(part) for part in base.split("."))
    if kind == "major":
        return f"{major + 1}.0.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    if kind == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise SystemExit(f"Unknown bump kind: {kind}")


def read_version_file(root: Path) -> str:
    path = root / "VERSION"
    if not path.is_file():
        raise SystemExit("VERSION file is missing at repository root.")
    return parse_version(path.read_text(encoding="utf-8"))


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def replace_csproj_version(path: Path, version: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(
        r"(<Version>)[^<]+(</Version>)",
        rf"\g<1>{version}\g<2>",
        text,
        count=1,
    )
    if count != 1:
        raise SystemExit(f"Could not update <Version> in {path}")
    write_text(path, updated)


def replace_json_version(path: Path, version: str, *, also_deps: bool) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = version
    if also_deps:
        for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            block = data.get(key)
            if not isinstance(block, dict):
                continue
            for dep_name, dep_version in list(block.items()):
                if (
                    isinstance(dep_name, str)
                    and dep_name.startswith("@hypabolic/")
                    and isinstance(dep_version, str)
                    and re.match(r"^\d+\.\d+\.\d+", dep_version)
                ):
                    block[dep_name] = version
    write_text(path, json.dumps(data, indent=2) + "\n")


def replace_cargo_workspace_version(path: Path, version: str) -> None:
    text = path.read_text(encoding="utf-8")
    # [workspace.package] version = "x.y.z"
    text2, n1 = re.subn(
        r'(?m)^version = "[^"]+"$',
        f'version = "{version}"',
        text,
        count=1,
    )
    if n1 != 1:
        raise SystemExit(f"Could not update workspace package version in {path}")
    # path dependency pin: hypabolic-trajectory = { version = "=x.y.z", path = ... }
    text3, n2 = re.subn(
        r'(hypabolic-trajectory\s*=\s*\{\s*version\s*=\s*")=?[^"]+(")',
        rf"\g<1>={version}\g<2>",
        text2,
        count=1,
    )
    if n2 != 1:
        raise SystemExit(f"Could not update hypabolic-trajectory path dep version in {path}")
    write_text(path, text3)


def replace_pyproject_version(path: Path, version: str) -> None:
    """Rewrite exactly one static ``version = "…"`` under ``[project]``.

    Never touches WIRE_PACKAGE_VERSION or other source constants. Only the
    top-level ``[project]`` table is considered (not nested tables).
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    in_project = False
    project_version_line: int | None = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            # Enter only top-level [project]; leave on any other table header.
            in_project = stripped == "[project]"
            continue
        if not in_project:
            continue
        if re.match(r'^version\s*=\s*"[^"]*"\s*(#.*)?$', stripped):
            if project_version_line is not None:
                raise SystemExit(
                    f"Multiple static version lines under [project] in {path}"
                )
            project_version_line = i
    if project_version_line is None:
        raise SystemExit(
            f'Could not find static version = "…" under [project] in {path}'
        )
    original = lines[project_version_line]
    body = original.rstrip("\r\n")
    newline = original[len(body) :]
    # Preserve any trailing comment after the assignment.
    comment = ""
    if "#" in body:
        hash_idx = body.find("#")
        comment_text = body[hash_idx:].lstrip()  # includes leading '#'
        comment = f"  {comment_text}"
    lines[project_version_line] = f'version = "{version}"{comment}{newline}'
    write_text(path, "".join(lines))



def apply_version(root: Path, version: str) -> list[str]:
    changed: list[str] = []

    version_file = root / "VERSION"
    write_text(version_file, version + "\n")
    changed.append(str(version_file.relative_to(root)))

    csprojs = [
        root / "dotnet/src/Trajectory/Trajectory.csproj",
        root / "dotnet/src/Trajectory.OpenTelemetry/Trajectory.OpenTelemetry.csproj",
        root / "dotnet/src/Trajectory.Testing/Trajectory.Testing.csproj",
        # Optional stream packages (LS-09 / LS-10 / LS-07h) — publish with core.
        root / "dotnet/src/Trajectory.IO/Trajectory.IO.csproj",
        root / "dotnet/src/Trajectory.Ahp/Trajectory.Ahp.csproj",
        root / "dotnet/src/Trajectory.Hermes/Trajectory.Hermes.csproj",
    ]
    for path in csprojs:
        if not path.is_file():
            continue
        replace_csproj_version(path, version)
        changed.append(str(path.relative_to(root)))

    npm_packages = [
        root / "typescript/package.json",
        root / "typescript/packages/trajectory/package.json",
        root / "typescript/packages/trajectory-node/package.json",
        root / "typescript/packages/trajectory-otel/package.json",
        root / "typescript/packages/trajectory-ahp/package.json",
        root / "typescript/packages/trajectory-hermes/package.json",
        root / "typescript/packages/trajectory-testing/package.json",
        root / "typescript/packages/trajectory-cli/package.json",
    ]
    for path in npm_packages:
        if not path.is_file():
            continue
        replace_json_version(path, version, also_deps=True)
        changed.append(str(path.relative_to(root)))

    cargo = root / "rust/Cargo.toml"
    replace_cargo_workspace_version(cargo, version)
    changed.append(str(cargo.relative_to(root)))

    # Python: only when scaffold exists (PY-14a monorepo stamp lockstep).
    pyproject = root / "python" / "pyproject.toml"
    if pyproject.is_file():
        replace_pyproject_version(pyproject, version)
        changed.append(str(pyproject.relative_to(root)))

    return changed



def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--version", help="Explicit SemVer (e.g. 0.1.1 or v0.1.1)")
    group.add_argument(
        "--bump",
        choices=("patch", "minor", "major"),
        help="Bump relative to the VERSION file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the target version without writing files",
    )
    args = parser.parse_args()
    root = args.repository_root.resolve()

    if args.version:
        version = parse_version(args.version)
    else:
        current = read_version_file(root)
        version = bump_version(current, args.bump)

    if args.dry_run:
        print(json.dumps({"status": "dry-run", "version": version}))
        return

    changed = apply_version(root, version)
    print(
        json.dumps(
            {
                "status": "success",
                "version": version,
                "tag": f"v{version}",
                "files": changed,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
