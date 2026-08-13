"""Local content/digest checks for published stream artifacts (M8)."""

from __future__ import annotations

import io
import json
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

from verify_published_stream_artifact import (  # noqa: E402
    CORE_STREAM_CAPS,
    VerifyError,
    identify_package,
    main,
    read_archive,
    sha256_bytes,
    verify_local_path,
    verify_stream_contents,
)


CORE_CAPS = {
    "runtime": "test",
    "capabilities": list(CORE_STREAM_CAPS),
}


def _core_caps_json() -> bytes:
    return json.dumps(CORE_CAPS).encode("utf-8")


def _write_zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


def _write_tgz(path: Path, members: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return path


def test_core_nupkg_with_stream_manifest_and_api_passes(tmp_path: Path) -> None:
    nupkg = _write_zip(
        tmp_path / "Hypabolic.Trajectory.0.1.3.nupkg",
        {
            "Hypabolic.Trajectory.nuspec": b"<package><metadata><id>Hypabolic.Trajectory</id></metadata></package>",
            "contentFiles/any/any/runtime-capabilities.json": _core_caps_json(),
            "lib/net10.0/Hypabolic.Trajectory.dll": b"xxTrajectoryStream\x00ApplyHermesExport",
        },
    )
    assert verify_local_path(nupkg, "0.1.3") == "Hypabolic.Trajectory"


def test_pre_stream_nupkg_fails_with_retag_guidance(tmp_path: Path) -> None:
    nupkg = _write_zip(
        tmp_path / "Hypabolic.Trajectory.0.1.2.nupkg",
        {
            "Hypabolic.Trajectory.nuspec": b"<package><metadata><id>Hypabolic.Trajectory</id></metadata></package>",
            "contentFiles/any/any/runtime-capabilities.json": json.dumps(
                {"runtime": "dotnet", "capabilities": ["normalize"]}
            ).encode("utf-8"),
            "lib/net10.0/Hypabolic.Trajectory.dll": b"normalize only",
        },
    )
    with pytest.raises(VerifyError, match="Do not retag 0.1.2") as exc:
        verify_local_path(nupkg, "0.1.2")
    assert "stream-core" in str(exc.value)


def test_optional_npm_tarball_requires_package_capabilities(tmp_path: Path) -> None:
    tgz = _write_tgz(
        tmp_path / "hypabolic-trajectory-ahp-0.1.3.tgz",
        {
            "package/package.json": json.dumps(
                {"name": "@hypabolic/trajectory-ahp", "version": "0.1.3"}
            ).encode("utf-8"),
            "package/package-capabilities.json": json.dumps(
                {"capabilities": ["stream-ahp-client"]}
            ).encode("utf-8"),
            "package/dist/client.js": b"export class AhpClient {}",
        },
    )
    assert verify_local_path(tgz, "0.1.3") == "@hypabolic/trajectory-ahp"


def test_optional_npm_tarball_without_manifest_fails(tmp_path: Path) -> None:
    tgz = _write_tgz(
        tmp_path / "hypabolic-trajectory-ahp-0.1.2.tgz",
        {
            "package/package.json": json.dumps(
                {"name": "@hypabolic/trajectory-ahp", "version": "0.1.2"}
            ).encode("utf-8"),
            "package/dist/index.js": b"export {}",
        },
    )
    with pytest.raises(VerifyError, match="package-capabilities.json"):
        verify_local_path(tgz, "0.1.2")


def test_python_wheel_requires_core_and_optional_stream_files(tmp_path: Path) -> None:
    wheel = _write_zip(
        tmp_path / "hypabolic_trajectory-0.1.3-py3-none-any.whl",
        {
            "hypabolic_trajectory-0.1.3.dist-info/METADATA": b"Name: hypabolic-trajectory\nVersion: 0.1.3\n",
            "hypabolic_trajectory/runtime-capabilities.json": _core_caps_json(),
            "hypabolic_trajectory/streaming/apply.py": b"class TrajectoryStream:\n    pass\n",
            "hypabolic_trajectory/io/package-capabilities.json": json.dumps(
                {"capabilities": ["stream-file-io"]}
            ).encode("utf-8"),
            "hypabolic_trajectory/io/file_stream.py": b"class FileTrajectoryStream:\n    pass\n",
            "hypabolic_trajectory/ahp_client/package-capabilities.json": json.dumps(
                {"capabilities": ["stream-ahp-client"]}
            ).encode("utf-8"),
            "hypabolic_trajectory/ahp_client/client.py": b"class AhpClient:\n    pass\n",
            "hypabolic_trajectory/hermes_provider/package-capabilities.json": json.dumps(
                {"capabilities": ["stream-hermes-provider"]}
            ).encode("utf-8"),
            "hypabolic_trajectory/hermes_provider/provider.py": b"class HermesProvider:\n    pass\n",
        },
    )
    assert verify_local_path(wheel, "0.1.3") == "hypabolic-trajectory"


def test_crate_core_requires_streaming_rs(tmp_path: Path) -> None:
    crate = _write_tgz(
        tmp_path / "hypabolic-trajectory-0.1.3.crate",
        {
            "hypabolic-trajectory-0.1.3/Cargo.toml": b'[package]\nname = "hypabolic-trajectory"\n',
            "hypabolic-trajectory-0.1.3/runtime-capabilities.json": _core_caps_json(),
            "hypabolic-trajectory-0.1.3/src/streaming.rs": b"pub struct TrajectoryStream;\n",
        },
    )
    assert verify_local_path(crate, "0.1.3") == "hypabolic-trajectory"


def test_non_stream_package_is_skipped(tmp_path: Path) -> None:
    nupkg = _write_zip(
        tmp_path / "Hypabolic.Trajectory.OpenTelemetry.0.1.3.nupkg",
        {
            "Hypabolic.Trajectory.OpenTelemetry.nuspec": (
                b"<package><metadata><id>Hypabolic.Trajectory.OpenTelemetry</id></metadata></package>"
            ),
            "lib/net10.0/Hypabolic.Trajectory.OpenTelemetry.dll": b"otel",
        },
    )
    assert verify_local_path(nupkg, "0.1.3") == "Hypabolic.Trajectory.OpenTelemetry"


def test_artifact_dir_cli_requires_stream_set(tmp_path: Path) -> None:
    nuget = tmp_path / "nuget"
    nuget.mkdir()
    _write_zip(
        nuget / "Hypabolic.Trajectory.0.1.3.nupkg",
        {
            "Hypabolic.Trajectory.nuspec": b"<package><metadata><id>Hypabolic.Trajectory</id></metadata></package>",
            "contentFiles/any/any/runtime-capabilities.json": _core_caps_json(),
            "lib/net10.0/Hypabolic.Trajectory.dll": b"TrajectoryStream",
        },
    )
    # Incomplete stream set — IO/Ahp/Hermes missing.
    assert (
        main(["--version", "0.1.3", "--artifact-dir", str(nuget)])
        == 1
    )


def test_identify_package_from_nuspec_and_package_json(tmp_path: Path) -> None:
    nupkg = _write_zip(
        tmp_path / "pkg.nupkg",
        {"Foo.nuspec": b"<id>Hypabolic.Trajectory.IO</id>"},
    )
    files = read_archive(nupkg)
    assert identify_package(files) == "Hypabolic.Trajectory.IO"

    tgz = _write_tgz(
        tmp_path / "pkg.tgz",
        {"package/package.json": b'{"name":"@hypabolic/trajectory-node"}'},
    )
    assert identify_package(read_archive(tgz)) == "@hypabolic/trajectory-node"


def test_digest_helper_is_stable() -> None:
    assert sha256_bytes(b"abc") == sha256_bytes(b"abc")
    assert sha256_bytes(b"abc") != sha256_bytes(b"abd")


def test_verify_stream_contents_unknown_package_fails_closed() -> None:
    failures = verify_stream_contents([], "some-random-pkg")
    assert failures
    assert "unknown package" in failures[0]


def test_ls07h_plan_does_not_defer_shared_hermes_provider_corpus() -> None:
    plan = (ROOT / "docs" / "live-session-streaming-plan.md").read_text(
        encoding="utf-8"
    )
    assert "hermes-provider-* stream-sequence corpus still deferred" not in plan
    assert "shared `hermes-provider-*` cases cover core `apply_hermes_export`" in plan
    assert "only optional" in plan and "SQLite/query I/O remains package-test-gated" in plan
