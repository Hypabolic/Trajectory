#!/usr/bin/env python3
"""Prove a packed or registry artifact contains stream capability manifests/APIs.

Already-published / skip-duplicate / skip-existing fallbacks must call this
before treating a version as a successful stream ship. A retag of an
already-used version (for example ``0.1.2``) fails unless the registry
artifact actually contains this branch's stream manifests and APIs.

Stdlib only. No network when inspecting ``--artifact`` / ``--artifact-dir``.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

USER_AGENT = "TrajectoryReleaseVerifier/1.0 (+https://github.com/Hypabolic/Trajectory)"
RETRY_ATTEMPTS = 8
RETRY_SLEEP_SECONDS = 15
NUGET_RETRY_ATTEMPTS = 18
NUGET_RETRY_SLEEP_SECONDS = 20

CORE_STREAM_CAPS = (
    "stream-core",
    "stream-cursor-v1",
    "stream-jsonl-framing",
    "stream-apply-snapshot",
    "stream-apply-append",
    "stream-full-snapshot",
    "stream-record-delta",
    "stream-reset",
    "stream-provisional-records",
    "stream-deterministic-replay",
    "stream-file-jsonl",
    "stream-ahp-snapshot",
    "stream-ahp-action-log",
)

# Optional stream packages claim these on package-capabilities.json only.
OPTIONAL_CAPS = {
    "Hypabolic.Trajectory.IO": frozenset({"stream-file-io", "stream-async-iterator"}),
    "@hypabolic/trajectory-node": frozenset({"stream-file-io", "stream-async-iterator"}),
    "hypabolic-trajectory-io": frozenset({"stream-file-io"}),
    "Hypabolic.Trajectory.Ahp": frozenset({"stream-ahp-client"}),
    "@hypabolic/trajectory-ahp": frozenset({"stream-ahp-client"}),
    "hypabolic-trajectory-ahp": frozenset({"stream-ahp-client"}),
    "Hypabolic.Trajectory.Hermes": frozenset({"stream-hermes-provider"}),
    "@hypabolic/trajectory-hermes": frozenset({"stream-hermes-provider"}),
    "hypabolic-trajectory-hermes": frozenset({"stream-hermes-provider"}),
}

CORE_PACKAGES = frozenset(
    {
        "Hypabolic.Trajectory",
        "@hypabolic/trajectory",
        "hypabolic-trajectory",
    }
)

# Python wheel is the core package plus optional stream extras in the same
# distribution (package-capabilities.json interiors).
PYTHON_DIST = "hypabolic-trajectory"

NON_STREAM_PACKAGES = frozenset(
    {
        "Hypabolic.Trajectory.OpenTelemetry",
        "Hypabolic.Trajectory.Testing",
        "@hypabolic/trajectory-otel",
        "hypabolic-trajectory-opentelemetry",
    }
)

CORE_API_MARKERS = (
    "TrajectoryStream",
    "apply_ahp_actions",
    "ApplyAhpActions",
    "apply_hermes_export",
    "ApplyHermesExport",
    "stream-core",
)

CORE_API_PATH_HINTS = (
    "streaming.rs",
    "streaming.js",
    "streaming.ts",
    "streaming/apply.py",
    "TrajectoryStream.cs",
    "AhpReducer.cs",
    "ahp-reducer.js",
    "ahp_reducer.py",
    "ahp_reducer.rs",
)

OPTIONAL_API_HINTS = {
    "stream-file-io": (
        "file-stream.js",
        "file-stream.ts",
        "file_stream.py",
        "FileTrajectoryStream.cs",
        "FileTrajectoryStream",
    ),
    "stream-ahp-client": (
        "client.js",
        "client.ts",
        "client.py",
        "AhpClient.cs",
        "AhpProtocol.cs",
        "AhpClient",
    ),
    "stream-hermes-provider": (
        "provider.js",
        "provider.ts",
        "provider.py",
        "HermesProvider.cs",
        "HermesProvider",
    ),
}


@dataclass(frozen=True)
class ArchiveFile:
    name: str
    data: bytes


class VerifyError(Exception):
    """One artifact failed the stream content check."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def contains_marker(data: bytes, marker: str) -> bool:
    utf8 = marker.encode("utf-8")
    if utf8 in data:
        return True
    return marker.encode("utf-16-le") in data


def _http_get(url: str, timeout: int = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _http_get_json(url: str) -> dict:
    payload = _http_get(url)
    return json.loads(payload.decode("utf-8"))


def read_archive(path: Path | None = None, data: bytes | None = None, name: str = "") -> list[ArchiveFile]:
    """Read a nupkg/wheel/zip or tgz/crate/sdist into memory."""
    if path is not None:
        name = name or path.name
        if data is None:
            data = path.read_bytes()
    elif data is None:
        raise VerifyError("read_archive requires path or data")
    lower = name.lower()
    bio = io.BytesIO(data)
    files: list[ArchiveFile] = []
    if lower.endswith((".nupkg", ".snupkg", ".zip", ".whl")):
        with zipfile.ZipFile(bio) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                files.append(ArchiveFile(info.filename, archive.read(info)))
        return files
    if lower.endswith((".tgz", ".tar.gz", ".crate")):
        with tarfile.open(fileobj=bio, mode="r:gz") as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                extracted = archive.extractfile(member)
                if extracted is None:
                    continue
                files.append(ArchiveFile(member.name, extracted.read()))
        return files
    raise VerifyError(f"unsupported artifact type: {name}")


def _basename(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _norm_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def find_json_files(files: Iterable[ArchiveFile], filename: str) -> list[tuple[str, dict]]:
    found: list[tuple[str, dict]] = []
    for item in files:
        if _basename(item.name) != filename:
            continue
        try:
            found.append((item.name, json.loads(item.data.decode("utf-8"))))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VerifyError(f"{item.name} is not valid JSON: {exc}") from exc
    return found


def identify_package(files: list[ArchiveFile], fallback_name: str = "") -> str:
    """Best-effort package identity from archive metadata."""
    for item in files:
        base = _basename(item.name).lower()
        if base.endswith(".nuspec"):
            text = item.data.decode("utf-8", errors="replace")
            start = text.find("<id>")
            end = text.find("</id>")
            if start != -1 and end != -1 and end > start:
                return text[start + 4 : end].strip()
        if base == "package.json":
            try:
                name = json.loads(item.data.decode("utf-8")).get("name")
            except (UnicodeDecodeError, json.JSONDecodeError):
                name = None
            if isinstance(name, str) and name:
                return name
        if base == "cargo.toml":
            for line in item.data.decode("utf-8", errors="replace").splitlines():
                stripped = line.strip()
                if stripped.startswith("name") and "=" in stripped:
                    value = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                    if value:
                        return value
                    break
        if base in {"metadata", "pkg-info"}:
            for line in item.data.decode("utf-8", errors="replace").splitlines():
                if line.lower().startswith("name:"):
                    return line.split(":", 1)[1].strip()
    return fallback_name


def _required_optional_caps(package: str) -> frozenset[str] | None:
    if package in OPTIONAL_CAPS:
        return OPTIONAL_CAPS[package]
    return None


def _is_core(package: str) -> bool:
    return package in CORE_PACKAGES


def _is_python_wheel_or_sdist(package: str, files: list[ArchiveFile]) -> bool:
    if package != PYTHON_DIST:
        return False
    return any(
        "hypabolic_trajectory/runtime-capabilities.json" in _norm_path(item.name)
        or _norm_path(item.name).endswith("src/hypabolic_trajectory/runtime-capabilities.json")
        for item in files
    )


def verify_stream_contents(files: list[ArchiveFile], package: str) -> list[str]:
    """Return a list of human-readable failures (empty = pass)."""
    if package in NON_STREAM_PACKAGES:
        return []

    failures: list[str] = []
    names = [_norm_path(item.name) for item in files]
    joined_names = "\n".join(names)

    optional_caps = _required_optional_caps(package)
    is_core = _is_core(package) or _is_python_wheel_or_sdist(package, files)

    if not is_core and optional_caps is None and package != PYTHON_DIST:
        failures.append(
            f"{package}: unknown package; refusing to treat already-published as success"
        )
        return failures

    if is_core:
        manifests = find_json_files(files, "runtime-capabilities.json")
        if not manifests:
            failures.append(f"{package}: missing runtime-capabilities.json")
        else:
            for path, doc in manifests:
                claimed = doc.get("capabilities")
                if not isinstance(claimed, list):
                    failures.append(f"{path}: capabilities must be a list")
                    continue
                missing = [cap for cap in CORE_STREAM_CAPS if cap not in claimed]
                if missing:
                    failures.append(
                        f"{path}: missing core stream capabilities {missing}"
                    )
        marker_hits = 0
        for item in files:
            if any(contains_marker(item.data, marker) for marker in CORE_API_MARKERS):
                marker_hits += 1
            if any(hint in _norm_path(item.name) for hint in CORE_API_PATH_HINTS):
                marker_hits += 1
        if marker_hits == 0:
            failures.append(
                f"{package}: no stream API markers "
                f"({', '.join(CORE_API_MARKERS[:3])}) or source paths"
            )

    if optional_caps is not None:
        manifests = find_json_files(files, "package-capabilities.json")
        if not manifests:
            failures.append(f"{package}: missing package-capabilities.json")
        else:
            for path, doc in manifests:
                claimed = doc.get("capabilities")
                if not isinstance(claimed, list):
                    failures.append(f"{path}: capabilities must be a list")
                    continue
                claimed_set = frozenset(claimed)
                if claimed_set != optional_caps:
                    failures.append(
                        f"{path}: capabilities must equal {sorted(optional_caps)} "
                        f"(got {sorted(claimed_set)})"
                    )
        hints = []
        for cap in optional_caps:
            hints.extend(OPTIONAL_API_HINTS.get(cap, ()))
        path_hit = any(hint in joined_names for hint in hints)
        marker_hit = any(
            contains_marker(item.data, hint)
            for item in files
            for hint in hints
        )
        if hints and not path_hit and not marker_hit:
            # Compiled .NET packages may only ship the DLL + manifest.
            dll_present = any(name.endswith(".dll") for name in names)
            if not dll_present:
                failures.append(
                    f"{package}: missing optional stream API path "
                    f"(looked for {sorted(set(hints))})"
                )

    if package == PYTHON_DIST and _is_python_wheel_or_sdist(package, files):
        # Same wheel carries optional extras; require their manifests too.
        for rel, expected in (
            ("hypabolic_trajectory/io/package-capabilities.json", frozenset({"stream-file-io"})),
            (
                "hypabolic_trajectory/ahp_client/package-capabilities.json",
                frozenset({"stream-ahp-client"}),
            ),
            (
                "hypabolic_trajectory/hermes_provider/package-capabilities.json",
                frozenset({"stream-hermes-provider"}),
            ),
        ):
            matches = [
                item
                for item in files
                if _norm_path(item.name).endswith(rel)
                or _norm_path(item.name).endswith("src/" + rel)
            ]
            if not matches:
                failures.append(f"{package}: missing {rel}")
                continue
            doc = json.loads(matches[0].data.decode("utf-8"))
            claimed = frozenset(doc.get("capabilities") or [])
            if claimed != expected:
                failures.append(
                    f"{rel}: capabilities must equal {sorted(expected)} "
                    f"(got {sorted(claimed)})"
                )
        for rel in (
            "hypabolic_trajectory/streaming/apply.py",
            "hypabolic_trajectory/io/file_stream.py",
            "hypabolic_trajectory/ahp_client/client.py",
            "hypabolic_trajectory/hermes_provider/provider.py",
        ):
            if not any(
                _norm_path(item.name).endswith(rel)
                or _norm_path(item.name).endswith("src/" + rel)
                for item in files
            ):
                failures.append(f"{package}: missing stream API {rel}")

    return failures


def format_failure(package: str, version: str, details: Iterable[str]) -> str:
    lines = [
        f"Registry/local artifact {package}@{version} does not contain required "
        "stream capability manifests and APIs.",
        "skip-duplicate / already-published / skip-existing is not success for a "
        "stream cut. Do not retag 0.1.2; cut a new synchronized tag after 0.1.2.",
        *details,
    ]
    return "\n".join(lines)


def download_nuget(package: str, version: str) -> bytes:
    ident = package.lower()
    ver = version.lower()
    urls = (
        f"https://api.nuget.org/v3-flatcontainer/{ident}/{ver}/{ident}.{ver}.nupkg",
        f"https://www.nuget.org/api/v2/package/{package}/{version}",
    )
    last_error: Exception | None = None
    for url in urls:
        try:
            return _http_get(url)
        except (VerifyError, urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
    raise VerifyError(f"{package}@{version} nuget download failed: {last_error}")


def download_npm(package: str, version: str) -> bytes:
    encoded = package.replace("/", "%2f")
    meta = _http_get_json(f"https://registry.npmjs.org/{encoded}")
    versions = meta.get("versions") or {}
    if version not in versions:
        raise VerifyError(f"{package}@{version} is not on npm")
    tarball = versions[version].get("dist", {}).get("tarball")
    if not tarball:
        raise VerifyError(f"{package}@{version} npm metadata has no tarball")
    return _http_get(tarball)


def download_crates(package: str, version: str) -> bytes:
    url = f"https://static.crates.io/crates/{package}/{package}-{version}.crate"
    return _http_get(url)


def download_pypi(package: str, version: str, *, prefer: str = "wheel") -> bytes:
    meta = _http_get_json(f"https://pypi.org/pypi/{package}/{version}/json")
    files = meta.get("urls") or []
    chosen = None
    for item in files:
        packagetype = item.get("packagetype")
        if prefer == "wheel" and packagetype == "bdist_wheel":
            chosen = item
            break
        if prefer == "sdist" and packagetype == "sdist":
            chosen = item
            break
    if chosen is None and files:
        chosen = files[0]
    if not chosen or not chosen.get("url"):
        raise VerifyError(f"{package}@{version} is not on PyPI")
    return _http_get(chosen["url"])


def download_with_retries(registry: str, package: str, version: str) -> bytes:
    last_error: Exception | None = None
    attempts = NUGET_RETRY_ATTEMPTS if registry == "nuget" else RETRY_ATTEMPTS
    sleep_s = NUGET_RETRY_SLEEP_SECONDS if registry == "nuget" else RETRY_SLEEP_SECONDS
    for attempt in range(1, attempts + 1):
        try:
            if registry == "nuget":
                return download_nuget(package, version)
            if registry == "npm":
                return download_npm(package, version)
            if registry == "crates":
                return download_crates(package, version)
            if registry == "pypi":
                return download_pypi(package, version)
            raise VerifyError(f"unknown registry {registry!r}")
        except (VerifyError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            print(
                f"{package}@{version} {registry} download attempt {attempt}/"
                f"{attempts} failed: {exc}",
                file=sys.stderr,
            )
            if attempt < attempts:
                time.sleep(sleep_s)
    raise VerifyError(
        f"could not download {package}@{version} from {registry}: {last_error}"
    )


def infer_registry(path: Path) -> str | None:
    name = path.name.lower()
    if name.endswith(".nupkg"):
        return "nuget"
    if name.endswith(".tgz"):
        return "npm"
    if name.endswith(".crate"):
        return "crates"
    if name.endswith(".whl") or name.endswith(".tar.gz"):
        return "pypi"
    return None


def iter_artifacts(directory: Path) -> list[Path]:
    found: list[Path] = []
    for pattern in ("**/*.nupkg", "**/*.tgz", "**/*.crate", "**/*.whl", "**/*.tar.gz"):
        found.extend(path for path in directory.glob(pattern) if path.is_file())
    # Skip symbol packages and nested extract noise.
    return [
        path
        for path in sorted(found)
        if not path.name.endswith(".snupkg") and ".symbols." not in path.name
    ]


def verify_bytes(
    data: bytes,
    *,
    package: str,
    version: str,
    filename: str,
    local_digest: str | None = None,
) -> None:
    if local_digest is not None:
        remote_digest = sha256_bytes(data)
        if remote_digest == local_digest:
            print(
                f"PASS {package}@{version}: registry digest matches local "
                f"({remote_digest[:12]}…)"
            )
            # Still require the bytes to contain stream content so a matching
            # pre-stream pack cannot slip through.
        else:
            print(
                f"NOTE {package}@{version}: registry digest {remote_digest[:12]}… "
                f"!= local {local_digest[:12]}…; requiring content check",
                file=sys.stderr,
            )
    files = read_archive(data=data, name=filename)
    identified = identify_package(files, fallback_name=package)
    failures = verify_stream_contents(files, identified or package)
    if failures:
        raise VerifyError(format_failure(identified or package, version, failures))
    print(f"PASS {identified or package}@{version}: stream manifests and APIs present")


def verify_local_path(path: Path, version: str, package: str | None = None) -> str:
    data = path.read_bytes()
    files = read_archive(path=path, data=data)
    identified = identify_package(files, fallback_name=package or "")
    if not identified:
        raise VerifyError(f"{path}: could not identify package")
    if identified in NON_STREAM_PACKAGES:
        print(f"SKIP {identified}@{version}: non-stream package")
        return identified
    failures = verify_stream_contents(files, identified)
    if failures:
        raise VerifyError(format_failure(identified, version, failures))
    print(f"PASS {identified}@{version} ({path.name}): stream manifests and APIs present")
    return identified


def verify_registry_package(
    registry: str,
    package: str,
    version: str,
    local_path: Path | None = None,
) -> None:
    if package in NON_STREAM_PACKAGES:
        print(f"SKIP {package}@{version}: non-stream package")
        return
    local_digest = None
    if local_path is not None:
        local_digest = sha256_bytes(local_path.read_bytes())
        # Local packed bytes must themselves be a stream ship.
        verify_local_path(local_path, version, package=package)
    suffix = {
        "nuget": ".nupkg",
        "npm": ".tgz",
        "crates": ".crate",
        "pypi": ".whl",
    }[registry]
    data = download_with_retries(registry, package, version)
    verify_bytes(
        data,
        package=package,
        version=version,
        filename=f"{package}{suffix}",
        local_digest=local_digest,
    )


REQUIRED_BY_REGISTRY: dict[str, frozenset[str]] = {
    "nuget": frozenset(
        {
            "Hypabolic.Trajectory",
            "Hypabolic.Trajectory.IO",
            "Hypabolic.Trajectory.Ahp",
            "Hypabolic.Trajectory.Hermes",
        }
    ),
    "npm": frozenset(
        {
            "@hypabolic/trajectory",
            "@hypabolic/trajectory-node",
            "@hypabolic/trajectory-ahp",
            "@hypabolic/trajectory-hermes",
        }
    ),
    "pypi": frozenset({"hypabolic-trajectory"}),
    "crates": frozenset({"hypabolic-trajectory"}),
}


def verify_artifact_dir(
    directory: Path,
    version: str,
    registry: str | None = None,
) -> None:
    artifacts = iter_artifacts(directory)
    if not artifacts:
        raise VerifyError(f"no packages under {directory}")

    seen: dict[str, set[str]] = {name: set() for name in REQUIRED_BY_REGISTRY}
    for path in artifacts:
        inferred = infer_registry(path)
        if inferred is None:
            continue
        if registry is not None and inferred != registry:
            continue
        if registry is None:
            identified = verify_local_path(path, version)
            if identified not in NON_STREAM_PACKAGES:
                seen[inferred].add(identified)
            continue
        data = path.read_bytes()
        files = read_archive(path=path, data=data)
        identified = identify_package(files, fallback_name="")
        if not identified:
            raise VerifyError(f"{path}: could not identify package")
        if identified in NON_STREAM_PACKAGES:
            print(f"SKIP {identified}@{version}: non-stream package")
            continue
        verify_registry_package(inferred, identified, version, local_path=path)
        seen[inferred].add(identified)

    present_registries = {infer_registry(path) for path in artifacts}
    check_registries = [registry] if registry is not None else sorted(REQUIRED_BY_REGISTRY)
    missing: list[str] = []
    for name in check_registries:
        if name is None:
            continue
        if registry is None and name not in present_registries:
            continue
        required = REQUIRED_BY_REGISTRY[name]
        # Crate dependents are packed only after core exists on crates.io.
        if name == "crates":
            required = frozenset({"hypabolic-trajectory"})
        absent = sorted(required - seen[name])
        missing.extend(f"{name}:{pkg}" for pkg in absent)
    if missing:
        raise VerifyError(
            "stream ship is incomplete; missing verified artifacts for: "
            + ", ".join(missing)
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fail already-published fallbacks unless the artifact contains "
            "stream capability manifests and APIs."
        )
    )
    parser.add_argument("--version", required=True, help="Package version (no v prefix)")
    parser.add_argument(
        "--registry",
        choices=("nuget", "npm", "crates", "pypi"),
        help="Download and inspect the published artifact from this registry",
    )
    parser.add_argument("--package", help="Registry package name")
    parser.add_argument("--artifact", type=Path, help="Local packed artifact to inspect")
    parser.add_argument(
        "--local-artifact",
        type=Path,
        help="Local packed artifact to digest-compare with the registry copy",
    )
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        help="Directory of packed artifacts (validate or publish jobs)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    version = args.version.lstrip("v")
    try:
        if args.artifact and args.artifact_dir:
            raise VerifyError("use only one of --artifact or --artifact-dir")
        if args.artifact:
            verify_local_path(args.artifact, version, package=args.package)
            return 0
        if args.artifact_dir:
            verify_artifact_dir(args.artifact_dir, version, registry=args.registry)
            return 0
        if args.registry and args.package:
            verify_registry_package(
                args.registry,
                args.package,
                version,
                local_path=args.local_artifact,
            )
            return 0
        raise VerifyError(
            "provide --artifact, --artifact-dir, or --registry plus --package"
        )
    except VerifyError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
