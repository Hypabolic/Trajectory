#!/usr/bin/env python3
"""Generate progressive ``verify.py`` argv from ``python/runtime-capabilities.json``.

Normative maps (docs/python-implementation-spec.md §5):

* schema-id → ``--operation`` (claimed ⊆ verified honesty gate)
* capability → coverage rules (must be satisfiable under the filtered suite)
* fail closed on empty filters, unknown claims, or unmet coverage
* emit explicit ``--source`` / ``--operation`` whenever claimed is a proper
  subset of tip; progressive jobs always emit filters for claimed⊆verified

Owned by **PY-15a**. Claim-writers (PY-10a / PY-10b-* / PY-11) own capability
contents; this tool only reads them and builds argv.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

# ---------------------------------------------------------------------------
# §5 schema-id → verify operation map (normative)
# ---------------------------------------------------------------------------

SCHEMA_TO_OPERATION: dict[str, str] = {
    "letta-trajectory-v1": "normalize-letta",
    "letta-canonical-v1": "normalize-canonical",
    "hypabolic-trajectory-v1": "normalize-hypabolic",
    "openai-chat-messages": "project-openai",
    "jsonl-minimal": "project-minimal-jsonl",
    "otel-genai-spans-v1": "project-otel",
}

NORMALIZE_OPERATIONS: frozenset[str] = frozenset(
    {
        "normalize-letta",
        "normalize-canonical",
        "normalize-hypabolic",
    }
)

# Batch capabilities with progressive coverage rules.
BATCH_CAPABILITIES: frozenset[str] = frozenset(
    {
        "normalize",
        "normalize-partial",
        "list-explicit-root",
        "typed-diagnostics",
        "typed-fatal-errors",
        "deterministic-rerun",
    }
)

# Core stream capabilities (LS-12). Claimed only when the shared stream matrix
# is green; optional package stream caps are never tip-required.
CORE_STREAM_CAPABILITIES: frozenset[str] = frozenset(
    {
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
    }
)

KNOWN_CAPABILITIES: frozenset[str] = BATCH_CAPABILITIES | CORE_STREAM_CAPABILITIES

NORMALIZER_CONTRACT_VERSION = "0.2.0"
REQUIRED_SLICE = "ML13"
CAPS_REL = Path("python") / "runtime-capabilities.json"
COMPAT_REL = Path("contracts") / "compatibility.json"
CASES_REL = Path("conformance") / "cases"


class GeneratorError(Exception):
    """Fail-closed error with a user-visible message."""


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def tip_matrix(root: Path) -> tuple[list[str], list[str], list[str]]:
    """Return tip (sources, outputs, required capabilities) from compatibility."""
    compat = load_json(root / COMPAT_REL)
    sources = list(compat["implemented"]["sources"])
    outputs = list(compat["implemented"]["outputs"])
    capabilities = list(compat["capabilities"]["required"])
    return sources, outputs, capabilities


def load_capabilities(path: Path) -> dict[str, Any]:
    caps = load_json(path)
    if not isinstance(caps, dict):
        raise GeneratorError(f"{path}: root must be a JSON object")
    return caps


def _as_str_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise GeneratorError(f"runtime-capabilities.json {field} must be a list of strings")
    return list(value)


def validate_capabilities(
    caps: dict[str, Any],
    *,
    tip_sources: Sequence[str],
    tip_outputs: Sequence[str],
    tip_capabilities: Sequence[str],
) -> tuple[list[str], list[str], list[str]]:
    """Return ordered (sources, outputs, capabilities); raise on invalid claims."""
    runtime = caps.get("runtime")
    if runtime != "python":
        raise GeneratorError(
            f"runtime-capabilities.json runtime must be 'python' (got {runtime!r})"
        )
    slice_id = caps.get("slice")
    if slice_id != REQUIRED_SLICE:
        raise GeneratorError(
            f"runtime-capabilities.json slice must be {REQUIRED_SLICE!r} "
            f"(got {slice_id!r})"
        )
    ncv = caps.get("normalizer_contract_version")
    if ncv != NORMALIZER_CONTRACT_VERSION:
        raise GeneratorError(
            "runtime-capabilities.json normalizer_contract_version must be "
            f"{NORMALIZER_CONTRACT_VERSION!r} (got {ncv!r})"
        )

    sources = _as_str_list(caps.get("sources"), "sources")
    outputs = _as_str_list(caps.get("outputs"), "outputs")
    capabilities = _as_str_list(caps.get("capabilities"), "capabilities")

    tip_s, tip_o, tip_c = set(tip_sources), set(tip_outputs), set(tip_capabilities)
    extra_s = set(sources) - tip_s
    extra_o = set(outputs) - tip_o
    extra_c = set(capabilities) - tip_c
    if extra_s:
        raise GeneratorError(
            f"claimed sources not subset of tip: {sorted(extra_s)}"
        )
    if extra_o:
        raise GeneratorError(
            f"claimed outputs not subset of tip: {sorted(extra_o)}"
        )
    if extra_c:
        raise GeneratorError(
            f"claimed capabilities not subset of tip: {sorted(extra_c)}"
        )

    unknown_caps = set(capabilities) - KNOWN_CAPABILITIES
    if unknown_caps:
        raise GeneratorError(
            f"claimed capabilities lack coverage rules: {sorted(unknown_caps)}"
        )

    unknown_outputs = set(outputs) - set(SCHEMA_TO_OPERATION)
    if unknown_outputs:
        raise GeneratorError(
            f"claimed outputs lack schema→op map entries: {sorted(unknown_outputs)}"
        )

    if not sources:
        raise GeneratorError(
            "claimed sources is empty — fail closed (zero operations)"
        )

    # When claimed is not a proper subset of tip (set equality), require
    # order-sensitive list equality to tip peers (same honesty as CI jq /
    # validate_release_metadata). Progressive proper subsets may reorder.
    claimed_equal_tip = (
        set(sources) == tip_s and set(outputs) == tip_o and set(capabilities) == tip_c
    )
    if claimed_equal_tip and (
        sources != list(tip_sources)
        or outputs != list(tip_outputs)
        or capabilities != list(tip_capabilities)
    ):
        raise GeneratorError(
            "claimed sources/outputs/capabilities equal tip set-wise but "
            "order differs from contracts/compatibility.json tip peers"
        )

    return sources, outputs, capabilities


def operations_from_claims(
    outputs: Sequence[str], capabilities: Sequence[str]
) -> list[str]:
    """Map claimed outputs + list/stream capabilities to ordered unique operations."""
    ops: list[str] = []
    seen: set[str] = set()
    for schema_id in outputs:
        op = SCHEMA_TO_OPERATION[schema_id]
        if op not in seen:
            ops.append(op)
            seen.add(op)
    if "list-explicit-root" in capabilities:
        if "list-trajectories" not in seen:
            ops.append("list-trajectories")
            seen.add("list-trajectories")
    if CORE_STREAM_CAPABILITIES.intersection(capabilities):
        if "stream-sequence" not in seen:
            ops.append("stream-sequence")
            seen.add("stream-sequence")
    if not ops:
        raise GeneratorError(
            "no operations derived from claimed outputs/capabilities — fail closed"
        )
    return ops


def is_proper_subset_of_tip(
    sources: Sequence[str],
    outputs: Sequence[str],
    capabilities: Sequence[str],
    tip_sources: Sequence[str],
    tip_outputs: Sequence[str],
    tip_capabilities: Sequence[str],
) -> bool:
    """True when claimed set is a proper subset of tip on any of sources/outputs/caps."""
    s, o, c = set(sources), set(outputs), set(capabilities)
    ts, to, tc = set(tip_sources), set(tip_outputs), set(tip_capabilities)
    return s < ts or o < to or c < tc


def build_argv(sources: Sequence[str], operations: Sequence[str]) -> list[str]:
    """Build ``--source`` / ``--operation`` argv tokens (always explicit filters)."""
    argv: list[str] = []
    for source in sources:
        argv.extend(["--source", source])
    for operation in operations:
        argv.extend(["--operation", operation])
    return argv


def _case_is_partial(manifest: dict[str, Any]) -> bool:
    if "normalize-partial" in manifest.get("required_capabilities", []):
        return True
    if manifest.get("mode") == "partial":
        return True
    ctx = manifest.get("source_context") or {}
    if isinstance(ctx, dict):
        if ctx.get("partial") is True:
            return True
        offset = ctx.get("base_byte_offset")
        if isinstance(offset, int) and offset != 0:
            return True
    return False


def _is_stream_case(manifest: dict[str, Any]) -> bool:
    steps = manifest.get("steps")
    return isinstance(steps, list) and len(steps) >= 1


def filtered_case_ops(
    root: Path, sources: Sequence[str], operations: Sequence[str]
) -> list[tuple[dict[str, Any], list[str]]]:
    """Return (manifest, matching_ops) pairs under the progressive filter.

    Batch cases contribute names from their ``operation`` map. Stream cases
    (ordered ``steps``) contribute ``stream-sequence`` when that op is selected.
    """
    source_set = set(sources)
    op_set = set(operations)
    cases_root = root / CASES_REL
    if not cases_root.is_dir():
        raise GeneratorError(f"missing conformance cases directory: {cases_root}")

    pairs: list[tuple[dict[str, Any], list[str]]] = []
    for path in sorted(cases_root.glob("**/case.json")):
        manifest = load_json(path)
        if manifest.get("source") not in source_set:
            continue
        if _is_stream_case(manifest):
            matching = [name for name in ("stream-sequence", "stream-replay") if name in op_set]
            if matching:
                pairs.append((manifest, matching))
            continue
        op_table = manifest.get("operation") or {}
        if not isinstance(op_table, dict):
            continue
        matching = [name for name in op_table if name in op_set]
        if matching:
            pairs.append((manifest, matching))
    return pairs


def enforce_capability_coverage(
    root: Path,
    sources: Sequence[str],
    operations: Sequence[str],
    capabilities: Sequence[str],
) -> int:
    """Fail closed if coverage rules are unmet. Returns matched operation count."""
    pairs = filtered_case_ops(root, sources, operations)
    total_ops = sum(len(ops) for _, ops in pairs)
    if total_ops == 0:
        raise GeneratorError(
            "filtered suite would execute zero operations — fail closed "
            f"(sources={list(sources)}, operations={list(operations)})"
        )

    op_set = set(operations)
    claimed = set(capabilities)

    if "normalize" in claimed:
        if not (op_set & NORMALIZE_OPERATIONS):
            raise GeneratorError(
                "capability 'normalize' claimed but no normalize-* operation "
                "in progressive filter"
            )
        has_normalize_case = any(
            any(op in NORMALIZE_OPERATIONS for op in ops) for _, ops in pairs
        )
        if not has_normalize_case:
            raise GeneratorError(
                "capability 'normalize' claimed but no matching case under filter"
            )

    if "normalize-partial" in claimed:
        partial_hits = [
            (m, ops)
            for m, ops in pairs
            if _case_is_partial(m)
            and any(op in NORMALIZE_OPERATIONS for op in ops)
        ]
        if not partial_hits:
            raise GeneratorError(
                "capability 'normalize-partial' claimed but filtered suite has "
                "no partial / base_byte_offset case under a normalize-* op"
            )

    if "list-explicit-root" in claimed:
        if "list-trajectories" not in op_set:
            raise GeneratorError(
                "capability 'list-explicit-root' claimed but "
                "--operation list-trajectories is missing from progressive filter"
            )
        has_list = any(
            "list-trajectories" in ops for _, ops in pairs
        )
        if not has_list:
            raise GeneratorError(
                "capability 'list-explicit-root' claimed but no list-trajectories "
                "case under claimed sources"
            )

    if "typed-diagnostics" in claimed:
        diag_hits = [
            m
            for m, ops in pairs
            if "typed-diagnostics" in m.get("required_capabilities", [])
            and m.get("expected", {}).get("result") == "success"
            and ops
        ]
        if not diag_hits:
            raise GeneratorError(
                "capability 'typed-diagnostics' claimed but filtered suite has "
                "no diagnostics-bearing success case"
            )

    if "typed-fatal-errors" in claimed:
        fatal_hits = [
            m
            for m, ops in pairs
            if m.get("expected", {}).get("result") == "fatal-error"
            and ops
        ]
        if not fatal_hits:
            raise GeneratorError(
                "capability 'typed-fatal-errors' claimed but filtered suite has "
                "no fatal-error case under filter"
            )

    stream_claimed = CORE_STREAM_CAPABILITIES.intersection(claimed)
    if stream_claimed:
        if "stream-sequence" not in op_set:
            raise GeneratorError(
                "stream-* core capabilities claimed but --operation stream-sequence "
                "is missing from progressive filter"
            )
        stream_pairs = [(m, ops) for m, ops in pairs if _is_stream_case(m) and ops]
        if not stream_pairs:
            raise GeneratorError(
                "stream-* core capabilities claimed but filtered suite has no "
                "stream-sequence cases under claimed sources"
            )
        # Every claimed stream core cap must appear on at least one matching case.
        required_on_cases: set[str] = set()
        for m, _ops in stream_pairs:
            for cap in m.get("required_capabilities") or []:
                if isinstance(cap, str):
                    required_on_cases.add(cap)
        missing_fixture_caps = sorted(stream_claimed - required_on_cases)
        if missing_fixture_caps:
            raise GeneratorError(
                "claimed stream capabilities lack shared fixtures under filter: "
                f"{missing_fixture_caps}"
            )

    # deterministic-rerun: satisfied automatically by verify.py double-invoke.
    return total_ops


def generate(
    root: Path,
    *,
    capabilities_path: Path | None = None,
) -> dict[str, Any]:
    """Generate progressive filter plan from capabilities.

    Returns a dict with sources, operations, argv, subset flag, and op count.
    """
    root = root.resolve()
    caps_path = capabilities_path or (root / CAPS_REL)
    if not caps_path.is_file():
        raise GeneratorError(f"missing capabilities file: {caps_path}")

    tip_sources, tip_outputs, tip_capabilities = tip_matrix(root)
    caps = load_capabilities(caps_path)
    sources, outputs, capabilities = validate_capabilities(
        caps,
        tip_sources=tip_sources,
        tip_outputs=tip_outputs,
        tip_capabilities=tip_capabilities,
    )
    operations = operations_from_claims(outputs, capabilities)
    subset = is_proper_subset_of_tip(
        sources,
        outputs,
        capabilities,
        tip_sources,
        tip_outputs,
        tip_capabilities,
    )
    # Progressive honesty: always emit explicit filters (required when ⊂ tip;
    # also correct when equal — claimed⊆verified stays enforced).
    argv = build_argv(sources, operations)
    if not argv:
        raise GeneratorError("empty argv — fail closed")
    if subset and not (any(t == "--source" for t in argv) and any(t == "--operation" for t in argv)):
        raise GeneratorError(
            "claimed set is a proper subset of tip but argv lacks explicit "
            "--source/--operation filters"
        )

    matched = enforce_capability_coverage(root, sources, operations, capabilities)
    return {
        "sources": sources,
        "outputs": outputs,
        "capabilities": capabilities,
        "operations": operations,
        "argv": argv,
        "proper_subset_of_tip": subset,
        "matched_operations": matched,
        "capabilities_path": str(caps_path),
    }


def format_output(plan: dict[str, Any], fmt: str) -> str:
    argv: list[str] = plan["argv"]
    if fmt == "argv":
        # Tokens are schema/source/op identifiers — safe for shell word-split.
        return " ".join(argv)
    if fmt == "lines":
        return "\n".join(argv)
    if fmt == "json":
        return json.dumps(
            {
                "sources": plan["sources"],
                "outputs": plan["outputs"],
                "capabilities": plan["capabilities"],
                "operations": plan["operations"],
                "argv": argv,
                "proper_subset_of_tip": plan["proper_subset_of_tip"],
                "matched_operations": plan["matched_operations"],
            },
            indent=2,
            sort_keys=True,
        )
    raise GeneratorError(f"unknown format: {fmt!r}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate progressive conformance/verify.py --source/--operation "
            "argv from python/runtime-capabilities.json (PY-15a)."
        )
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path.cwd(),
        help="repository root (default: cwd)",
    )
    parser.add_argument(
        "--capabilities",
        type=Path,
        default=None,
        help="override path to runtime-capabilities.json",
    )
    parser.add_argument(
        "--format",
        choices=("argv", "lines", "json"),
        default="argv",
        help="output format (default: argv — single shell-splittable line)",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="validate coverage and exit 0 without printing argv",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        plan = generate(args.repository_root, capabilities_path=args.capabilities)
    except GeneratorError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1

    if args.check_only:
        print(
            json.dumps(
                {
                    "status": "ok",
                    "matched_operations": plan["matched_operations"],
                    "proper_subset_of_tip": plan["proper_subset_of_tip"],
                    "sources": plan["sources"],
                    "operations": plan["operations"],
                },
                separators=(",", ":"),
            )
        )
        return 0

    print(format_output(plan, args.format))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
