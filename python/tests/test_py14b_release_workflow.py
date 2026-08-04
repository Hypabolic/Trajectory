"""PY-14b: release.yml PyPI OIDC contract + publishing.md install/prereq rows.

Does not publish. Asserts the pinned release-workflow-pypi-artifact-contract:
validate packs to artifacts/release/pypi; publish-pypi is download-only with
skip-existing; github-release needs publish-pypi; docs document install + OIDC.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RELEASE_YML = ROOT / ".github" / "workflows" / "release.yml"
PUBLISHING_MD = ROOT / "docs" / "publishing.md"


def _job_block(workflow: str, job_id: str) -> str:
    """Return the YAML body of a top-level job (until the next job or EOF)."""
    pattern = re.compile(
        rf"(?m)^  {re.escape(job_id)}:\n(.*?)(?=^  [a-zA-Z0-9_-]+:\n|\Z)",
        re.DOTALL,
    )
    m = pattern.search(workflow)
    assert m is not None, f"job {job_id!r} not found in release.yml"
    return m.group(1)


def test_release_yml_has_publish_pypi_oidc_job() -> None:
    text = RELEASE_YML.read_text(encoding="utf-8")
    assert "publish-pypi:" in text
    job = _job_block(text, "publish-pypi")

    assert "environment: release" in job
    assert "id-token: write" in job
    assert "actions/download-artifact" in job
    assert "pypa/gh-action-pypi-publish@release/v1" in job
    assert "packages-dir: artifacts/release/pypi" in job
    assert "skip-existing: true" in job

    # Download-only: no rebuild / stamp / prepare in the publish job.
    assert "python -m build" not in job
    assert "prepare_python_package" not in job
    assert "python_package_smoke" not in job
    assert "stamp_release_version" not in job
    assert "actions/setup-python" not in job


def test_validate_packs_pypi_to_release_artifacts() -> None:
    text = RELEASE_YML.read_text(encoding="utf-8")
    job = _job_block(text, "validate")

    assert "actions/setup-python" in job
    assert "python_package_smoke.py" in job
    assert "artifacts/release/pypi" in job
    # Upload path includes the whole artifacts/release tree (pypi under it).
    assert "artifacts/release" in job


def test_github_release_needs_publish_pypi_and_uploads_pypi() -> None:
    text = RELEASE_YML.read_text(encoding="utf-8")
    job = _job_block(text, "github-release")

    assert re.search(r"needs: \[[^\]]*publish-pypi", job)
    assert "needs.publish-pypi.result == 'success'" in job
    assert "artifacts/release/pypi/*" in job
    assert "pip install hypabolic-trajectory==" in job
    assert "pypi.org/project/hypabolic-trajectory" in job


def test_publish_pypi_skipped_on_dry_run() -> None:
    text = RELEASE_YML.read_text(encoding="utf-8")
    job = _job_block(text, "publish-pypi")
    assert "dry_run" in job
    assert "needs.validate.result == 'success'" in job


def test_publishing_md_pypi_prereq_and_install() -> None:
    md = PUBLISHING_MD.read_text(encoding="utf-8")

    assert "hypabolic-trajectory" in md
    assert "PyPI" in md or "pypi" in md
    assert "pip install hypabolic-trajectory" in md
    assert "hypabolic-trajectory[otel]" in md or "'hypabolic-trajectory[otel]" in md

    # Trusted publisher / pending publisher fields
    assert "Hypabolic" in md
    assert "release.yml" in md
    assert "pending publisher" in md.lower() or "Trusted Publishing" in md
    assert "pypa/gh-action-pypi-publish" in md
    assert "skip-existing" in md
    assert "artifacts/release/pypi" in md
    # No rebuild contract documented
    assert "no rebuild" in md.lower() or "does **not** rebuild" in md
