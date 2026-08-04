"""Protocol v1 conformance runner (PY-10-full).

Implements the full protocol-v1 surface:

- Normative preamble, response templates, case→NormalizeRequest mapping
- Free-function normalize/project ops for all six output schemas
- Full §7 listing-runner algorithm (``list-trajectories`` + ``$ROOT`` rewrite)

Protocol v1 operations (request-v1.schema.json enum — all wired):

- ``normalize-letta`` / ``normalize-canonical`` / ``normalize-hypabolic``
- ``project-openai`` / ``project-minimal-jsonl`` / ``project-otel``
- ``list-trajectories``

Authority:
  - docs/python-implementation-spec.md §7 + §9 PY-10-full
  - conformance/protocol/request-v1.schema.json
  - conformance/protocol/response-v1.schema.json
  - peer runners (dotnet Program.cs, typescript cli.ts, rust main.rs)
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
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
    list_trajectories,
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
from hypabolic_trajectory.dto import TrajectoryListing, TrajectoryListingPage

PROTOCOL_VERSION: Final[str] = "1"

# Full protocol-v1 operation set (must match request-v1.schema.json enum).
PROTOCOL_V1_OPERATIONS: Final[frozenset[str]] = frozenset(
    {
        "normalize-letta",
        "normalize-canonical",
        "normalize-hypabolic",
        "project-openai",
        "project-minimal-jsonl",
        "project-otel",
        "list-trajectories",
    }
)

# Normalize/project ops share the case→NormalizeRequest + free-function path.
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

_KNOWN_OPS: Final[frozenset[str]] = PROTOCOL_V1_OPERATIONS

# Sources whose declarative fixtures use a ``store/`` prefix; lister root is
# ``{temp_root}/store`` (goldens show ``$ROOT/store/...``). All other sources
# (pi, openclaw, hermes/ahp stubs) list at the temp root itself.
_LISTING_STORE_PREFIX_SOURCES: Final[frozenset[str]] = frozenset(
    {"claude-code", "codex"}
)


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


def _parse_store_updated_at(value: str) -> float:
    """Parse store fixture UTC timestamp to epoch seconds for ``os.utime``."""
    text = value
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ProtocolError(
            "Store fixture updated_at is not a valid ISO-8601 timestamp."
        ) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _rewrite_listing_path(absolute_path: str, temporary_root: Path) -> str:
    """Strip the temp root prefix and emit ``$ROOT/<relative>`` with forward slashes.

    Rewrite is always relative to the **temporary root** (not the lister root),
    so claude-code/codex goldens correctly show ``$ROOT/store/...``.
    """
    root_resolved = temporary_root.resolve()
    item_resolved = Path(absolute_path).resolve()
    try:
        relative = item_resolved.relative_to(root_resolved)
    except ValueError:
        raise ProtocolError("Listing item path escaped the temporary root.") from None
    # relative_to yields ``.`` when equal; a file path should never equal the root.
    rel_posix = relative.as_posix()
    if rel_posix == ".":
        return "$ROOT"
    return f"$ROOT/{rel_posix}"


def _listing_item_to_wire(
    item: TrajectoryListing, temporary_root: Path
) -> dict[str, Any]:
    """Wire item: id, path, optional updated_at / title / size_bytes (omit absent)."""
    wire: dict[str, Any] = {
        "id": item.id,
        "path": _rewrite_listing_path(item.path, temporary_root),
    }
    if item.updated_at is not None:
        wire["updated_at"] = item.updated_at
    if item.title is not None:
        wire["title"] = item.title
    if item.size_bytes is not None:
        wire["size_bytes"] = item.size_bytes
    return wire


def _listing_page_to_wire(
    page: TrajectoryListingPage, temporary_root: Path
) -> dict[str, Any]:
    """Wire page object: items + always-present next_cursor (string or null)."""
    return {
        "items": [
            _listing_item_to_wire(item, temporary_root) for item in page.items
        ],
        "next_cursor": page.next_cursor,
    }


def execute_listing(
    repository_root: Path,
    manifest: Mapping[str, Any],
) -> str:
    """Full §7 listing-runner algorithm. Raises ProtocolError or TrajectoryError."""
    store_name = manifest.get("store")
    if type(store_name) is not str or store_name == "":
        raise ProtocolError("Listing case requires a declarative store.")

    stores_root = (repository_root / "conformance" / "stores").resolve()
    if not stores_root.is_dir():
        raise ProtocolError("conformance/stores is missing under repository_root.")
    store_path = safe_join(stores_root, f"{store_name}/store.json")
    try:
        store_raw = json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ProtocolError("Store fixture is not valid JSON.") from None
    if type(store_raw) is not dict:
        raise ProtocolError("Store fixture must be a JSON object.")
    files = store_raw.get("files")
    if type(files) is not list:
        raise ProtocolError("Store fixture field 'files' must be an array.")

    source = manifest.get("source")
    if type(source) is not str or source == "":
        raise ProtocolError("Case field 'source' must be a non-empty string.")

    listing_opts = _optional_object(manifest, "listing")
    limit_raw = listing_opts.get("limit", 50)
    if type(limit_raw) is not int or isinstance(limit_raw, bool):
        raise ProtocolError("listing.limit must be an integer when present.")
    limit = limit_raw
    all_pages_raw = listing_opts.get("all_pages", False)
    if type(all_pages_raw) is not bool:
        raise ProtocolError("listing.all_pages must be a boolean when present.")
    all_pages = all_pages_raw

    temporary_root = Path(
        tempfile.mkdtemp(prefix="trajectory-conformance-")
    )
    try:
        for entry in files:
            if type(entry) is not dict:
                raise ProtocolError("Each store.files entry must be an object.")
            relative = entry.get("path")
            if type(relative) is not str or relative == "":
                raise ProtocolError(
                    "Store file path must be a non-empty relative path."
                )
            content = entry.get("content")
            if type(content) is not str:
                raise ProtocolError("Store file content must be a string.")
            destination = safe_join(temporary_root, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
            if "updated_at" in entry and entry["updated_at"] is not None:
                updated_at = entry["updated_at"]
                if type(updated_at) is not str:
                    raise ProtocolError(
                        "Store file updated_at must be a string when present."
                    )
                epoch = _parse_store_updated_at(updated_at)
                os.utime(destination, (epoch, epoch))

        if source in _LISTING_STORE_PREFIX_SOURCES:
            listing_root: Path = temporary_root / "store"
        else:
            listing_root = temporary_root

        pages_wire: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            page = list_trajectories(
                source=source,
                root=listing_root,
                cursor=cursor,
                limit=limit,
            )
            pages_wire.append(_listing_page_to_wire(page, temporary_root))
            cursor = page.next_cursor
            if not all_pages or cursor is None:
                break

        if all_pages:
            return serialize_projection(pages_wire)
        return serialize_projection(pages_wire[0])
    finally:
        # Best-effort cleanup — never leave declarative fixture trees behind.
        shutil.rmtree(temporary_root, ignore_errors=True)


def execute_normalize(
    case_directory: Path,
    manifest: Mapping[str, Any],
    operation: str,
) -> tuple[str, list[dict[str, Any]]]:
    """Map case → request, normalize, project, serialize. Raises TrajectoryError."""
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
        if operation == "list-trajectories":
            output_text = execute_listing(repository_root, manifest)
            diagnostics: list[dict[str, Any]] = []
        else:
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
