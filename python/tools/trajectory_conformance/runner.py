"""Protocol v1 conformance runner (PY-10-full + LS-02 stream protocol).

Implements the full protocol-v1 surface:

- Normative preamble, response templates, case→NormalizeRequest mapping
- Free-function normalize/project ops for all six output schemas
- Full §7 listing-runner algorithm (``list-trajectories`` + ``$ROOT`` rewrite)
- Stream sequence ops (``stream-sequence`` / ``stream-replay`` / apply ops):
  accepted by the protocol; return ``status=unsupported`` until stream engines
  land (LS-04+).

Protocol v1 operations (request-v1.schema.json enum — all wired):

- ``normalize-letta`` / ``normalize-canonical`` / ``normalize-hypabolic``
- ``project-openai`` / ``project-minimal-jsonl`` / ``project-otel``
- ``list-trajectories``
- ``stream-sequence`` / ``stream-replay`` / stream-apply-* / ``stream-finish`` /
  ``stream-reset`` (unsupported until stream engine)

Authority:
  - docs/python-implementation-spec.md §7 + §9 PY-10-full
  - docs/live-session-streaming-plan.md LS-02
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
    StreamOptions,
    ToolArgumentBounds,
    ToolResultBounds,
    TrajectoryError,
    apply_append,
    apply_snapshot,
    create_stream,
    finish_stream,
    list_trajectories,
    normalize_to_ir,
    project_canonical,
    project_hypabolic,
    project_letta,
    project_minimal_jsonl,
    project_openai,
    project_otel_genai,
    reset_stream,
    serialize_projection,
)
from hypabolic_trajectory.diagnostics import Diagnostic
from hypabolic_trajectory.dto import TrajectoryListing, TrajectoryListingPage
from hypabolic_trajectory.streaming.types import (
    BytePosition,
    StreamCursor,
    StreamResetRequest,
    StreamState,
    StreamUpdate,
)

PROTOCOL_VERSION: Final[str] = "1"

# Batch protocol-v1 operation set (normalize/list).
PROTOCOL_BATCH_OPERATIONS: Final[frozenset[str]] = frozenset(
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

# LS-02 stream protocol ops (engines return unsupported until LS-04+).
PROTOCOL_STREAM_OPERATIONS: Final[frozenset[str]] = frozenset(
    {
        "stream-sequence",
        "stream-replay",
        "stream-apply-append",
        "stream-apply-snapshot",
        "stream-apply-ahp-actions",
        "stream-apply-ahp-snapshot",
        "stream-finish",
        "stream-reset",
    }
)

# Full protocol-v1 operation set (must match request-v1.schema.json enum).
PROTOCOL_V1_OPERATIONS: Final[frozenset[str]] = (
    PROTOCOL_BATCH_OPERATIONS | PROTOCOL_STREAM_OPERATIONS
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
    {"claude-code", "codex", "grok-build"}
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
    include_encrypted = raw.get("include_encrypted_reasoning", False)
    if type(include_encrypted) is not bool:
        raise ProtocolError(
            "source_context.include_encrypted_reasoning must be a boolean."
        )
    return SourceContext(
        group_id=group_id,
        base_byte_offset=base,
        partial=partial,
        include_encrypted_reasoning=include_encrypted,
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


def unsupported_response(
    case: str,
    operation: str,
    *,
    code: str = "capability_unsupported",
    message: str = "Stream engine is not implemented yet.",
) -> dict[str, Any]:
    """Stream op accepted by protocol but engine not yet available (LS-02)."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "case": case,
        "operation": operation,
        "status": "unsupported",
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


def _parse_stream_cursor(raw: Mapping[str, Any] | None) -> StreamCursor | None:
    if raw is None:
        return None
    if type(raw) is not dict:
        raise ProtocolError("Step cursor must be an object when present.")
    source = raw.get("source")
    group_id = raw.get("group_id")
    if type(source) is not str or type(group_id) is not str:
        raise ProtocolError("Step cursor requires source and group_id strings.")
    generation = raw.get("generation", 0)
    if type(generation) is not int or isinstance(generation, bool) or generation < 0:
        raise ProtocolError("Step cursor generation must be a non-negative integer.")
    pos_raw = raw.get("position")
    if type(pos_raw) is not dict:
        raise ProtocolError("Step cursor position must be an object.")
    if pos_raw.get("kind") != "byte":
        raise ProtocolError("Stream engine supports byte cursors only in this slice.")
    next_off = pos_raw.get("next_byte_offset", 0)
    pending_len = pos_raw.get("pending_byte_length", 0)
    if type(next_off) is not int or isinstance(next_off, bool) or next_off < 0:
        raise ProtocolError("next_byte_offset must be a non-negative integer.")
    if type(pending_len) is not int or isinstance(pending_len, bool) or pending_len < 0:
        raise ProtocolError("pending_byte_length must be a non-negative integer.")
    source_revision = raw.get("source_revision")
    if source_revision is not None and type(source_revision) is not str:
        raise ProtocolError("source_revision must be a string or null.")
    prefix_sha256 = raw.get("prefix_sha256")
    if prefix_sha256 is not None and type(prefix_sha256) is not str:
        raise ProtocolError("prefix_sha256 must be a string or null.")
    return StreamCursor(
        source=source,
        group_id=group_id,
        generation=generation,
        position=BytePosition(
            next_byte_offset=next_off, pending_byte_length=pending_len
        ),
        source_revision=source_revision,
        prefix_sha256=prefix_sha256,
    )


def _load_step_bytes(
    case_directory: Path, step_input: Mapping[str, Any]
) -> bytes:
    if "inline_utf8" in step_input and step_input["inline_utf8"] is not None:
        text = step_input["inline_utf8"]
        if type(text) is not str:
            raise ProtocolError("inline_utf8 must be a string when present.")
        return text.encode("utf-8")
    material = step_input.get("material")
    if type(material) is not str or material == "":
        raise ProtocolError("Step input requires material or inline_utf8.")
    path = safe_join(case_directory, material)
    try:
        return path.read_bytes()
    except OSError:
        raise ProtocolError("Failed to read step material bytes.") from None


def _stream_options_from_manifest(manifest: Mapping[str, Any]) -> StreamOptions:
    source = manifest.get("source")
    if type(source) is not str or source == "":
        raise ProtocolError("Case field 'source' must be a non-empty string.")
    group_id = manifest.get("group_id")
    if group_id is not None and type(group_id) is not str:
        raise ProtocolError("group_id must be a string when present.")
    opts_raw = _optional_object(manifest, "options")
    delivery = opts_raw.get("delivery", "both")
    if delivery not in ("both", "snapshot", "delta"):
        raise ProtocolError("options.delivery is invalid.")
    include_provisional = opts_raw.get("include_provisional", True)
    require_complete_lines = opts_raw.get("require_complete_lines", True)
    finalize_on_close = opts_raw.get("finalize_on_close", True)
    reset_policy = opts_raw.get("reset_policy", "return-reset-required")
    max_pending = opts_raw.get("max_pending_bytes")
    max_line = opts_raw.get("max_line_bytes")
    if max_pending is not None and (
        type(max_pending) is not int or isinstance(max_pending, bool)
    ):
        raise ProtocolError("max_pending_bytes must be an integer when present.")
    if max_line is not None and (
        type(max_line) is not int or isinstance(max_line, bool)
    ):
        raise ProtocolError("max_line_bytes must be an integer when present.")
    return StreamOptions(
        source=source,
        group_id=group_id,
        delivery=delivery,  # type: ignore[arg-type]
        include_provisional=bool(include_provisional),
        require_complete_lines=bool(require_complete_lines),
        finalize_on_close=bool(finalize_on_close),
        reset_policy=reset_policy,  # type: ignore[arg-type]
        max_pending_bytes=max_pending,
        max_line_bytes=max_line,
    )


def _apply_step(
    state: StreamState,
    case_directory: Path,
    step_input: Mapping[str, Any],
) -> tuple[StreamState, StreamUpdate]:
    kind = step_input.get("kind")
    if type(kind) is not str:
        raise ProtocolError("Step input.kind must be a string.")
    source_revision = step_input.get("source_revision")
    if source_revision is not None and type(source_revision) is not str:
        raise ProtocolError("source_revision must be a string or null.")
    cursor = _parse_stream_cursor(step_input.get("cursor"))

    if kind == "append-bytes":
        data = _load_step_bytes(case_directory, step_input)
        return apply_append(
            state, data, cursor=cursor, source_revision=source_revision
        )
    if kind == "snapshot-bytes":
        data = _load_step_bytes(case_directory, step_input)
        return apply_snapshot(
            state,
            data,
            source_revision=source_revision or "",
            cursor=cursor,
        )
    if kind == "finish":
        return finish_stream(state)
    if kind == "reset":
        reset_raw = step_input.get("reset")
        if type(reset_raw) is not dict:
            raise ProtocolError("reset step requires reset object.")
        reason = reset_raw.get("reason")
        if type(reason) is not str:
            raise ProtocolError("reset.reason must be a string.")
        generation = reset_raw.get("generation")
        if generation is not None and (
            type(generation) is not int or isinstance(generation, bool)
        ):
            raise ProtocolError("reset.generation must be an integer when present.")
        rev = reset_raw.get("source_revision")
        if rev is not None and type(rev) is not str:
            raise ProtocolError("reset.source_revision must be a string or null.")
        material: bytes | None = None
        if "material" in reset_raw and reset_raw["material"] is not None:
            material = _load_step_bytes(case_directory, reset_raw)
        elif "inline_utf8" in reset_raw and reset_raw["inline_utf8"] is not None:
            material = _load_step_bytes(case_directory, reset_raw)
        return reset_stream(
            state,
            StreamResetRequest(
                reason=reason,  # type: ignore[arg-type]
                generation=generation,
                source_revision=rev,
                material=material,
            ),
        )
    if kind in {"ahp-actions", "ahp-snapshot", "hermes-export"}:
        # LS-06/LS-07/provider — not in LS-05 engine.
        raise _StreamEngineUnsupported(
            f"Stream input kind '{kind}' is not implemented in this slice."
        )
    raise ProtocolError(f"Unsupported stream input kind '{kind}'.")


class _StreamEngineUnsupported(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _stream_state_equivalent(a: StreamState, b: StreamState) -> bool:
    """Observable equality for double-invoke (cursor + committed + pending + flags)."""
    if a.finished != b.finished or a.generation != b.generation:
        return False
    if bytes(a.committed_prefix) != bytes(b.committed_prefix):
        return False
    if bytes(a.pending_bytes) != bytes(b.pending_bytes):
        return False
    ca, cb = a.cursor, b.cursor
    if (
        ca.source != cb.source
        or ca.group_id != cb.group_id
        or ca.generation != cb.generation
        or ca.source_revision != cb.source_revision
        or ca.prefix_sha256 != cb.prefix_sha256
    ):
        return False
    pa, pb = ca.position, cb.position
    if type(pa) is not type(pb):
        return False
    if isinstance(pa, BytePosition) and isinstance(pb, BytePosition):
        return (
            pa.next_byte_offset == pb.next_byte_offset
            and pa.pending_byte_length == pb.pending_byte_length
        )
    return True


def _oracle_section(
    manifest: Mapping[str, Any],
    state: StreamState,
    step_results: list[dict[str, Any]],
) -> dict[str, Any] | None:
    oracle = manifest.get("oracle")
    if type(oracle) is not dict:
        return None
    want_append = bool(oracle.get("append_equals_prefix"))
    want_prefix = bool(oracle.get("prefix_re_normalize"))
    if not want_append and not want_prefix:
        return None

    # Fresh snapshot of the final committed prefix must match append-path records.
    opts = _stream_options_from_manifest(manifest)
    oracle_state = create_stream(opts)
    prefix = bytes(state.committed_prefix)
    rev = state.cursor.source_revision or "oracle"
    _, snap_update = apply_snapshot(oracle_state, prefix, source_revision=rev)
    if snap_update.kind not in {"updated", "unchanged"}:
        return {
            "append_equals_prefix": False if want_append else None,
            "prefix_re_normalize": False if want_prefix else None,
        }

    append_ids: list[str] = []
    if state.snapshot is not None:
        append_ids = [r.record.get("id", "") for r in state.snapshot.records]
    snap_ids: list[str] = []
    if snap_update.snapshot is not None:
        snap_ids = [r.record.get("id", "") for r in snap_update.snapshot.records]
    # Also compare last updated step snapshot when present.
    last_updated_ids: list[str] | None = None
    for step in reversed(step_results):
        update = step.get("update")
        if type(update) is dict and update.get("kind") == "updated":
            snap = update.get("snapshot")
            if type(snap) is dict and type(snap.get("records")) is list:
                last_updated_ids = [
                    (rec.get("record") or {}).get("id", "")
                    if type(rec) is dict
                    else ""
                    for rec in snap["records"]
                ]
            break
    ids_match = append_ids == snap_ids
    if last_updated_ids is not None:
        ids_match = ids_match and last_updated_ids == snap_ids
    offset_match = True
    if isinstance(state.cursor.position, BytePosition) and isinstance(
        snap_update.cursor.position, BytePosition
    ):
        offset_match = (
            state.cursor.position.next_byte_offset
            == snap_update.cursor.position.next_byte_offset
            and state.cursor.prefix_sha256 == snap_update.cursor.prefix_sha256
        )
    ok = ids_match and offset_match
    out: dict[str, Any] = {}
    if want_append:
        out["append_equals_prefix"] = ok
    if want_prefix:
        out["prefix_re_normalize"] = ok
    return out


def execute_stream_sequence(
    case_directory: Path,
    manifest: Mapping[str, Any],
) -> str:
    """Run multi-step stream case; returns JSON output_text."""
    steps = manifest.get("steps")
    if type(steps) is not list or len(steps) == 0:
        raise ProtocolError("Stream sequence requires non-empty steps[].")

    # Defer AHP/Hermes until those slices land.
    for step in steps:
        if type(step) is not dict:
            raise ProtocolError("Each step must be an object.")
        step_input = step.get("input")
        if type(step_input) is not dict:
            raise ProtocolError("Each step requires an input object.")
        kind = step_input.get("kind")
        if kind in {"ahp-actions", "ahp-snapshot", "hermes-export"}:
            raise _StreamEngineUnsupported(
                f"Stream input kind '{kind}' is not implemented in this slice."
            )

    state = create_stream(_stream_options_from_manifest(manifest))
    step_results: list[dict[str, Any]] = []
    for step in steps:
        assert type(step) is dict
        step_id = step.get("id", "step")
        if type(step_id) is not str:
            step_id = "step"
        step_input = step["input"]
        assert type(step_input) is dict
        double = step.get("double_invoke", True)
        if type(double) is not bool:
            double = True

        state, update = _apply_step(state, case_directory, step_input)
        idempotent = True
        if double:
            state_after, update2 = _apply_step(state, case_directory, step_input)
            if update.kind in {"updated", "unchanged"}:
                # Prefer pure replay → unchanged; accept deterministic re-install
                # (e.g. reset with same generation/material) when state matches.
                idempotent = update2.kind == "unchanged" or (
                    update2.kind == "updated"
                    and _stream_state_equivalent(state, state_after)
                )
            else:
                # reset-required / error: re-apply must not advance state.
                idempotent = update2.kind == update.kind and _stream_state_equivalent(
                    state, state_after
                )
            state = state_after

        step_results.append(
            {
                "id": step_id,
                "update": update.to_dict(),
                "idempotent": idempotent,
            }
        )

    payload: dict[str, Any] = {"steps": step_results}
    oracle = _oracle_section(manifest, state, step_results)
    if oracle is not None:
        payload["oracle"] = oracle
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def execute(request: Mapping[str, str]) -> dict[str, Any]:
    """Full case execution after protocol preamble parse."""
    repository_root = Path(request["repository_root"]).resolve()
    case_id = request["case"]
    operation = request["operation"]

    case_directory, manifest = load_case_manifest(repository_root, case_id)

    # LS-05: multi-step stream cases via core apply_append / apply_snapshot.
    if operation in PROTOCOL_STREAM_OPERATIONS:
        steps = manifest.get("steps")
        if type(steps) is not list or len(steps) == 0:
            raise ProtocolError(
                f"Stream operation '{operation}' requires a streaming case "
                "with steps[]."
            )
        if operation in {"stream-sequence", "stream-replay"}:
            try:
                output_text = execute_stream_sequence(case_directory, manifest)
            except _StreamEngineUnsupported as err:
                return unsupported_response(
                    case_id, operation, message=err.message
                )
            except TrajectoryError as err:
                return fatal_error_response(
                    case_id, operation, err.code, err.message
                )
            return success_response(case_id, operation, output_text, [])
        # Per-step apply ops remain reserved until dedicated harness lands.
        return unsupported_response(case_id, operation)

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
