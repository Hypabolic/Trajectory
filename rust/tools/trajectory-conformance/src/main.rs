#![forbid(unsafe_code)]
#![doc = "Private versioned conformance protocol runner for the Rust implementation."]

use std::fs::{self, File};
use std::io::{self, Read as _};
use std::path::{Component, Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use chrono::DateTime;
use hypabolic_trajectory::{
    AhpServerSeqPosition, BytePosition, ListingOptions, NormalizeOptions, NormalizeRequest,
    SnapshotRevisionPosition, SourceContext, StreamCursor, StreamDelivery, StreamOptions,
    StreamPosition, StreamResetRequest, StreamSnapshot, StreamState, StreamUpdate, TrajectoryError,
    TrajectorySource, TruncationStrategy, apply_ahp_actions, apply_ahp_snapshot, apply_append,
    apply_snapshot, create_stream, finish_stream, list_ahp_trajectories,
    list_claude_code_trajectories, list_codex_trajectories, list_grok_build_trajectories,
    list_hermes_trajectories, list_openclaw_trajectories, list_pi_trajectories, normalize_ahp,
    normalize_claude_code, normalize_codex, normalize_grok_build, normalize_hermes,
    normalize_openclaw, normalize_pi, project_canonical, project_hypabolic, project_letta,
    project_minimal_jsonl, project_openai, project_opentelemetry, reset_stream, update_to_value,
};
use serde::Deserialize;
use serde_json::{Map, Value, json};

#[derive(Deserialize)]
struct Request {
    protocol_version: String,
    case: String,
    operation: String,
    repository_root: String,
}

#[derive(Deserialize)]
struct Manifest {
    id: String,
    source: String,
    #[serde(default)]
    group_id: Option<String>,
    #[serde(default)]
    transcript: Option<String>,
    #[serde(default)]
    operation: Map<String, Value>,
    #[serde(default)]
    steps: Option<Vec<Value>>,
    #[serde(default)]
    options: Option<Value>,
    #[serde(default)]
    oracle: Option<Value>,
    store: Option<String>,
    listing: Option<ListingManifest>,
    #[serde(default)]
    source_context: SourceContextManifest,
    #[serde(default)]
    bounds: BoundsManifest,
    #[serde(default)]
    filters: FiltersManifest,
}

fn is_stream_operation(operation: &str) -> bool {
    matches!(
        operation,
        "stream-sequence"
            | "stream-replay"
            | "stream-apply-append"
            | "stream-apply-snapshot"
            | "stream-apply-ahp-actions"
            | "stream-apply-ahp-snapshot"
            | "stream-finish"
            | "stream-reset"
    )
}

#[derive(Default, Deserialize)]
struct SourceContextManifest {
    group_id: Option<String>,
    base_byte_offset: Option<i64>,
    partial: Option<bool>,
    /// Spec allows boolean true OR string `"true"` (match .NET/TS).
    #[serde(default, deserialize_with = "deserialize_include_encrypted_reasoning")]
    include_encrypted_reasoning: Option<bool>,
}

fn deserialize_include_encrypted_reasoning<'de, D>(
    deserializer: D,
) -> Result<Option<bool>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let value = Option::<Value>::deserialize(deserializer)?;
    Ok(match value {
        None | Some(Value::Null) => None,
        Some(Value::Bool(flag)) => Some(flag),
        Some(Value::String(text)) => Some(text.eq_ignore_ascii_case("true")),
        Some(_) => Some(false),
    })
}

#[derive(Default, Deserialize)]
struct BoundsManifest {
    tool_arguments: Option<BoundManifest>,
    tool_results: Option<ResultBoundManifest>,
}

#[derive(Deserialize)]
struct BoundManifest {
    max_characters: Option<usize>,
}

#[derive(Deserialize)]
struct ResultBoundManifest {
    max_characters: Option<usize>,
    strategy: Option<String>,
}

#[derive(Default, Deserialize)]
struct FiltersManifest {
    tool_results: Option<String>,
}

#[derive(Deserialize)]
struct ListingManifest {
    limit: Option<usize>,
    all_pages: Option<bool>,
}

#[derive(Deserialize)]
struct Store {
    files: Vec<StoreFile>,
}

#[derive(Deserialize)]
struct StoreFile {
    path: String,
    content: String,
    updated_at: Option<String>,
}

fn main() {
    match run() {
        Ok(response) => {
            print!(
                "{}",
                serde_json::to_string(&response).expect("response is serializable")
            );
        }
        Err(error) => {
            print!(
                "{}",
                serde_json::to_string(&json!({
                    "case": "",
                    "operation": "",
                    "status": "protocol-error",
                    "output_text": null,
                    "diagnostics": [],
                    "fatal_error": {
                        "code": "invalid_request",
                        "message": error,
                    },
                }))
                .expect("response is serializable")
            );
            std::process::exit(2);
        }
    }
}

fn run() -> Result<Value, String> {
    let request = read_request()?;
    if request.protocol_version != "1" {
        return Err(format!(
            "Unsupported protocol version '{}'.",
            request.protocol_version
        ));
    }
    let repository_root = PathBuf::from(&request.repository_root);
    let cases_root = repository_root.join("conformance").join("cases");
    let case_directory = safe_join(&cases_root, &request.case)?;
    let manifest: Manifest = read_json(&case_directory.join("case.json"))?;
    if manifest.id != request.case {
        return Err("The requested case does not match its manifest ID.".into());
    }
    if !matches!(
        manifest.source.as_str(),
        "pi" | "claude-code" | "codex" | "openclaw" | "hermes" | "ahp" | "grok-build"
    ) {
        return Err(format!(
            "Rust does not support source '{}'.",
            manifest.source
        ));
    }

    // LS-05: multi-step stream sequence via core apply_append / apply_snapshot.
    if is_stream_operation(&request.operation) {
        let steps_ok = manifest
            .steps
            .as_ref()
            .map(|steps| !steps.is_empty())
            .unwrap_or(false);
        if !steps_ok {
            return Err(format!(
                "Stream operation '{}' requires a streaming case with steps[].",
                request.operation
            ));
        }
        if request.operation == "stream-sequence" || request.operation == "stream-replay" {
            match execute_stream_sequence(&case_directory, &manifest) {
                Ok(output_text) => {
                    return Ok(json!({
                        "protocol_version": "1",
                        "case": request.case,
                        "operation": request.operation,
                        "status": "success",
                        "output_text": output_text,
                        "diagnostics": [],
                        "fatal_error": null,
                    }));
                }
                Err(StreamEngineError::Unsupported(message)) => {
                    return Ok(json!({
                        "protocol_version": "1",
                        "case": request.case,
                        "operation": request.operation,
                        "status": "unsupported",
                        "output_text": null,
                        "diagnostics": [],
                        "fatal_error": {
                            "code": "capability_unsupported",
                            "message": message,
                        },
                    }));
                }
                Err(StreamEngineError::Protocol(message)) => return Err(message),
                Err(StreamEngineError::Fatal(error)) => {
                    return Ok(json!({
                        "protocol_version": "1",
                        "case": request.case,
                        "operation": request.operation,
                        "status": "fatal-error",
                        "output_text": null,
                        "diagnostics": [],
                        "fatal_error": {
                            "code": error.code,
                            "message": error.message,
                        },
                    }));
                }
            }
        }
        return Ok(json!({
            "protocol_version": "1",
            "case": request.case,
            "operation": request.operation,
            "status": "unsupported",
            "output_text": null,
            "diagnostics": [],
            "fatal_error": {
                "code": "capability_unsupported",
                "message": "Per-step stream apply ops are not implemented yet.",
            },
        }));
    }

    if !manifest.operation.contains_key(&request.operation) {
        return Err(format!(
            "Case '{}' does not declare operation '{}'.",
            request.case, request.operation
        ));
    }

    let result = execute(
        &repository_root,
        &case_directory,
        &manifest,
        &request.operation,
    );
    match result {
        Ok((output_text, diagnostics)) => Ok(json!({
            "case": request.case,
            "operation": request.operation,
            "status": "success",
            "output_text": output_text,
            "diagnostics": diagnostics,
            "fatal_error": null,
        })),
        Err(error) => Ok(json!({
            "case": request.case,
            "operation": request.operation,
            "status": "fatal-error",
            "output_text": null,
            "diagnostics": [],
            "fatal_error": {
                "code": error.code,
                "message": error.message,
            },
        })),
    }
}

enum StreamEngineError {
    Unsupported(String),
    Protocol(String),
    Fatal(TrajectoryError),
}

fn parse_trajectory_source(name: &str) -> Result<TrajectorySource, StreamEngineError> {
    match name {
        "pi" => Ok(TrajectorySource::Pi),
        "claude-code" => Ok(TrajectorySource::ClaudeCode),
        "codex" => Ok(TrajectorySource::Codex),
        "openclaw" => Ok(TrajectorySource::OpenClaw),
        "hermes" => Ok(TrajectorySource::Hermes),
        "ahp" => Ok(TrajectorySource::Ahp),
        "grok-build" => Ok(TrajectorySource::GrokBuild),
        other => Err(StreamEngineError::Protocol(format!(
            "Unknown stream source '{other}'."
        ))),
    }
}

fn stream_options_from_manifest(manifest: &Manifest) -> Result<StreamOptions, StreamEngineError> {
    let source = parse_trajectory_source(&manifest.source)?;
    let mut opts = StreamOptions::new(source);
    if let Some(group) = &manifest.group_id {
        opts = opts.with_group_id(group);
    }
    if let Some(Value::Object(map)) = &manifest.options {
        if let Some(Value::String(delivery)) = map.get("delivery") {
            opts.delivery = match delivery.as_str() {
                "snapshot" => StreamDelivery::Snapshot,
                "delta" => StreamDelivery::Delta,
                _ => StreamDelivery::Both,
            };
        }
        if let Some(Value::Bool(v)) = map.get("include_provisional") {
            opts.include_provisional = *v;
        }
        if let Some(Value::Bool(v)) = map.get("require_complete_lines") {
            opts.require_complete_lines = *v;
        }
        if let Some(Value::Bool(v)) = map.get("finalize_on_close") {
            opts.finalize_on_close = *v;
        }
        if let Some(Value::Number(n)) = map.get("max_pending_bytes") {
            if let Some(v) = n.as_i64() {
                opts.max_pending_bytes = Some(v);
            }
        }
        if let Some(Value::Number(n)) = map.get("max_line_bytes") {
            if let Some(v) = n.as_i64() {
                opts.max_line_bytes = Some(v);
            }
        }
        if let Some(Value::String(v)) = map.get("ahp_protocol_version") {
            opts.ahp_protocol_version = Some(v.clone());
        }
    }
    Ok(opts)
}

fn load_step_bytes(
    case_directory: &Path,
    input: &Value,
) -> Result<Vec<u8>, StreamEngineError> {
    if let Some(Value::String(text)) = input.get("inline_utf8") {
        return Ok(text.as_bytes().to_vec());
    }
    let material = input
        .get("material")
        .and_then(Value::as_str)
        .ok_or_else(|| {
            StreamEngineError::Protocol("Step input requires material or inline_utf8.".into())
        })?;
    let path = safe_join(case_directory, material)
        .map_err(StreamEngineError::Protocol)?;
    fs::read(&path).map_err(|_| {
        StreamEngineError::Protocol("Failed to read step material bytes.".into())
    })
}

fn parse_stream_cursor(raw: Option<&Value>) -> Result<Option<StreamCursor>, StreamEngineError> {
    let Some(Value::Object(map)) = raw else {
        return Ok(None);
    };
    let source = map
        .get("source")
        .and_then(Value::as_str)
        .ok_or_else(|| StreamEngineError::Protocol("cursor.source required.".into()))?
        .to_string();
    let group_id = map
        .get("group_id")
        .and_then(Value::as_str)
        .ok_or_else(|| StreamEngineError::Protocol("cursor.group_id required.".into()))?
        .to_string();
    let generation = map
        .get("generation")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    let position_obj = map
        .get("position")
        .and_then(Value::as_object)
        .ok_or_else(|| StreamEngineError::Protocol("cursor.position required.".into()))?;
    let kind = position_obj
        .get("kind")
        .and_then(Value::as_str)
        .ok_or_else(|| StreamEngineError::Protocol("cursor.position.kind required.".into()))?;
    let position = match kind {
        "byte" => StreamPosition::Byte(BytePosition {
            next_byte_offset: position_obj
                .get("next_byte_offset")
                .and_then(Value::as_i64)
                .unwrap_or(0),
            pending_byte_length: position_obj
                .get("pending_byte_length")
                .and_then(Value::as_i64)
                .unwrap_or(0),
        }),
        "ahp-server-seq" => StreamPosition::AhpServerSeq(AhpServerSeqPosition {
            next_server_seq: position_obj
                .get("next_server_seq")
                .and_then(Value::as_i64)
                .unwrap_or(0),
            last_server_seq: position_obj
                .get("last_server_seq")
                .and_then(Value::as_i64)
                .unwrap_or(0),
            next_byte_offset: position_obj
                .get("next_byte_offset")
                .and_then(Value::as_i64),
        }),
        "snapshot-revision" => StreamPosition::SnapshotRevision(SnapshotRevisionPosition {
            revision: position_obj
                .get("revision")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string(),
            content_sha256: position_obj
                .get("content_sha256")
                .and_then(|v| v.as_str().map(str::to_string)),
        }),
        other => {
            return Err(StreamEngineError::Protocol(format!(
                "Unsupported stream cursor position kind '{other}'."
            )));
        }
    };
    let source_revision = map
        .get("source_revision")
        .and_then(|v| v.as_str().map(str::to_string));
    let prefix_sha256 = map
        .get("prefix_sha256")
        .and_then(|v| v.as_str().map(str::to_string));
    Ok(Some(StreamCursor {
        cursor_version: 1,
        source,
        group_id,
        generation,
        position,
        source_revision,
        prefix_sha256,
    }))
}

fn apply_stream_step(
    state: &StreamState,
    case_directory: &Path,
    step_input: &Value,
) -> Result<(StreamState, StreamUpdate), StreamEngineError> {
    let kind = step_input
        .get("kind")
        .and_then(Value::as_str)
        .ok_or_else(|| StreamEngineError::Protocol("Step input.kind required.".into()))?;
    let source_revision = step_input
        .get("source_revision")
        .and_then(Value::as_str);
    let cursor = parse_stream_cursor(step_input.get("cursor"))?;
    match kind {
        "append-bytes" => {
            let data = load_step_bytes(case_directory, step_input)?;
            apply_append(state, &data, cursor.as_ref(), source_revision)
                .map_err(StreamEngineError::Fatal)
        }
        "snapshot-bytes" => {
            let data = load_step_bytes(case_directory, step_input)?;
            apply_snapshot(
                state,
                &data,
                source_revision.unwrap_or(""),
                cursor.as_ref(),
            )
            .map_err(StreamEngineError::Fatal)
        }
        "finish" => finish_stream(state).map_err(StreamEngineError::Fatal),
        "reset" => {
            let reset = step_input
                .get("reset")
                .and_then(Value::as_object)
                .ok_or_else(|| {
                    StreamEngineError::Protocol("reset step requires reset object.".into())
                })?;
            let reason = reset
                .get("reason")
                .and_then(Value::as_str)
                .ok_or_else(|| StreamEngineError::Protocol("reset.reason required.".into()))?
                .to_string();
            let generation = reset.get("generation").and_then(Value::as_u64);
            let rev = reset
                .get("source_revision")
                .and_then(|v| v.as_str().map(str::to_string));
            let material = if reset.contains_key("material") || reset.contains_key("inline_utf8") {
                Some(load_step_bytes(case_directory, &Value::Object(reset.clone()))?)
            } else {
                None
            };
            let request = StreamResetRequest {
                reason,
                generation,
                source_revision: rev,
                prior_cursor: None,
                material,
            };
            reset_stream(state, &request).map_err(StreamEngineError::Fatal)
        }
        "ahp-snapshot" => {
            let data = load_step_bytes(case_directory, step_input)?;
            apply_ahp_snapshot(
                state,
                &data,
                source_revision.unwrap_or(""),
                cursor.as_ref(),
            )
            .map_err(StreamEngineError::Fatal)
        }
        "ahp-actions" => {
            let data = load_step_bytes(case_directory, step_input)?;
            apply_ahp_actions(state, &data, cursor.as_ref()).map_err(StreamEngineError::Fatal)
        }
        "hermes-export" => Err(StreamEngineError::Unsupported(
            "Stream input kind 'hermes-export' is not implemented in this slice.".into(),
        )),
        other => Err(StreamEngineError::Protocol(format!(
            "Unsupported stream input kind '{other}'."
        ))),
    }
}

fn stream_state_equivalent(a: &StreamState, b: &StreamState) -> bool {
    a.finished == b.finished
        && a.generation == b.generation
        && a.committed_prefix == b.committed_prefix
        && a.pending_bytes == b.pending_bytes
        && a.cursor.source == b.cursor.source
        && a.cursor.group_id == b.cursor.group_id
        && a.cursor.generation == b.cursor.generation
        && a.cursor.source_revision == b.cursor.source_revision
        && a.cursor.prefix_sha256 == b.cursor.prefix_sha256
        && a.cursor.position == b.cursor.position
        && a.ahp_last_server_seq == b.ahp_last_server_seq
        && a.ahp_last_snapshot_revision == b.ahp_last_snapshot_revision
        && a.ahp_last_content_sha256 == b.ahp_last_content_sha256
}

fn execute_stream_sequence(
    case_directory: &Path,
    manifest: &Manifest,
) -> Result<String, StreamEngineError> {
    let steps = manifest
        .steps
        .as_ref()
        .ok_or_else(|| StreamEngineError::Protocol("steps required.".into()))?;
    for step in steps {
        if let Some(kind) = step
            .get("input")
            .and_then(|i| i.get("kind"))
            .and_then(Value::as_str)
        {
            if kind == "hermes-export" {
                return Err(StreamEngineError::Unsupported(format!(
                    "Stream input kind '{kind}' is not implemented in this slice."
                )));
            }
        }
    }

    let mut state = create_stream(stream_options_from_manifest(manifest)?);
    let mut step_results = Vec::new();
    for step in steps {
        let step_id = step
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or("step")
            .to_string();
        let step_input = step.get("input").ok_or_else(|| {
            StreamEngineError::Protocol("Each step requires an input object.".into())
        })?;
        let double = step
            .get("double_invoke")
            .and_then(Value::as_bool)
            .unwrap_or(true);
        let pre_cursor = state.cursor.clone();
        let (next_state, update) = apply_stream_step(&state, case_directory, step_input)?;
        state = next_state;
        let mut idempotent = true;
        if double {
            let kind = step_input.get("kind").and_then(Value::as_str).unwrap_or("");
            let (state2, update2) = if kind == "append-bytes"
                && (update.kind == "updated" || update.kind == "unchanged")
            {
                // True append replay: re-supply with the cursor that governed the first apply.
                let replay_cursor = parse_stream_cursor(step_input.get("cursor"))?
                    .unwrap_or(pre_cursor);
                let data = load_step_bytes(case_directory, step_input)?;
                let source_revision = step_input.get("source_revision").and_then(Value::as_str);
                apply_append(&state, &data, Some(&replay_cursor), source_revision)
                    .map_err(StreamEngineError::Fatal)?
            } else {
                apply_stream_step(&state, case_directory, step_input)?
            };
            if update.kind == "updated" || update.kind == "unchanged" {
                idempotent = update2.kind == "unchanged"
                    || (update2.kind == "updated" && stream_state_equivalent(&state, &state2));
            } else {
                idempotent =
                    update2.kind == update.kind && stream_state_equivalent(&state, &state2);
            }
            state = state2;
        }
        step_results.push(json!({
            "id": step_id,
            "update": update_to_value(&update),
            "idempotent": idempotent,
        }));
    }

    let mut payload = json!({ "steps": step_results });
    if let Some(oracle) = &manifest.oracle {
        let want_append = oracle
            .get("append_equals_prefix")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let want_prefix = oracle
            .get("prefix_re_normalize")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let want_action = oracle
            .get("action_equals_snapshot")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        if want_append || want_prefix || want_action {
            let mut section = Map::new();
            if want_append || want_prefix {
                let oracle_state = create_stream(stream_options_from_manifest(manifest)?);
                let rev = state
                    .cursor
                    .source_revision
                    .clone()
                    .unwrap_or_else(|| "oracle".into());
                let (oracle_state, mut snap) =
                    apply_snapshot(&oracle_state, &state.committed_prefix, &rev, None)
                        .map_err(StreamEngineError::Fatal)?;
                // When the append path finished (stable→final), mirror finish so
                // oracle finality matches (LS-08 stable-to-final).
                if (snap.kind == "updated" || snap.kind == "unchanged") && state.finished {
                    let (_finished_state, finished_update) =
                        finish_stream(&oracle_state).map_err(StreamEngineError::Fatal)?;
                    snap = finished_update;
                }
                let ok = (snap.kind == "updated" || snap.kind == "unchanged")
                    && oracle_snapshots_match(
                        state.snapshot.as_ref(),
                        snap.snapshot.as_ref(),
                        &state.cursor,
                        &snap.cursor,
                    );
                if want_append {
                    section.insert("append_equals_prefix".into(), Value::Bool(ok));
                }
                if want_prefix {
                    section.insert("prefix_re_normalize".into(), Value::Bool(ok));
                }
            }
            if want_action {
                let material_name = oracle
                    .get("snapshot_material")
                    .and_then(Value::as_str)
                    .unwrap_or("step-snapshot.json");
                let snap_rev = oracle
                    .get("snapshot_source_revision")
                    .and_then(Value::as_str)
                    .unwrap_or("ahp-equiv-1");
                let ok = match load_step_bytes(
                    case_directory,
                    &json!({ "material": material_name }),
                ) {
                    Ok(material) => {
                        let snap_state = create_stream(stream_options_from_manifest(manifest)?);
                        match apply_ahp_snapshot(&snap_state, &material, snap_rev, None) {
                            Ok((_, snap)) => {
                                (snap.kind == "updated" || snap.kind == "unchanged")
                                    && action_snapshot_parity(
                                        state.snapshot.as_ref(),
                                        snap.snapshot.as_ref(),
                                    )
                            }
                            Err(_) => false,
                        }
                    }
                    Err(_) => false,
                };
                section.insert("action_equals_snapshot".into(), Value::Bool(ok));
            }
            payload
                .as_object_mut()
                .unwrap()
                .insert("oracle".into(), Value::Object(section));
        }
    }
    serde_json::to_string(&payload).map_err(|e| {
        StreamEngineError::Protocol(format!("Failed to serialize stream sequence: {e}"))
    })
}

fn action_snapshot_parity(
    action_snap: Option<&StreamSnapshot>,
    snapshot_snap: Option<&StreamSnapshot>,
) -> bool {
    match (action_snap, snapshot_snap) {
        (None, None) => true,
        (Some(a), Some(o)) => {
            if a.records.len() != o.records.len() {
                return false;
            }
            for (ar, or) in a.records.iter().zip(o.records.iter()) {
                let aid = ar.record.get("id").and_then(Value::as_str).unwrap_or("");
                let oid = or.record.get("id").and_then(Value::as_str).unwrap_or("");
                if aid != oid || ar.status != or.status {
                    return false;
                }
                let arole = ar.record.get("role").and_then(Value::as_str).unwrap_or("");
                let orole = or.record.get("role").and_then(Value::as_str).unwrap_or("");
                if arole == "meta" && orole == "meta" {
                    continue;
                }
                let acontent = ar.record.get("content");
                let ocontent = or.record.get("content");
                if arole != orole || acontent != ocontent {
                    return false;
                }
            }
            true
        }
        _ => false,
    }
}

fn oracle_snapshots_match(
    append_snap: Option<&StreamSnapshot>,
    oracle_snap: Option<&StreamSnapshot>,
    append_cursor: &StreamCursor,
    oracle_cursor: &StreamCursor,
) -> bool {
    // Missing snapshot (never updated — pure pending) ≡ empty incomplete snapshot.
    let a_len = append_snap.map(|s| s.records.len()).unwrap_or(0);
    let o_len = oracle_snap.map(|s| s.records.len()).unwrap_or(0);
    if a_len != o_len {
        return false;
    }
    if let (Some(a), Some(o)) = (append_snap, oracle_snap) {
        for (ar, or) in a.records.iter().zip(o.records.iter()) {
            let aid = ar.record.get("id").and_then(Value::as_str).unwrap_or("");
            let oid = or.record.get("id").and_then(Value::as_str).unwrap_or("");
            if aid != oid
                || ar.status != or.status
                || ar.provisional_id != or.provisional_id
                || ar.replaces_provisional_id != or.replaces_provisional_id
                || ar.finalizes_provisional_id != or.finalizes_provisional_id
            {
                return false;
            }
        }
        if a.diagnostics.len() != o.diagnostics.len() {
            return false;
        }
        for (ad, od) in a.diagnostics.iter().zip(o.diagnostics.iter()) {
            if ad.code != od.code
                || ad.message != od.message
                || ad.input_line != od.input_line
                || ad.record_index != od.record_index
                || ad.count != od.count
            {
                return false;
            }
        }
    } else if a_len != 0 || o_len != 0 {
        return false;
    } else {
        // Both empty (one or both missing): diagnostics must also be empty.
        let a_d = append_snap.map(|s| s.diagnostics.len()).unwrap_or(0);
        let o_d = oracle_snap.map(|s| s.diagnostics.len()).unwrap_or(0);
        if a_d != 0 || o_d != 0 {
            return false;
        }
    }
    let a_complete = append_snap.map(|s| s.complete).unwrap_or(false);
    let o_complete = oracle_snap.map(|s| s.complete).unwrap_or(false);
    if a_complete != o_complete {
        return false;
    }
    append_cursor.position.next_byte_offset() == oracle_cursor.position.next_byte_offset()
        && append_cursor.prefix_sha256 == oracle_cursor.prefix_sha256
}

fn execute(
    repository_root: &Path,
    case_directory: &Path,
    manifest: &Manifest,
    operation: &str,
) -> Result<(String, Vec<hypabolic_trajectory::Diagnostic>), TrajectoryError> {
    if operation == "list-trajectories" {
        return execute_listing(repository_root, manifest).map(|value| (value, Vec::new()));
    }
    let transcript_name = manifest.transcript.as_deref().ok_or_else(|| {
        TrajectoryError::new(
            "invalid_input",
            "Case field 'transcript' must be a non-empty string.",
        )
    })?;
    let transcript_path = safe_join(case_directory, transcript_name)
        .map_err(|message| TrajectoryError::new("invalid_input", message))?;
    let transcript = fs::read(transcript_path)
        .map_err(|error| TrajectoryError::new("io_error", error.to_string()))?;
    let strategy = manifest
        .bounds
        .tool_results
        .as_ref()
        .and_then(|value| value.strategy.as_deref())
        .map(|value| match value {
            "head" => Ok(TruncationStrategy::Head),
            "head-tail" => Ok(TruncationStrategy::HeadTail),
            _ => Err(TrajectoryError::new(
                "invalid_input",
                "Unknown tool result truncation strategy.",
            )),
        })
        .transpose()?;
    let normalize_request = NormalizeRequest {
        transcript: &transcript,
        source_context: SourceContext {
            group_id: manifest.source_context.group_id.as_deref(),
            base_byte_offset: manifest.source_context.base_byte_offset.unwrap_or(0),
            partial: manifest.source_context.partial.unwrap_or(false),
            include_encrypted_reasoning: manifest
                .source_context
                .include_encrypted_reasoning
                .unwrap_or(false),
        },
        options: NormalizeOptions {
            tool_arguments_max_characters: manifest
                .bounds
                .tool_arguments
                .as_ref()
                .map(|value| value.max_characters),
            tool_results_max_characters: manifest
                .bounds
                .tool_results
                .as_ref()
                .map(|value| value.max_characters),
            tool_results_strategy: strategy,
            include_tool_results: manifest
                .filters
                .tool_results
                .as_deref()
                .map(|value| value != "omit"),
        },
    };
    let trajectory = match manifest.source.as_str() {
        "pi" => normalize_pi(normalize_request),
        "claude-code" => normalize_claude_code(normalize_request),
        "codex" => normalize_codex(normalize_request),
        "openclaw" => normalize_openclaw(normalize_request),
        "hermes" => normalize_hermes(normalize_request),
        "ahp" => normalize_ahp(normalize_request),
        "grok-build" => normalize_grok_build(normalize_request),
        _ => unreachable!("source is validated before execution"),
    }?;
    let output = match operation {
        "normalize-letta" => project_letta(&trajectory),
        "normalize-canonical" => project_canonical(&trajectory),
        "normalize-hypabolic" => project_hypabolic(&trajectory),
        "project-openai" => project_openai(&trajectory),
        "project-minimal-jsonl" => project_minimal_jsonl(&trajectory),
        "project-otel" => project_opentelemetry(&trajectory),
        _ => Err(TrajectoryError::new(
            "unknown_operation",
            format!("Rust ML7 does not support operation '{operation}'."),
        )),
    }?;
    Ok((output, trajectory.diagnostics))
}

fn execute_listing(repository_root: &Path, manifest: &Manifest) -> Result<String, TrajectoryError> {
    let store_name = manifest
        .store
        .as_deref()
        .ok_or_else(|| TrajectoryError::new("invalid_input", "Listing case requires a store."))?;
    let store_path = safe_join(
        &repository_root.join("conformance").join("stores"),
        &format!("{store_name}/store.json"),
    )
    .map_err(|message| TrajectoryError::new("invalid_input", message))?;
    let store: Store =
        read_json(&store_path).map_err(|message| TrajectoryError::new("invalid_input", message))?;
    let root = unique_temp_root();
    fs::create_dir_all(&root)
        .map_err(|error| TrajectoryError::new("io_error", error.to_string()))?;
    let outcome = (|| {
        for fixture in store.files {
            let destination = safe_join(&root, &fixture.path)
                .map_err(|message| TrajectoryError::new("invalid_input", message))?;
            if let Some(parent) = destination.parent() {
                fs::create_dir_all(parent)
                    .map_err(|error| TrajectoryError::new("io_error", error.to_string()))?;
            }
            fs::write(&destination, fixture.content)
                .map_err(|error| TrajectoryError::new("io_error", error.to_string()))?;
            if let Some(updated_at) = fixture.updated_at {
                set_modified_time(&destination, &updated_at)?;
            }
        }

        let listing = manifest.listing.as_ref();
        let limit = listing.and_then(|value| value.limit).unwrap_or(50);
        let all_pages = listing.and_then(|value| value.all_pages).unwrap_or(false);
        let mut pages = Vec::new();
        let mut cursor = None;
        loop {
            let listing_root = if matches!(
                manifest.source.as_str(),
                "claude-code" | "codex" | "grok-build"
            ) {
                root.join("store")
            } else {
                root.clone()
            };
            let options = ListingOptions {
                root: &listing_root,
                cursor: cursor.as_deref(),
                limit,
            };
            let page = match manifest.source.as_str() {
                "pi" => list_pi_trajectories(&options),
                "claude-code" => list_claude_code_trajectories(&options),
                "codex" => list_codex_trajectories(&options),
                "openclaw" => list_openclaw_trajectories(&options),
                "hermes" => list_hermes_trajectories(&options),
                "ahp" => list_ahp_trajectories(&options),
                "grok-build" => list_grok_build_trajectories(&options),
                _ => unreachable!("source is validated before execution"),
            }?;
            let items = page
                .items
                .iter()
                .map(|item| {
                    let relative = item.path.strip_prefix(&root).map_err(|_| {
                        TrajectoryError::new("invalid_input", "Listing escaped its explicit root.")
                    })?;
                    let mut obj = json!({
                        "id": item.id,
                        "path": format!("$ROOT/{}", relative.to_string_lossy().replace('\\', "/")),
                        "updated_at": item.updated_at,
                        "size_bytes": item.size_bytes,
                    });
                    if let Some(title) = &item.title {
                        obj.as_object_mut()
                            .expect("listing item object")
                            .insert("title".into(), json!(title));
                    }
                    Ok(obj)
                })
                .collect::<Result<Vec<_>, TrajectoryError>>()?;
            let next = page.next_cursor.clone();
            pages.push(json!({
                "items": items,
                "next_cursor": next,
            }));
            cursor = page.next_cursor;
            if !all_pages || cursor.is_none() {
                break;
            }
        }
        let output = if all_pages {
            Value::Array(pages)
        } else {
            pages.into_iter().next().expect("at least one page")
        };
        hypabolic_trajectory::serialize_projection(&output)
    })();
    let _ = fs::remove_dir_all(&root);
    outcome
}

fn read_request() -> Result<Request, String> {
    let mut text = String::new();
    let arguments = std::env::args_os().collect::<Vec<_>>();
    if arguments.len() == 2 {
        text = fs::read_to_string(&arguments[1]).map_err(|error| error.to_string())?;
    } else {
        io::stdin()
            .read_to_string(&mut text)
            .map_err(|error| error.to_string())?;
    }
    serde_json::from_str(&text).map_err(|error| error.to_string())
}

fn read_json<T: for<'de> Deserialize<'de>>(path: &Path) -> Result<T, String> {
    let text = fs::read_to_string(path).map_err(|error| error.to_string())?;
    serde_json::from_str(&text).map_err(|error| error.to_string())
}

fn safe_join(root: &Path, relative: &str) -> Result<PathBuf, String> {
    let path = Path::new(relative);
    if path.is_absolute()
        || path
            .components()
            .any(|part| !matches!(part, Component::Normal(_)))
    {
        return Err("Fixture path escapes its declared root.".into());
    }
    Ok(root.join(path))
}

fn unique_temp_root() -> PathBuf {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    std::env::temp_dir().join(format!(
        "trajectory-conformance-{}-{nonce}",
        std::process::id()
    ))
}

fn set_modified_time(path: &Path, timestamp: &str) -> Result<(), TrajectoryError> {
    let milliseconds = DateTime::parse_from_rfc3339(timestamp)
        .map_err(|_| TrajectoryError::new("invalid_input", "Store timestamp is invalid."))?
        .timestamp_millis();
    let milliseconds = u64::try_from(milliseconds)
        .map_err(|_| TrajectoryError::new("invalid_input", "Store timestamp is out of range."))?;
    let time = UNIX_EPOCH + Duration::from_millis(milliseconds);
    let file = File::options()
        .write(true)
        .open(path)
        .map_err(|error| TrajectoryError::new("io_error", error.to_string()))?;
    file.set_times(fs::FileTimes::new().set_accessed(time).set_modified(time))
        .map_err(|error| TrajectoryError::new("io_error", error.to_string()))
}
