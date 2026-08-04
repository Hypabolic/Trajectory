"""Protocol v1 conformance runner (PY-10a early surface).

Implements the normative preamble, response templates, case→NormalizeRequest
mapping, and free-function normalize/project ops. Listing is deferred to
PY-10b-list.

Authority:
  - docs/python-implementation-spec.md §7
  - conformance/protocol/request-v1.schema.json
  - conformance/protocol/response-v1.schema.json
  - peer runners (dotnet Program.cs, typescript cli.ts, rust main.rs)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Final, Mapping

from hypabolic_trajectory import (
    Bounds,
    Filters,
    NormalizeOptions,
    NormalizeRequest,
    SourceContext,
    ToolArgumentBounds,
    ToolResultBounds,
    TrajectoryError,
    normalize_to_ir,
    project_canonical,
    project_hypabolic,
    project_letta,
    project_minimal_jsonl,
    project_openai,
    project_otel_genai,
    serialize_projection,
)
from hypabolic_trajectory.diagnostics import Diagnostic

PROTOCOL_VERSION: Final[str] = "1"

# Ops implemented in this early runner (PY-10a + free-function project surface).
# list-trajectories is intentionally out of scope until PY-10b-list.
_NORMALIZE_OPS: Final[frozenset[str]] = frozenset(
    {
        "normalize-letta",
        "normalize-canonical",
        "normalize-hypabolic",
        "project-openai",
        "project-minimal-jsonl",
        "project-otel",
    }
)

_KNOWN_OPS: Final[frozenset[str]] = _NORMALIZE_OPS | frozenset({"list-trajectories"})


class ProtocolError(Exception):
    """Protocol / I/O / request-shape failure → protocol-error response, exit 2."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# ---------------------------------------------------------------------------
# Request I/O
# ---------------------------------------------------------------------------


def read_request_text(argv: list[str]) -> str:
    """Read request JSON text from a single path argument or stdin."""
    if len(argv) == 0:
        return sys.stdin.read()
    if len(argv) == 1:
        path = Path(argv[0])
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ProtocolError(f"Failed to read request file: {exc.strerror}.") from None
    raise ProtocolError("Pass one request file or write one request object to stdin.")


def parse_request(text: str) -> dict[str, str]:
    """Parse and validate protocol-v1 request object (preamble steps 2–4)."""
    try:
        parsed: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"Request is not valid JSON: {exc.msg}.") from None

    # Only a JSON object is valid (reject arrays, null, scalars).
    if type(parsed) is not dict:
        raise ProtocolError("The request must be a JSON object.")

    required = ("protocol_version", "case", "operation", "repository_root")
    for key in required:
        value = parsed.get(key)
        if type(value) is not str or value == "":
            raise ProtocolError(f"Request property '{key}' must be a non-empty string.")

    version = parsed["protocol_version"]
    if version != PROTOCOL_VERSION:
        raise ProtocolError(f"Unsupported protocol version '{version}'.")

    operation = parsed["operation"]
    if operation not in _KNOWN_OPS:
        raise ProtocolError(f"Unsupported operation '{operation}'.")

    return {
        "protocol_version": version,
        "case": parsed["case"],
        "operation": operation,
        "repository_root": parsed["repository_root"],
    }


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def safe_join(root: Path, relative: str) -> Path:
    """Join ``relative`` under ``root``; reject absolute paths and ``..`` escape."""
    if not relative:
        raise ProtocolError("Fixture path must be a non-empty relative path.")
    rel = Path(relative)
    if rel.is_absolute():
        raise ProtocolError("Fixture path must be relative.")
    for part in rel.parts:
        if part == "..":
            raise ProtocolError("Fixture path escapes its declared root.")
    candidate = (root / rel).resolve()
    root_resolved = root.resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        raise ProtocolError("Fixture path escapes its declared root.") from None
    return candidate


# ---------------------------------------------------------------------------
# Case → NormalizeRequest
# ---------------------------------------------------------------------------


def _optional_object(manifest: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = manifest.get(key)
    if value is None:
        return {}
    if type(value) is not dict:
        raise ProtocolError(f"Case field '{key}' must be an object when present.")
    return value


def map_source_context(raw: Mapping[str, Any]) -> SourceContext:
    group_id = raw.get("group_id")
    if group_id is not None and type(group_id) is not str:
        raise ProtocolError("source_context.group_id must be a string when present.")
    base = raw.get("base_byte_offset", 0)
    if base is None:
        base = 0
    if type(base) is not int or isinstance(base, bool):
        raise ProtocolError("source_context.base_byte_offset must be an integer.")
    partial = raw.get("partial", False)
    if type(partial) is not bool:
        raise ProtocolError("source_context.partial must be a boolean.")
    return SourceContext(
        group_id=group_id,
        base_byte_offset=base,
        partial=partial,
    )


def map_bounds(raw: Mapping[str, Any]) -> Bounds:
    """Triple-state bounds at the case→API boundary (spec §7)."""
    if not raw:
        return Bounds()

    ta_raw = raw.get("tool_arguments")
    if ta_raw is None:
        tool_arguments = ToolArgumentBounds()
    elif type(ta_raw) is not dict:
        raise ProtocolError("bounds.tool_arguments must be an object when present.")
    elif "max_characters" in ta_raw:
        mc = ta_raw["max_characters"]
        if mc is not None and (type(mc) is not int or isinstance(mc, bool)):
            raise ProtocolError("bounds.tool_arguments.max_characters must be int or null.")
        tool_arguments = ToolArgumentBounds(max_characters=mc)
    else:
        tool_arguments = ToolArgumentBounds()

    tr_raw = raw.get("tool_results")
    if tr_raw is None:
        tool_results = ToolResultBounds()
    elif type(tr_raw) is not dict:
        raise ProtocolError("bounds.tool_results must be an object when present.")
    else:
        kwargs: dict[str, Any] = {}
        if "max_characters" in tr_raw:
            mc = tr_raw["max_characters"]
            if mc is not None and (type(mc) is not int or isinstance(mc, bool)):
                raise ProtocolError(
                    "bounds.tool_results.max_characters must be int or null."
                )
            kwargs["max_characters"] = mc
        if "strategy" in tr_raw:
            strategy = tr_raw["strategy"]
            if strategy not in ("head", "head-tail"):
                raise ProtocolError(
                    "bounds.tool_results.strategy must be 'head' or 'head-tail'."
                )
            kwargs["strategy"] = strategy
        tool_results = ToolResultBounds(**kwargs)

    return Bounds(tool_arguments=tool_arguments, tool_results=tool_results)


def map_filters(raw: Mapping[str, Any]) -> Filters:
    if not raw:
        return Filters()
    if "tool_results" not in raw:
        return Filters()
    value = raw["tool_results"]
    if value not in ("include", "omit"):
        raise ProtocolError("filters.tool_results must be 'include' or 'omit'.")
    return Filters(tool_results=value)


def write_indented_from_case(manifest: Mapping[str, Any]) -> bool:
    opts = _optional_object(manifest, "projection_options")
    value = opts.get("write_indented", False)
    if type(value) is not bool:
        raise ProtocolError("projection_options.write_indented must be a boolean.")
    return value


# ---------------------------------------------------------------------------
# Diagnostics wire (protocol response casing — always camelCase)
# ---------------------------------------------------------------------------


def diagnostics_to_wire(diagnostics: tuple[Diagnostic, ...] | list[Diagnostic]) -> list[dict[str, Any]]:
    """Protocol diagnostic objects: code, message; optional inputLine/recordIndex/count."""
    out: list[dict[str, Any]] = []
    for diagnostic in diagnostics:
        item: dict[str, Any] = {
            "code": diagnostic.code,
            "message": diagnostic.message,
        }
        if diagnostic.input_line is not None:
            item["inputLine"] = diagnostic.input_line
        if diagnostic.record_index is not None:
            item["recordIndex"] = diagnostic.record_index
        if diagnostic.count is not None:
            item["count"] = diagnostic.count
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------


def success_response(
    case: str,
    operation: str,
    output_text: str,
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "case": case,
        "operation": operation,
        "status": "success",
        "output_text": output_text,
        "diagnostics": diagnostics,
        "fatal_error": None,
    }


def fatal_error_response(
    case: str,
    operation: str,
    code: str,
    message: str,
) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "case": case,
        "operation": operation,
        "status": "fatal-error",
        "output_text": None,
        "diagnostics": [],
        "fatal_error": {"code": code, "message": message},
    }


def protocol_error_response(
    case: str,
    operation: str,
    message: str,
    *,
    code: str = "invalid_request",
) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "case": case,
        "operation": operation,
        "status": "protocol-error",
        "output_text": None,
        "diagnostics": [],
        "fatal_error": {"code": code, "message": message},
    }


def emit_response(response: Mapping[str, Any]) -> None:
    """Write exactly one compact JSON object to stdout (no trailing commentary)."""
    sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")))


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------


def load_case_manifest(repository_root: Path, case_id: str) -> tuple[Path, dict[str, Any]]:
    cases_root = (repository_root / "conformance" / "cases").resolve()
    if not cases_root.is_dir():
        raise ProtocolError("conformance/cases is missing under repository_root.")
    case_directory = safe_join(cases_root, case_id)
    manifest_path = case_directory / "case.json"
    if not manifest_path.is_file():
        raise ProtocolError("Case manifest case.json is missing.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ProtocolError("Case manifest is not valid JSON.") from None
    if type(manifest) is not dict:
        raise ProtocolError("Case manifest must be a JSON object.")
    if manifest.get("id") != case_id:
        raise ProtocolError("The requested case does not match its manifest ID.")
    return case_directory, manifest


def execute_normalize(
    case_directory: Path,
    manifest: Mapping[str, Any],
    operation: str,
) -> tuple[str, list[dict[str, Any]]]:
    """Map case → request, normalize, project, serialize. Raises TrajectoryError."""
    if operation == "list-trajectories":
        raise ProtocolError(
            "Operation 'list-trajectories' is not implemented in this early runner."
        )
    if operation not in _NORMALIZE_OPS:
        raise ProtocolError(f"Unsupported operation '{operation}'.")

    transcript_name = manifest.get("transcript")
    if type(transcript_name) is not str or transcript_name == "":
        raise ProtocolError("Case field 'transcript' must be a non-empty string.")
    transcript_path = safe_join(case_directory, transcript_name)
    try:
        transcript = transcript_path.read_bytes()
    except OSError:
        raise ProtocolError("Failed to read transcript bytes.") from None

    source = manifest.get("source")
    if type(source) is not str or source == "":
        raise ProtocolError("Case field 'source' must be a non-empty string.")

    source_context = map_source_context(_optional_object(manifest, "source_context"))
    bounds = map_bounds(_optional_object(manifest, "bounds"))
    filters = map_filters(_optional_object(manifest, "filters"))
    write_indented = write_indented_from_case(manifest)

    request = NormalizeRequest(
        source=source,
        transcript=transcript,
        source_context=source_context,
        options=NormalizeOptions(bounds=bounds, filters=filters),
    )

    trajectory = normalize_to_ir(request)
    wire_diagnostics = diagnostics_to_wire(trajectory.diagnostics)

    if operation == "normalize-letta":
        output = serialize_projection(
            project_letta(trajectory), write_indented=write_indented
        )
    elif operation == "normalize-canonical":
        output = serialize_projection(
            project_canonical(trajectory), write_indented=write_indented
        )
    elif operation == "normalize-hypabolic":
        output = serialize_projection(
            project_hypabolic(trajectory), write_indented=write_indented
        )
    elif operation == "project-openai":
        output = serialize_projection(
            project_openai(trajectory), write_indented=write_indented
        )
    elif operation == "project-minimal-jsonl":
        # write_indented is NOT used for jsonl (spec §7).
        output = project_minimal_jsonl(trajectory)
    elif operation == "project-otel":
        output = serialize_projection(
            project_otel_genai(trajectory), write_indented=write_indented
        )
    else:
        raise ProtocolError(f"Unsupported operation '{operation}'.")

    return output, wire_diagnostics


def execute(request: Mapping[str, str]) -> dict[str, Any]:
    """Full case execution after protocol preamble parse."""
    repository_root = Path(request["repository_root"]).resolve()
    case_id = request["case"]
    operation = request["operation"]

    case_directory, manifest = load_case_manifest(repository_root, case_id)

    operations = manifest.get("operation")
    if type(operations) is not dict:
        raise ProtocolError("Case field 'operation' must be an object.")
    if operation not in operations:
        raise ProtocolError(
            f"Case '{case_id}' does not declare operation '{operation}'."
        )

    try:
        output_text, diagnostics = execute_normalize(
            case_directory, manifest, operation
        )
    except TrajectoryError as err:
        return fatal_error_response(case_id, operation, err.code, err.message)
    except ProtocolError:
        raise
    except Exception:
        # Content-safe: never leak stacks, paths, or transcript fragments.
        raise ProtocolError(
            "Unexpected failure while executing the conformance operation."
        ) from None

    return success_response(case_id, operation, output_text, diagnostics)


def main(argv: list[str] | None = None) -> int:
    """Runner entry. Returns process exit code (0 or 2)."""
    args = list(sys.argv[1:] if argv is None else argv)
    case_hint = ""
    operation_hint = ""
    try:
        text = read_request_text(args)
        request = parse_request(text)
        case_hint = request["case"]
        operation_hint = request["operation"]
        response = execute(request)
        emit_response(response)
        return 0 if response["status"] != "protocol-error" else 2
    except ProtocolError as err:
        emit_response(
            protocol_error_response(case_hint, operation_hint, err.message)
        )
        return 2
    except Exception:
        # Outer guard for preamble failures that are not ProtocolError.
        emit_response(
            protocol_error_response(
                case_hint,
                operation_hint,
                "Unexpected failure while handling the conformance request.",
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
