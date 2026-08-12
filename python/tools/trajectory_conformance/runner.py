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
    apply_ahp_actions,
    apply_ahp_snapshot,
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
    AhpServerSeqPosition,
    BytePosition,
    SnapshotRevisionPosition,
    StreamCursor,
    StreamDiagnostic,
    StreamRecord,
    StreamResetRequest,
    StreamSnapshot,
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
    kind = pos_raw.get("kind")
    source_revision = raw.get("source_revision")
    if source_revision is not None and type(source_revision) is not str:
        raise ProtocolError("source_revision must be a string or null.")
    prefix_sha256 = raw.get("prefix_sha256")
    if prefix_sha256 is not None and type(prefix_sha256) is not str:
        raise ProtocolError("prefix_sha256 must be a string or null.")
    if kind == "byte":
        next_off = pos_raw.get("next_byte_offset", 0)
        pending_len = pos_raw.get("pending_byte_length", 0)
        if type(next_off) is not int or isinstance(next_off, bool) or next_off < 0:
            raise ProtocolError("next_byte_offset must be a non-negative integer.")
        if type(pending_len) is not int or isinstance(pending_len, bool) or pending_len < 0:
            raise ProtocolError("pending_byte_length must be a non-negative integer.")
        position: BytePosition | AhpServerSeqPosition | SnapshotRevisionPosition = (
            BytePosition(next_byte_offset=next_off, pending_byte_length=pending_len)
        )
    elif kind == "ahp-server-seq":
        next_seq = pos_raw.get("next_server_seq", 0)
        last_seq = pos_raw.get("last_server_seq", -1)
        if type(next_seq) is not int or isinstance(next_seq, bool) or next_seq < 0:
            raise ProtocolError("next_server_seq must be a non-negative integer.")
        if type(last_seq) is not int or isinstance(last_seq, bool):
            raise ProtocolError("last_server_seq must be an integer.")
        nbo = pos_raw.get("next_byte_offset")
        if nbo is not None and (
            type(nbo) is not int or isinstance(nbo, bool) or nbo < 0
        ):
            raise ProtocolError("next_byte_offset must be a non-negative integer.")
        position = AhpServerSeqPosition(
            next_server_seq=next_seq,
            last_server_seq=last_seq,
            next_byte_offset=nbo,
        )
    elif kind == "snapshot-revision":
        rev = pos_raw.get("revision")
        if type(rev) is not str:
            raise ProtocolError("snapshot-revision.revision must be a string.")
        csha = pos_raw.get("content_sha256")
        if csha is not None and type(csha) is not str:
            raise ProtocolError("content_sha256 must be a string or null.")
        position = SnapshotRevisionPosition(revision=rev, content_sha256=csha)
    else:
        raise ProtocolError(
            "Stream cursor position.kind must be byte, ahp-server-seq, or snapshot-revision."
        )
    return StreamCursor(
        source=source,
        group_id=group_id,
        generation=generation,
        position=position,
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
    ahp_protocol_version = opts_raw.get("ahp_protocol_version")
    if ahp_protocol_version is not None and type(ahp_protocol_version) is not str:
        raise ProtocolError("ahp_protocol_version must be a string when present.")
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
        ahp_protocol_version=ahp_protocol_version,
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
    if kind == "ahp-snapshot":
        data = _load_step_bytes(case_directory, step_input)
        return apply_ahp_snapshot(
            state,
            data,
            source_revision=source_revision or "",
            cursor=cursor,
        )
    if kind == "ahp-actions":
        data = _load_step_bytes(case_directory, step_input)
        return apply_ahp_actions(state, data, cursor=cursor)
    if kind == "hermes-export":
        # Optional provider — not in LS-06/07 core.
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
    if isinstance(pa, AhpServerSeqPosition) and isinstance(pb, AhpServerSeqPosition):
        return (
            pa.next_server_seq == pb.next_server_seq
            and pa.last_server_seq == pb.last_server_seq
        )
    if isinstance(pa, SnapshotRevisionPosition) and isinstance(
        pb, SnapshotRevisionPosition
    ):
        return (
            pa.revision == pb.revision and pa.content_sha256 == pb.content_sha256
        )
    return False


def _stream_record_parity_key(rec: StreamRecord) -> tuple[Any, ...]:
    """Identity + status/finality fields for append-vs-prefix oracle parity."""
    return (
        rec.record.get("id", ""),
        rec.status,
        rec.provisional_id,
        rec.replaces_provisional_id,
        rec.finalizes_provisional_id,
    )


def _stream_diagnostic_parity_key(d: StreamDiagnostic) -> tuple[Any, ...]:
    return (d.code, d.message, d.input_line, d.record_index, d.count)


def _oracle_snapshots_match(
    append_snap: StreamSnapshot | None,
    oracle_snap: StreamSnapshot | None,
    append_cursor: StreamCursor,
    oracle_cursor: StreamCursor,
) -> bool:
    """Full snapshot/status/diagnostics/cursor parity for prefix-oracle equality.

    A missing stream snapshot (never updated — e.g. pure pending bytes held) is
    treated as an empty incomplete snapshot so it can match a fresh prefix
    re-normalize of an empty committed prefix.
    """
    append_records = () if append_snap is None else append_snap.records
    oracle_records = () if oracle_snap is None else oracle_snap.records
    append_keys = [_stream_record_parity_key(r) for r in append_records]
    oracle_keys = [_stream_record_parity_key(r) for r in oracle_records]
    if append_keys != oracle_keys:
        return False
    append_diags = (
        ()
        if append_snap is None
        else tuple(_stream_diagnostic_parity_key(d) for d in append_snap.diagnostics)
    )
    oracle_diags = (
        ()
        if oracle_snap is None
        else tuple(_stream_diagnostic_parity_key(d) for d in oracle_snap.diagnostics)
    )
    if append_diags != oracle_diags:
        return False
    append_complete = False if append_snap is None else append_snap.complete
    oracle_complete = False if oracle_snap is None else oracle_snap.complete
    if append_complete != oracle_complete:
        return False
    if isinstance(append_cursor.position, BytePosition) and isinstance(
        oracle_cursor.position, BytePosition
    ):
        if (
            append_cursor.position.next_byte_offset
            != oracle_cursor.position.next_byte_offset
            or append_cursor.prefix_sha256 != oracle_cursor.prefix_sha256
        ):
            return False
    return True


def _action_snapshot_parity(
    action_snap: StreamSnapshot | None,
    snapshot_snap: StreamSnapshot | None,
) -> bool:
    """Non-meta record id/status/content parity for AHP action ≡ snapshot oracle."""
    if action_snap is None or snapshot_snap is None:
        return action_snap is None and snapshot_snap is None

    def _non_meta(records: tuple[StreamRecord, ...] | list[StreamRecord]) -> list[tuple]:
        out: list[tuple] = []
        for r in records:
            role = r.record.get("role")
            if role == "meta":
                continue
            out.append((r.record.get("id"), r.status, role, r.record.get("content")))
        return out

    # Full identity parity including meta (matches unit oracle).
    act_ids = [(r.record.get("id"), r.status) for r in action_snap.records]
    snap_ids = [(r.record.get("id"), r.status) for r in snapshot_snap.records]
    if act_ids != snap_ids:
        return False
    return _non_meta(action_snap.records) == _non_meta(snapshot_snap.records)


def _oracle_section(
    manifest: Mapping[str, Any],
    state: StreamState,
    step_results: list[dict[str, Any]],
    *,
    case_directory: Path | None = None,
) -> dict[str, Any] | None:
    oracle = manifest.get("oracle")
    if type(oracle) is not dict:
        return None
    want_append = bool(oracle.get("append_equals_prefix"))
    want_prefix = bool(oracle.get("prefix_re_normalize"))
    want_action = bool(oracle.get("action_equals_snapshot"))
    if not want_append and not want_prefix and not want_action:
        return None

    out: dict[str, Any] = {}
    opts = _stream_options_from_manifest(manifest)

    if want_append or want_prefix:
        # Fresh snapshot of the final committed prefix must match append-path records,
        # status/provisional finality, diagnostics, and cursor fingerprint.
        # When the append path finished (stable→final), mirror finish so oracle
        # finality matches (LS-08 stable-to-final).
        oracle_state = create_stream(opts)
        prefix = bytes(state.committed_prefix)
        rev = state.cursor.source_revision or "oracle"
        oracle_state, snap_update = apply_snapshot(
            oracle_state, prefix, source_revision=rev
        )
        if snap_update.kind in {"updated", "unchanged"} and state.finished:
            oracle_state, snap_update = finish_stream(oracle_state)
        if snap_update.kind not in {"updated", "unchanged"}:
            if want_append:
                out["append_equals_prefix"] = False
            if want_prefix:
                out["prefix_re_normalize"] = False
        else:
            ok = _oracle_snapshots_match(
                state.snapshot,
                snap_update.snapshot,
                state.cursor,
                snap_update.cursor,
            )
            # Cross-check last updated step snapshot records when present (wire form).
            if ok:
                for step in reversed(step_results):
                    update = step.get("update")
                    if type(update) is not dict or update.get("kind") != "updated":
                        continue
                    snap = update.get("snapshot")
                    if type(snap) is not dict or type(snap.get("records")) is not list:
                        break
                    if snap_update.snapshot is None:
                        ok = False
                        break
                    wire_keys = []
                    for rec in snap["records"]:
                        if type(rec) is not dict:
                            wire_keys.append(("", "", None, None, None))
                            continue
                        body = rec.get("record") if type(rec.get("record")) is dict else {}
                        wire_keys.append(
                            (
                                body.get("id", "") if type(body) is dict else "",
                                rec.get("status", ""),
                                rec.get("provisional_id"),
                                rec.get("replaces_provisional_id"),
                                rec.get("finalizes_provisional_id"),
                            )
                        )
                    oracle_keys = [
                        _stream_record_parity_key(r) for r in snap_update.snapshot.records
                    ]
                    if wire_keys != oracle_keys:
                        ok = False
                    wire_diags = []
                    diags = snap.get("diagnostics")
                    if type(diags) is list:
                        for d in diags:
                            if type(d) is not dict:
                                continue
                            wire_diags.append(
                                (
                                    d.get("code", ""),
                                    d.get("message", ""),
                                    d.get("input_line"),
                                    d.get("record_index"),
                                    d.get("count"),
                                )
                            )
                    oracle_diags = [
                        _stream_diagnostic_parity_key(d)
                        for d in snap_update.snapshot.diagnostics
                    ]
                    if wire_diags != oracle_diags:
                        ok = False
                    break
            if want_append:
                out["append_equals_prefix"] = ok
            if want_prefix:
                out["prefix_re_normalize"] = ok

    if want_action:
        material_name = oracle.get("snapshot_material") or "step-snapshot.json"
        rev = oracle.get("snapshot_source_revision") or "ahp-equiv-1"
        if type(material_name) is not str or type(rev) is not str:
            out["action_equals_snapshot"] = False
        elif case_directory is None:
            out["action_equals_snapshot"] = False
        else:
            try:
                material = (case_directory / material_name).read_bytes()
            except OSError:
                out["action_equals_snapshot"] = False
            else:
                snap_state = create_stream(opts)
                _, snap_update = apply_ahp_snapshot(
                    snap_state, material, source_revision=rev
                )
                out["action_equals_snapshot"] = (
                    snap_update.kind in {"updated", "unchanged"}
                    and _action_snapshot_parity(state.snapshot, snap_update.snapshot)
                )

    return out if out else None


def execute_stream_sequence(
    case_directory: Path,
    manifest: Mapping[str, Any],
) -> str:
    """Run multi-step stream case; returns JSON output_text."""
    steps = manifest.get("steps")
    if type(steps) is not list or len(steps) == 0:
        raise ProtocolError("Stream sequence requires non-empty steps[].")

    # Hermes provider streaming remains optional (not LS-06/07 core).
    for step in steps:
        if type(step) is not dict:
            raise ProtocolError("Each step must be an object.")
        step_input = step.get("input")
        if type(step_input) is not dict:
            raise ProtocolError("Each step requires an input object.")
        kind = step_input.get("kind")
        if kind == "hermes-export":
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

        pre_cursor = state.cursor
        state, update = _apply_step(state, case_directory, step_input)
        idempotent = True
        if double:
            # Append true-replay: re-supply with the cursor that governed the first
            # apply (step cursor when present, else pre-apply state cursor). Content
            # equality alone must not short-circuit legitimate identical growth.
            kind = step_input.get("kind")
            if (
                kind == "append-bytes"
                and update.kind in {"updated", "unchanged"}
            ):
                replay_cursor = _parse_stream_cursor(step_input.get("cursor"))
                if replay_cursor is None:
                    replay_cursor = pre_cursor
                data = _load_step_bytes(case_directory, step_input)
                source_revision = step_input.get("source_revision")
                if source_revision is not None and type(source_revision) is not str:
                    source_revision = None
                state_after, update2 = apply_append(
                    state,
                    data,
                    cursor=replay_cursor,
                    source_revision=source_revision,
                )
            else:
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
    oracle = _oracle_section(
        manifest, state, step_results, case_directory=case_directory
    )
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
