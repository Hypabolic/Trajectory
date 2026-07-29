use std::collections::{BTreeMap, HashMap, HashSet};

use chrono::{DateTime, SecondsFormat, TimeZone as _, Utc};
use serde_json::{Map, Value};

use crate::canonical::{canonical_json, relaxed_json, utf16_compare};
use crate::model::{
    AppliedConfig, Bounds, Diagnostic, Filters, IrRecord, ModelInvocation, ModelTokenUsage,
    NormalizeRequest, Provenance, RecordHashes, RecordKind, Role, ToolCall, Trajectory,
    TrajectoryError, TrajectoryExecution, TrajectorySource, TruncationStrategy,
};
use crate::projection::{format_ms, record_type, sha256, to_letta_record};

const SYNTHETIC_BASE: i64 = 1_767_225_600_000;

/// Ecosystem-native source decoder boundary.
pub trait SourceAdapter {
    /// Adapter source name.
    fn source(&self) -> &'static str;

    /// Decodes and normalizes exact transcript bytes.
    fn normalize(&self, request: NormalizeRequest<'_>) -> Result<Trajectory, TrajectoryError>;
}

/// Native Pi JSONL source adapter.
#[derive(Debug, Default, Clone, Copy)]
pub struct PiSourceAdapter;

impl SourceAdapter for PiSourceAdapter {
    fn source(&self) -> &'static str {
        "pi"
    }

    fn normalize(&self, request: NormalizeRequest<'_>) -> Result<Trajectory, TrajectoryError> {
        normalize_pi(request)
    }
}

/// Native `OpenClaw` JSONL source adapter (Pi-family with delivery-mirror masking).
#[derive(Debug, Default, Clone, Copy)]
pub struct OpenClawSourceAdapter;

impl SourceAdapter for OpenClawSourceAdapter {
    fn source(&self) -> &'static str {
        "openclaw"
    }

    fn normalize(&self, request: NormalizeRequest<'_>) -> Result<Trajectory, TrajectoryError> {
        normalize_openclaw(request)
    }
}

/// Native Claude Code JSONL source adapter.
#[derive(Debug, Default, Clone, Copy)]
pub struct ClaudeCodeSourceAdapter;

impl SourceAdapter for ClaudeCodeSourceAdapter {
    fn source(&self) -> &'static str {
        "claude-code"
    }

    fn normalize(&self, request: NormalizeRequest<'_>) -> Result<Trajectory, TrajectoryError> {
        normalize_claude_code(request)
    }
}

/// Native Codex rollout JSONL source adapter.
#[derive(Debug, Default, Clone, Copy)]
pub struct CodexSourceAdapter;

impl SourceAdapter for CodexSourceAdapter {
    fn source(&self) -> &'static str {
        "codex"
    }

    fn normalize(&self, request: NormalizeRequest<'_>) -> Result<Trajectory, TrajectoryError> {
        normalize_codex(request)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
enum EventKind {
    Message,
    Reasoning,
    ToolCall,
    ToolResult,
}

#[derive(Debug, Clone)]
struct DecodedEvent {
    kind: EventKind,
    role: Role,
    content: Option<String>,
    tool_call_id: Option<String>,
    tool_name: Option<String>,
    arguments_json: Option<String>,
    is_error: Option<bool>,
    native_id: Option<String>,
    producer_version: Option<String>,
    source_sequence: Option<i64>,
    source_offset: i64,
    input_line: Option<usize>,
    timestamp_ms: Option<i64>,
    timestamp_precise: Option<String>,
    component_index: usize,
    model: Option<String>,
}

struct DecodedSession {
    source: TrajectorySource,
    source_name: &'static str,
    group_id: Option<String>,
    cwd: Option<String>,
    git_branch: Option<String>,
    model: Option<String>,
    producer_version: Option<String>,
    created_at_ms: Option<i64>,
    events: Vec<DecodedEvent>,
    model_invocations: Vec<DecodedModelInvocation>,
    diagnostics: Vec<Diagnostic>,
}

#[derive(Debug, Clone)]
struct DecodedModelInvocation {
    native_id: Option<String>,
    source_offset: Option<i64>,
    provider: Option<String>,
    api_family: Option<String>,
    requested_model: Option<String>,
    response_model: Option<String>,
    response_id: Option<String>,
    stop_reason: Option<String>,
    producer_version: Option<String>,
    usage: Option<ModelTokenUsage>,
    started_at_ms: Option<i64>,
    started_at_precise: Option<String>,
    completed_at_ms: Option<i64>,
    completed_at_precise: Option<String>,
}

#[derive(Debug, Clone)]
struct PlannedCall {
    source_id: String,
    final_id: String,
    synthesized: bool,
    renamed: bool,
    consumed: bool,
}

struct Plan {
    calls: HashMap<usize, PlannedCall>,
    open_calls: HashMap<String, Vec<PlannedCall>>,
    ordinals: Vec<usize>,
}

/// Normalizes a native Pi JSONL transcript into the private Rust IR.
pub fn normalize_pi(request: NormalizeRequest<'_>) -> Result<Trajectory, TrajectoryError> {
    let config = resolve_config(request)?;
    let decoded = decode_pi_session(request.transcript, PiFamilyOptions::pi())?;
    normalize_decoded(config, decoded)
}

/// Normalizes a native `OpenClaw` JSONL transcript into the private Rust IR.
pub fn normalize_openclaw(request: NormalizeRequest<'_>) -> Result<Trajectory, TrajectoryError> {
    let config = resolve_config(request)?;
    let decoded = decode_pi_session(request.transcript, PiFamilyOptions::openclaw())?;
    normalize_decoded(config, decoded)
}

/// Native Hermes message-row / session-envelope source adapter.
#[derive(Debug, Default, Clone, Copy)]
pub struct HermesSourceAdapter;

impl SourceAdapter for HermesSourceAdapter {
    fn source(&self) -> &'static str {
        "hermes"
    }

    fn normalize(&self, request: NormalizeRequest<'_>) -> Result<Trajectory, TrajectoryError> {
        normalize_hermes(request)
    }
}

/// Normalizes a Hermes session export (message-row array or session envelope).
pub fn normalize_hermes(request: NormalizeRequest<'_>) -> Result<Trajectory, TrajectoryError> {
    let config = resolve_config(request)?;
    let decoded = decode_hermes(request.transcript)?;
    normalize_decoded(config, decoded)
}

/// Native AHP Shape A snapshot source adapter.
#[derive(Debug, Default, Clone, Copy)]
pub struct AhpSourceAdapter;

impl SourceAdapter for AhpSourceAdapter {
    fn source(&self) -> &'static str {
        "ahp"
    }

    fn normalize(&self, request: NormalizeRequest<'_>) -> Result<Trajectory, TrajectoryError> {
        normalize_ahp(request)
    }
}

/// Normalizes an AHP Shape A chat snapshot export.
pub fn normalize_ahp(request: NormalizeRequest<'_>) -> Result<Trajectory, TrajectoryError> {
    let config = resolve_config(request)?;
    let partial = config.partial || config.base_byte_offset > 0;
    let decoded = decode_ahp(request.transcript, partial)?;
    normalize_decoded(config, decoded)
}

/// Normalizes a native Claude Code JSONL transcript into the private Rust IR.
pub fn normalize_claude_code(request: NormalizeRequest<'_>) -> Result<Trajectory, TrajectoryError> {
    let config = resolve_config(request)?;
    let decoded = decode_claude_code(request.transcript)?;
    normalize_decoded(config, decoded)
}

/// Normalizes a native Codex rollout JSONL transcript into the private Rust IR.
pub fn normalize_codex(request: NormalizeRequest<'_>) -> Result<Trajectory, TrajectoryError> {
    let config = resolve_config(request)?;
    let decoded = decode_codex(request.transcript)?;
    normalize_decoded(config, decoded)
}

fn normalize_decoded(
    config: AppliedConfig,
    decoded: DecodedSession,
) -> Result<Trajectory, TrajectoryError> {
    let provided = config.source_group_id.as_deref();
    if let (Some(detected), Some(provided)) = (decoded.group_id.as_deref(), provided) {
        if detected != provided {
            return Err(TrajectoryError::new(
                "source_group_conflict",
                format!(
                    "Detected source group {} conflicts with the provided source context group {}.",
                    quote(detected),
                    quote(provided)
                ),
            ));
        }
    }
    let source_group_resolved = decoded.group_id.is_some() || config.source_group_id.is_some();
    let group_id = decoded
        .group_id
        .clone()
        .or_else(|| config.source_group_id.clone())
        .unwrap_or_else(|| "default".into());
    let partial = config.partial || config.base_byte_offset > 0;
    let mut diagnostics = decoded.diagnostics;
    let mut plan = plan_events(&decoded.events);
    let mut records = Vec::new();
    let mut anchors = BTreeMap::new();
    let mut model_counts: HashMap<String, usize> = HashMap::new();

    for (event_index, event) in decoded.events.iter().enumerate() {
        if let Some(model) = &event.model {
            *model_counts.entry(model.clone()).or_default() += 1;
        }
        let record_index = records.len() + 1;
        if let Some(record) = normalize_event(
            event,
            event_index,
            record_index,
            &group_id,
            &config,
            partial,
            &mut plan,
            &mut diagnostics,
        )? {
            if let Some(timestamp) = event.timestamp_ms {
                anchors.insert(records.len(), timestamp);
            }
            records.push(record);
        }
    }

    if !partial && !records.iter().any(|record| record.role == Role::User) {
        return Err(TrajectoryError::new(
            "missing_user_records",
            "Transcript did not contain any normalizable user records.",
        ));
    }
    if !partial && !records.iter().any(|record| record.role == Role::Assistant) {
        return Err(TrajectoryError::new(
            "missing_assistant_records",
            "Transcript did not contain any normalizable assistant records.",
        ));
    }

    let timestamps = fill_timestamps(
        records.len(),
        &anchors,
        decoded.created_at_ms,
        &mut diagnostics,
    )?;
    for (record, timestamp) in records.iter_mut().zip(timestamps) {
        record.timestamp_ms = Some(timestamp);
        record.hashes = hash_record(record)?;
    }

    let mut model_counts = model_counts.into_iter().collect::<Vec<_>>();
    model_counts.sort_by(|left, right| {
        right
            .1
            .cmp(&left.1)
            .then_with(|| utf16_compare(&left.0, &right.0))
    });
    let model = decoded
        .model
        .clone()
        .or_else(|| model_counts.first().map(|value| value.0.clone()));
    let execution = normalize_execution(
        &decoded.model_invocations,
        &group_id,
        config.base_byte_offset,
    )?;
    let meta = create_meta(
        &group_id,
        decoded.source_name,
        decoded.cwd,
        decoded.git_branch,
        model,
        decoded.producer_version.clone(),
    )?;
    records.insert(0, meta);
    Ok(Trajectory {
        source: decoded.source,
        source_name: decoded.source_name.into(),
        group_id,
        source_group_resolved,
        producer_version: decoded.producer_version,
        records,
        diagnostics,
        execution,
        config,
    })
}

#[derive(Debug, Clone, Copy)]
struct PiFamilyOptions {
    source: TrajectorySource,
    source_name: &'static str,
    source_label: &'static str,
    excluded_models: &'static [&'static str],
}

impl PiFamilyOptions {
    const fn pi() -> Self {
        Self {
            source: TrajectorySource::Pi,
            source_name: "pi",
            source_label: "Pi",
            excluded_models: &[],
        }
    }

    const fn openclaw() -> Self {
        Self {
            source: TrajectorySource::OpenClaw,
            source_name: "openclaw",
            source_label: "OpenClaw",
            excluded_models: &["delivery-mirror"],
        }
    }

    fn exclude_model(self, model: Option<&str>) -> Option<String> {
        model.and_then(|value| {
            if self.excluded_models.contains(&value) {
                None
            } else {
                Some(value.to_owned())
            }
        })
    }
}

fn decode_pi_session(
    bytes: &[u8],
    options: PiFamilyOptions,
) -> Result<DecodedSession, TrajectoryError> {
    let mut events = Vec::new();
    let mut diagnostics = Vec::new();
    let mut group_id = None;
    let mut cwd = None;
    let mut producer_version = None;
    let mut requested_provider = None;
    let mut requested_model = None;
    let mut created_at_ms = None;
    let mut model_invocations = Vec::new();
    let mut saw_message = false;
    let mut offset = 0_usize;
    let mut line = 1_usize;

    loop {
        let relative_end = bytes[offset..].iter().position(|value| *value == b'\n');
        let end = relative_end.map_or(bytes.len(), |value| offset + value);
        let line_end = if end > offset && bytes[end - 1] == b'\r' {
            end - 1
        } else {
            end
        };
        let slice = &bytes[offset..line_end];
        if !slice
            .iter()
            .all(|value| matches!(*value, b' ' | b'\t' | b'\r'))
        {
            let row = std::str::from_utf8(slice)
                .ok()
                .and_then(|text| serde_json::from_str::<Value>(text).ok());
            match row {
                Some(Value::Object(row)) => {
                    let row_type = string_value(row.get("type"));
                    if row_type == Some("session") {
                        if cwd.is_none() {
                            cwd = string_value(row.get("cwd")).map(str::to_owned);
                        }
                        if group_id.is_none() {
                            group_id = string_value(row.get("id")).map(str::to_owned);
                        }
                        if created_at_ms.is_none() {
                            created_at_ms = row
                                .get("timestamp")
                                .and_then(parse_timestamp)
                                .map(|value| value.0);
                        }
                        if producer_version.is_none() {
                            producer_version = scalar_string(row.get("version"));
                        }
                    } else if row_type == Some("model_change") {
                        requested_provider = string_value(row.get("provider")).map(str::to_owned);
                        requested_model = string_value(row.get("modelId")).map(str::to_owned);
                    } else if row_type == Some("message") {
                        if let Some(Value::Object(message)) = row.get("message") {
                            saw_message = true;
                            let source_offset = i64::try_from(offset).map_err(|_| {
                                TrajectoryError::new(
                                    "invalid_input",
                                    "Transcript byte offset exceeds signed 64-bit range.",
                                )
                            })?;
                            if string_value(message.get("role")) == Some("assistant") {
                                model_invocations.push(decode_pi_invocation(
                                    &row,
                                    message,
                                    source_offset,
                                    requested_provider.as_deref(),
                                    requested_model.as_deref(),
                                    options,
                                ));
                            }
                            decode_message(
                                &row,
                                message,
                                line,
                                source_offset,
                                options,
                                &mut events,
                            )?;
                        }
                    }
                }
                Some(_) => diagnostics.push(Diagnostic {
                    code: "non_object_json_line".into(),
                    message: format!("Skipped non-object JSON on line {line}."),
                    input_line: Some(line),
                    record_index: None,
                    count: None,
                }),
                None => diagnostics.push(Diagnostic {
                    code: "invalid_json_line".into(),
                    message: format!("Skipped invalid JSON on line {line}."),
                    input_line: Some(line),
                    record_index: None,
                    count: None,
                }),
            }
        }
        if end == bytes.len() {
            break;
        }
        offset = end + 1;
        line += 1;
    }

    if !saw_message && group_id.is_none() {
        return Err(TrajectoryError::new(
            "invalid_input",
            format!(
                "{} transcript must be session JSONL containing a session header or message entries.",
                options.source_label
            ),
        ));
    }
    Ok(DecodedSession {
        source: options.source,
        source_name: options.source_name,
        group_id,
        cwd,
        git_branch: None,
        model: None,
        producer_version,
        created_at_ms,
        events,
        model_invocations,
        diagnostics,
    })
}

fn decode_pi_invocation(
    row: &Map<String, Value>,
    message: &Map<String, Value>,
    source_offset: i64,
    requested_provider: Option<&str>,
    requested_model: Option<&str>,
    options: PiFamilyOptions,
) -> DecodedModelInvocation {
    let usage = message
        .get("usage")
        .and_then(Value::as_object)
        .map(|usage| ModelTokenUsage {
            input_tokens: integer_alias(usage, &["input"]),
            output_tokens: integer_alias(usage, &["output"]),
            cache_read_tokens: integer_alias(usage, &["cacheRead"]),
            cache_write_tokens: integer_alias(usage, &["cacheWrite"]),
            total_tokens: integer_alias(usage, &["totalTokens"]),
        });
    let started = message
        .get("startTimestamp")
        .or_else(|| message.get("requestTimestamp"))
        .and_then(parse_timestamp);
    let completed = message
        .get("timestamp")
        .and_then(parse_timestamp)
        .or_else(|| row.get("timestamp").and_then(parse_timestamp));
    DecodedModelInvocation {
        native_id: string_value(row.get("id")).map(str::to_owned),
        source_offset: Some(source_offset),
        provider: string_value(message.get("provider"))
            .or(requested_provider)
            .map(str::to_owned),
        api_family: string_value(message.get("api")).map(str::to_owned),
        requested_model: requested_model.map(str::to_owned),
        response_model: options.exclude_model(string_value(message.get("model"))),
        response_id: string_value(message.get("responseId")).map(str::to_owned),
        stop_reason: string_value(message.get("stopReason")).map(str::to_owned),
        producer_version: None,
        usage,
        started_at_ms: started.as_ref().map(|value| value.0),
        started_at_precise: started.map(|value| value.1),
        completed_at_ms: completed.as_ref().map(|value| value.0),
        completed_at_precise: completed.map(|value| value.1),
    }
}

#[derive(Debug)]
struct ContextCandidate {
    value: String,
    timestamp: i64,
    tie: String,
}
fn decode_claude_code(bytes: &[u8]) -> Result<DecodedSession, TrajectoryError> {
    const TRANSPORT_TYPES: &[&str] = &[
        "progress",
        "queue-operation",
        "file-history-snapshot",
        "summary",
        "system",
        "pr-link",
        "last-prompt",
        "custom-title",
        "ai-title",
        "agent-name",
        "permission-mode",
        "attachment",
        "mode",
    ];

    let mut events = Vec::new();
    let mut model_invocations = Vec::new();
    let mut diagnostics = Vec::new();
    let mut session_ids = HashSet::new();
    let mut cwd = None;
    let mut git_branch = None;
    let mut producer_version = None;
    let mut offset = 0_usize;
    let mut line = 1_usize;

    loop {
        let relative_end = bytes[offset..].iter().position(|value| *value == b'\n');
        let end = relative_end.map_or(bytes.len(), |value| offset + value);
        let line_end = if end > offset && bytes[end - 1] == b'\r' {
            end - 1
        } else {
            end
        };
        let slice = &bytes[offset..line_end];
        if !slice
            .iter()
            .all(|value| matches!(*value, b' ' | b'\t' | b'\r'))
        {
            let parsed = std::str::from_utf8(slice)
                .ok()
                .and_then(|text| serde_json::from_str::<Value>(text).ok());
            match parsed {
                Some(Value::Object(row)) => {
                    let row_type = string_value(row.get("type"));
                    if row.get("isSidechain") == Some(&Value::Bool(true)) {
                        diagnostics.push(Diagnostic {
                            code: "sidechain_record_dropped".into(),
                            message: format!(
                                "Dropped a Claude Code sidechain record on line {line}."
                            ),
                            input_line: Some(line),
                            record_index: None,
                            count: None,
                        });
                    } else if row_type.is_some_and(|value| TRANSPORT_TYPES.contains(&value)) {
                        // Transport records are deliberately outside the semantic transcript.
                    } else {
                        let timestamp = row.get("timestamp").and_then(parse_timestamp);
                        let native_id = string_value(row.get("uuid")).map(str::to_owned);
                        let context_timestamp =
                            timestamp.as_ref().map_or(i64::MAX, |value| value.0);
                        let context_tie = native_id.clone().unwrap_or_else(|| format!("@{offset}"));
                        select_earlier(
                            &mut cwd,
                            string_value(row.get("cwd")),
                            context_timestamp,
                            &context_tie,
                        );
                        select_earlier(
                            &mut git_branch,
                            string_value(row.get("gitBranch")),
                            context_timestamp,
                            &context_tie,
                        );
                        let version = scalar_string(row.get("version"));
                        select_earlier(
                            &mut producer_version,
                            version.as_deref(),
                            context_timestamp,
                            &context_tie,
                        );
                        if let Some(session_id) =
                            string_value(row.get("sessionId")).filter(|value| !value.is_empty())
                        {
                            session_ids.insert(session_id.to_owned());
                        }

                        if matches!(row_type, Some("user" | "assistant")) {
                            if let Some(Value::Object(message)) = row.get("message") {
                                let source_offset = i64::try_from(offset).map_err(|_| {
                                    TrajectoryError::new(
                                        "invalid_input",
                                        "Transcript byte offset exceeds signed 64-bit range.",
                                    )
                                })?;
                                if row_type == Some("assistant") {
                                    model_invocations.push(decode_claude_invocation(
                                        &row,
                                        message,
                                        source_offset,
                                        timestamp.as_ref(),
                                    ));
                                }
                                decode_claude_message(
                                    row_type.expect("semantic row type"),
                                    &row,
                                    message,
                                    line,
                                    source_offset,
                                    timestamp.as_ref(),
                                    &mut events,
                                    &mut diagnostics,
                                )?;
                            }
                        } else if let Some(row_type) = row_type.filter(|value| !value.is_empty()) {
                            let _ = row_type;
                            diagnostics.push(Diagnostic {
                                code: "unknown_semantic_record".into(),
                                message: format!(
                                    "Skipped an unknown Claude Code semantic record on line {line}."
                                ),
                                input_line: Some(line),
                                record_index: None,
                                count: None,
                            });
                        }
                    }
                }
                Some(_) => diagnostics.push(Diagnostic {
                    code: "non_object_json_line".into(),
                    message: format!("Skipped non-object JSON on line {line}."),
                    input_line: Some(line),
                    record_index: None,
                    count: None,
                }),
                None => diagnostics.push(Diagnostic {
                    code: "invalid_json_line".into(),
                    message: format!("Skipped invalid JSON on line {line}."),
                    input_line: Some(line),
                    record_index: None,
                    count: None,
                }),
            }
        }
        if end == bytes.len() {
            break;
        }
        offset = end + 1;
        line += 1;
    }

    if session_ids.len() > 1 {
        let mut ids = session_ids.into_iter().collect::<Vec<_>>();
        ids.sort_by(|left, right| utf16_compare(left, right));
        return Err(TrajectoryError::new(
            "source_group_conflict",
            format!(
                "Claude Code transcript contains multiple session ids: {}.",
                ids.iter()
                    .map(|value| quote(value))
                    .collect::<Vec<_>>()
                    .join(", ")
            ),
        ));
    }

    Ok(DecodedSession {
        source: TrajectorySource::ClaudeCode,
        source_name: "claude-code",
        group_id: session_ids.into_iter().next(),
        cwd: cwd.map(|value: ContextCandidate| value.value),
        git_branch: git_branch.map(|value: ContextCandidate| value.value),
        model: None,
        producer_version: Some(
            producer_version
                .map_or_else(|| "unknown".into(), |value: ContextCandidate| value.value),
        ),
        created_at_ms: None,
        events,
        model_invocations,
        diagnostics,
    })
}

fn decode_claude_invocation(
    row: &Map<String, Value>,
    message: &Map<String, Value>,
    source_offset: i64,
    timestamp: Option<&(i64, String)>,
) -> DecodedModelInvocation {
    let usage = message
        .get("usage")
        .and_then(Value::as_object)
        .map(|usage| ModelTokenUsage {
            input_tokens: integer_alias(usage, &["input_tokens", "input"]),
            output_tokens: integer_alias(usage, &["output_tokens", "output"]),
            cache_read_tokens: integer_alias(usage, &["cache_read_input_tokens", "cacheRead"]),
            cache_write_tokens: integer_alias(
                usage,
                &["cache_creation_input_tokens", "cacheWrite"],
            ),
            total_tokens: integer_alias(usage, &["total_tokens"]),
        });
    DecodedModelInvocation {
        native_id: string_value(row.get("uuid")).map(str::to_owned),
        source_offset: Some(source_offset),
        provider: Some("anthropic".into()),
        api_family: None,
        requested_model: None,
        response_model: string_value(message.get("model")).map(str::to_owned),
        response_id: string_value(message.get("id")).map(str::to_owned),
        stop_reason: string_value(message.get("stop_reason"))
            .or_else(|| string_value(message.get("stopReason")))
            .map(str::to_owned),
        producer_version: scalar_string(row.get("version")),
        usage,
        started_at_ms: None,
        started_at_precise: None,
        completed_at_ms: timestamp.map(|value| value.0),
        completed_at_precise: timestamp.map(|value| value.1.clone()),
    }
}

fn integer_alias(object: &Map<String, Value>, names: &[&str]) -> Option<i64> {
    names
        .iter()
        .find_map(|name| object.get(*name).and_then(Value::as_i64))
}

fn decode_codex(bytes: &[u8]) -> Result<DecodedSession, TrajectoryError> {
    const INJECTED_PREFIXES: &[&str] = &[
        "<environment_context>",
        "<user_instructions>",
        "<permissions instructions>",
        "<turn_context>",
    ];

    let mut events = Vec::new();
    let mut diagnostics = Vec::new();
    let mut group_id = None;
    let mut cwd = None;
    let mut git_branch = None;
    let mut model = None;
    let mut producer_version = None;
    let mut created_at_ms = None;
    let mut offset = 0_usize;
    let mut line = 1_usize;

    loop {
        let relative_end = bytes[offset..].iter().position(|value| *value == b'\n');
        let end = relative_end.map_or(bytes.len(), |value| offset + value);
        let line_end = if end > offset && bytes[end - 1] == b'\r' {
            end - 1
        } else {
            end
        };
        let slice = &bytes[offset..line_end];
        if !slice
            .iter()
            .all(|value| matches!(*value, b' ' | b'\t' | b'\r'))
        {
            let parsed = std::str::from_utf8(slice)
                .ok()
                .and_then(|text| serde_json::from_str::<Value>(text).ok());
            match parsed {
                Some(Value::Object(row)) => {
                    let record_type = string_value(row.get("type"));
                    let timestamp = row.get("timestamp").and_then(parse_timestamp);
                    let payload = row.get("payload").and_then(Value::as_object);
                    let payload_type = payload.and_then(|value| string_value(value.get("type")));

                    if record_type == Some("session_meta") {
                        if let Some(payload) = payload {
                            if cwd.is_none() {
                                cwd = non_empty(string_value(payload.get("cwd")));
                            }
                            if group_id.is_none() {
                                group_id = non_empty(string_value(payload.get("id")));
                            }
                            if producer_version.is_none() {
                                producer_version =
                                    non_empty_owned(scalar_string(payload.get("cli_version")));
                            }
                            if created_at_ms.is_none() {
                                created_at_ms = payload
                                    .get("timestamp")
                                    .and_then(parse_timestamp)
                                    .or(timestamp.clone())
                                    .map(|value| value.0);
                            }
                            if git_branch.is_none() {
                                git_branch = payload
                                    .get("git")
                                    .and_then(Value::as_object)
                                    .and_then(|git| non_empty(string_value(git.get("branch"))));
                            }
                        }
                    } else if record_type == Some("turn_context") {
                        if let Some(payload) = payload {
                            if cwd.is_none() {
                                cwd = non_empty(string_value(payload.get("cwd")));
                            }
                            if model.is_none() {
                                model = non_empty(string_value(payload.get("model")));
                            }
                        }
                    } else {
                        let source_offset = i64::try_from(offset).map_err(|_| {
                            TrajectoryError::new(
                                "invalid_input",
                                "Transcript byte offset exceeds signed 64-bit range.",
                            )
                        })?;
                        let mut emit =
                            |kind: EventKind,
                             role: Role,
                             content: Option<String>,
                             tool_call_id: Option<String>,
                             tool_name: Option<String>,
                             arguments_json: Option<String>,
                             is_error: Option<bool>| {
                                events.push(DecodedEvent {
                                    kind,
                                    role,
                                    content,
                                    tool_call_id,
                                    tool_name,
                                    arguments_json,
                                    is_error,
                                    native_id: None,
                                    producer_version: producer_version.clone(),
                                    source_sequence: None,
                                    source_offset,
                                    input_line: Some(line),
                                    timestamp_ms: timestamp.as_ref().map(|value| value.0),
                                    timestamp_precise: timestamp
                                        .as_ref()
                                        .map(|value| value.1.clone()),
                                    component_index: 0,
                                    model: model.clone(),
                                });
                            };

                        if record_type == Some("event_msg")
                            && payload_type == Some("agent_reasoning")
                        {
                            if let Some(content) = payload
                                .and_then(|value| string_value(value.get("text")))
                                .filter(|value| !value.trim().is_empty())
                            {
                                emit(
                                    EventKind::Reasoning,
                                    Role::Reasoning,
                                    Some(content.into()),
                                    None,
                                    None,
                                    None,
                                    None,
                                );
                            }
                        } else if record_type == Some("response_item") {
                            let empty_payload = Map::new();
                            let payload = payload.unwrap_or(&empty_payload);
                            match payload_type {
                                Some("message") => {
                                    let role = string_value(payload.get("role"));
                                    let content = read_blocks_text(payload.get("content"));
                                    if role == Some("user")
                                        && INJECTED_PREFIXES
                                            .iter()
                                            .any(|prefix| content.trim_start().starts_with(prefix))
                                    {
                                        diagnostics.push(Diagnostic {
                                            code: "injected_context_dropped".into(),
                                            message: format!(
                                                "Dropped Codex system-injected user content on line {line}."
                                            ),
                                            input_line: Some(line),
                                            record_index: None,
                                            count: None,
                                        });
                                    } else if matches!(role, Some("user" | "assistant")) {
                                        emit(
                                            EventKind::Message,
                                            if role == Some("user") {
                                                Role::User
                                            } else {
                                                Role::Assistant
                                            },
                                            Some(content),
                                            None,
                                            None,
                                            None,
                                            None,
                                        );
                                    }
                                }
                                Some("function_call") => emit(
                                    EventKind::ToolCall,
                                    Role::Assistant,
                                    None,
                                    string_value(payload.get("call_id")).map(str::to_owned),
                                    string_value(payload.get("name")).map(str::to_owned),
                                    Some(
                                        non_empty(string_value(payload.get("arguments")))
                                            .unwrap_or_else(|| "{}".into()),
                                    ),
                                    None,
                                ),
                                Some("custom_tool_call") => {
                                    let mut arguments = Map::new();
                                    arguments.insert(
                                        "input".into(),
                                        payload
                                            .get("input")
                                            .filter(|value| !value.is_null())
                                            .cloned()
                                            .unwrap_or_else(|| Value::String(String::new())),
                                    );
                                    emit(
                                        EventKind::ToolCall,
                                        Role::Assistant,
                                        None,
                                        string_value(payload.get("call_id")).map(str::to_owned),
                                        string_value(payload.get("name")).map(str::to_owned),
                                        Some(relaxed_json(&Value::Object(arguments))?),
                                        None,
                                    );
                                }
                                Some("web_search_call") => {
                                    let arguments = payload
                                        .iter()
                                        .filter(|(key, _)| {
                                            !matches!(key.as_str(), "type" | "call_id" | "status")
                                        })
                                        .map(|(key, value)| (key.clone(), value.clone()))
                                        .collect();
                                    emit(
                                        EventKind::ToolCall,
                                        Role::Assistant,
                                        None,
                                        string_value(payload.get("call_id")).map(str::to_owned),
                                        Some("web_search".into()),
                                        Some(relaxed_json(&Value::Object(arguments))?),
                                        None,
                                    );
                                }
                                Some("tool_search_call") => {
                                    let arguments = match payload.get("arguments") {
                                        Some(Value::String(value)) if !value.is_empty() => {
                                            value.clone()
                                        }
                                        Some(value) if !value.is_null() => relaxed_json(value)?,
                                        _ => "{}".into(),
                                    };
                                    emit(
                                        EventKind::ToolCall,
                                        Role::Assistant,
                                        None,
                                        string_value(payload.get("call_id")).map(str::to_owned),
                                        Some("tool_search".into()),
                                        Some(arguments),
                                        None,
                                    );
                                }
                                Some(
                                    "function_call_output"
                                    | "custom_tool_call_output"
                                    | "tool_search_output",
                                ) => {
                                    let content = if payload_type == Some("tool_search_output") {
                                        relaxed_json(
                                            payload
                                                .get("tools")
                                                .filter(|value| !value.is_null())
                                                .unwrap_or(&Value::Array(Vec::new())),
                                        )?
                                    } else {
                                        codex_output_text(payload.get("output"))?
                                    };
                                    emit(
                                        EventKind::ToolResult,
                                        Role::Tool,
                                        Some(content),
                                        string_value(payload.get("call_id")).map(str::to_owned),
                                        None,
                                        None,
                                        Some(false),
                                    );
                                }
                                _ => {}
                            }
                        }
                    }
                }
                Some(_) => diagnostics.push(Diagnostic {
                    code: "non_object_json_line".into(),
                    message: format!("Skipped non-object JSON on line {line}."),
                    input_line: Some(line),
                    record_index: None,
                    count: None,
                }),
                None => diagnostics.push(Diagnostic {
                    code: "invalid_json_line".into(),
                    message: format!("Skipped invalid JSON on line {line}."),
                    input_line: Some(line),
                    record_index: None,
                    count: None,
                }),
            }
        }
        if end == bytes.len() {
            break;
        }
        offset = end + 1;
        line += 1;
    }

    Ok(DecodedSession {
        source: TrajectorySource::Codex,
        source_name: "codex",
        group_id,
        cwd,
        git_branch,
        model: None,
        producer_version: Some(producer_version.unwrap_or_else(|| "unknown".into())),
        created_at_ms,
        events,
        model_invocations: Vec::new(),
        diagnostics,
    })
}

const HERMES_CONTENT_JSON_PREFIX: &str = "\u{0000}json:";

fn decode_hermes(bytes: &[u8]) -> Result<DecodedSession, TrajectoryError> {
    let mut diagnostics = Vec::new();
    let mut events = Vec::new();
    let parsed = parse_hermes_transcript(bytes)?;
    let mut rows: Vec<Value> = parsed
        .messages
        .into_iter()
        .filter(|row| !hermes_is_inactive(row))
        .collect();
    order_hermes_rows(&mut rows);
    let calls_by_row = plan_hermes_tool_calls(&rows, &mut diagnostics)?;

    for (index, row) in rows.iter().enumerate() {
        let timestamp_ms = hermes_timestamp(row.get("timestamp"));
        let native = hermes_row_id(row);
        let mut component_index = 0usize;
        let mut emit = |mut event: DecodedEvent| {
            event.component_index = component_index;
            component_index += 1;
            if let Some((text, numeric)) = &native {
                event.native_id = Some(text.clone());
                event.source_sequence = *numeric;
                event.source_offset = 0;
            } else {
                event.source_offset = i64::try_from(index).unwrap_or(0);
                event.source_sequence = Some(i64::try_from(index).unwrap_or(0));
            }
            events.push(event);
        };

        let role = string_value(row.get("role")).unwrap_or_default();
        if role == "user" {
            let content = hermes_content_text(row.get("content"))?;
            if !content.is_empty() {
                emit(DecodedEvent {
                    kind: EventKind::Message,
                    role: Role::User,
                    content: Some(content),
                    tool_call_id: None,
                    tool_name: None,
                    arguments_json: None,
                    is_error: None,
                    native_id: None,
                    producer_version: None,
                    source_sequence: None,
                    source_offset: 0,
                    input_line: None,
                    timestamp_ms,
                    timestamp_precise: None,
                    component_index: 0,
                    model: None,
                });
            }
            continue;
        }
        if role == "assistant" {
            let reasoning = hermes_reasoning_text(row);
            if !reasoning.is_empty() {
                emit(DecodedEvent {
                    kind: EventKind::Reasoning,
                    role: Role::Reasoning,
                    content: Some(reasoning),
                    tool_call_id: None,
                    tool_name: None,
                    arguments_json: None,
                    is_error: None,
                    native_id: None,
                    producer_version: None,
                    source_sequence: None,
                    source_offset: 0,
                    input_line: None,
                    timestamp_ms,
                    timestamp_precise: None,
                    component_index: 0,
                    model: None,
                });
            }
            let content = hermes_content_text(row.get("content"))?;
            if !content.is_empty() {
                emit(DecodedEvent {
                    kind: EventKind::Message,
                    role: Role::Assistant,
                    content: Some(content),
                    tool_call_id: None,
                    tool_name: None,
                    arguments_json: None,
                    is_error: None,
                    native_id: None,
                    producer_version: None,
                    source_sequence: None,
                    source_offset: 0,
                    input_line: None,
                    timestamp_ms,
                    timestamp_precise: None,
                    component_index: 0,
                    model: None,
                });
            }
            if let Some(calls) = calls_by_row.get(&index) {
                for call in calls {
                    emit(DecodedEvent {
                        kind: EventKind::ToolCall,
                        role: Role::Assistant,
                        content: None,
                        tool_call_id: call.id.clone(),
                        tool_name: call.name.clone(),
                        arguments_json: Some(call.args.clone()),
                        is_error: None,
                        native_id: None,
                        producer_version: None,
                        source_sequence: None,
                        source_offset: 0,
                        input_line: None,
                        timestamp_ms,
                        timestamp_precise: None,
                        component_index: 0,
                        model: None,
                    });
                }
            }
            continue;
        }
        if role == "tool" {
            emit(DecodedEvent {
                kind: EventKind::ToolResult,
                role: Role::Tool,
                content: Some(hermes_content_text(row.get("content"))?),
                tool_call_id: string_value(row.get("tool_call_id")).map(str::to_owned),
                tool_name: string_value(row.get("tool_name")).map(str::to_owned),
                arguments_json: None,
                is_error: None,
                native_id: None,
                producer_version: None,
                source_sequence: None,
                source_offset: 0,
                input_line: None,
                timestamp_ms,
                timestamp_precise: None,
                component_index: 0,
                model: None,
            });
        }
    }

    let session = parsed.session.as_ref();
    let model = session
        .and_then(|value| string_value(value.get("model")))
        .filter(|value| !value.is_empty())
        .map(str::to_owned);
    let cwd = session
        .and_then(|value| string_value(value.get("cwd")))
        .filter(|value| !value.is_empty())
        .map(str::to_owned);
    let created_at_ms = session.and_then(|value| hermes_timestamp(value.get("started_at")));
    let group_id = resolve_hermes_group_id(session, &parsed.raw_messages);

    Ok(DecodedSession {
        source: TrajectorySource::Hermes,
        source_name: "hermes",
        group_id,
        cwd,
        git_branch: None,
        model,
        producer_version: None,
        created_at_ms,
        events,
        model_invocations: Vec::new(),
        diagnostics,
    })
}

struct ParsedHermesTranscript {
    session: Option<Value>,
    messages: Vec<Value>,
    raw_messages: Vec<Value>,
}

fn parse_hermes_transcript(bytes: &[u8]) -> Result<ParsedHermesTranscript, TrajectoryError> {
    let parsed: Value = serde_json::from_slice(bytes).map_err(|_| invalid_hermes_transcript())?;
    if let Value::Array(items) = parsed {
        if !items.iter().all(Value::is_object) {
            return Err(invalid_hermes_transcript());
        }
        return Ok(ParsedHermesTranscript {
            session: None,
            messages: items.clone(),
            raw_messages: items,
        });
    }
    if let Value::Object(map) = &parsed {
        if let Some(Value::Array(items)) = map.get("messages") {
            if !items.iter().all(Value::is_object) {
                return Err(invalid_hermes_transcript());
            }
            let session = map
                .get("session")
                .filter(|value| value.is_object())
                .cloned();
            return Ok(ParsedHermesTranscript {
                session,
                messages: items.clone(),
                raw_messages: items.clone(),
            });
        }
    }
    Err(invalid_hermes_transcript())
}

fn order_hermes_rows(rows: &mut [Value]) {
    if !rows
        .iter()
        .all(|row| row.get("id").and_then(Value::as_i64).is_some())
    {
        return;
    }
    let mut indexed: Vec<(usize, Value)> = rows.iter().cloned().enumerate().collect();
    indexed.sort_by(|left, right| {
        let left_id = left.1.get("id").and_then(Value::as_i64).unwrap_or(0);
        let right_id = right.1.get("id").and_then(Value::as_i64).unwrap_or(0);
        left_id.cmp(&right_id).then(left.0.cmp(&right.0))
    });
    for (slot, (_, value)) in rows.iter_mut().zip(indexed) {
        *slot = value;
    }
}

#[derive(Debug, Clone)]
struct HermesToolCall {
    id: Option<String>,
    name: Option<String>,
    args: String,
}

fn plan_hermes_tool_calls(
    rows: &[Value],
    diagnostics: &mut Vec<Diagnostic>,
) -> Result<HashMap<usize, Vec<HermesToolCall>>, TrajectoryError> {
    let mut plan = HashMap::new();
    for (index, row) in rows.iter().enumerate() {
        if string_value(row.get("role")) != Some("assistant") {
            continue;
        }
        let mut calls = hermes_row_tool_calls(row, index, diagnostics)?;
        if calls.is_empty() {
            continue;
        }
        let idless_count = calls.iter().filter(|call| call.id.is_none()).count();
        if idless_count > 0 {
            let claimed: HashSet<String> =
                calls.iter().filter_map(|call| call.id.clone()).collect();
            let mut available = Vec::new();
            for next in rows.iter().skip(index + 1) {
                if string_value(next.get("role")) != Some("tool") {
                    break;
                }
                if let Some(tool_call_id) =
                    string_value(next.get("tool_call_id")).filter(|value| !value.is_empty())
                {
                    if !claimed.contains(tool_call_id) {
                        available.push(tool_call_id.to_owned());
                    }
                }
            }
            if available.len() == idless_count {
                let mut position = 0usize;
                for call in &mut calls {
                    if call.id.is_none() {
                        call.id = Some(available[position].clone());
                        position += 1;
                    }
                }
            }
        }
        plan.insert(index, calls);
    }
    Ok(plan)
}

fn hermes_row_tool_calls(
    row: &Value,
    index: usize,
    diagnostics: &mut Vec<Diagnostic>,
) -> Result<Vec<HermesToolCall>, TrajectoryError> {
    let Some(tool_calls) = row.get("tool_calls") else {
        return Ok(Vec::new());
    };
    let parsed = if let Value::String(text) = tool_calls {
        if text.is_empty() {
            return Ok(Vec::new());
        }
        if let Ok(value) = serde_json::from_str::<Value>(text) {
            value
        } else {
            diagnostics.push(Diagnostic {
                code: "invalid_json_line".into(),
                message: format!("Skipped undecodable tool_calls on message {}.", index + 1),
                input_line: Some(index + 1),
                record_index: None,
                count: None,
            });
            return Ok(Vec::new());
        }
    } else {
        tool_calls.clone()
    };
    let Value::Array(entries) = parsed else {
        return Ok(Vec::new());
    };
    let mut calls = Vec::new();
    for entry in entries {
        let Value::Object(map) = entry else {
            continue;
        };
        let function = map.get("function").and_then(|value| value.as_object());
        let name = first_string(
            function
                .and_then(|value| value.get("name"))
                .and_then(Value::as_str),
            map.get("name").and_then(Value::as_str),
        );
        let id = first_string(
            map.get("id").and_then(Value::as_str),
            map.get("call_id").and_then(Value::as_str),
        );
        let args_value = if let Some(function) = function {
            function.get("arguments")
        } else {
            map.get("arguments")
        };
        let args = match args_value {
            Some(Value::String(text)) if !text.is_empty() => text.clone(),
            Some(value) => relaxed_json(value)?,
            None => "{}".into(),
        };
        calls.push(HermesToolCall {
            id: id.map(str::to_owned),
            name: name.map(str::to_owned),
            args,
        });
    }
    Ok(calls)
}

fn hermes_content_text(value: Option<&Value>) -> Result<String, TrajectoryError> {
    match value {
        None | Some(Value::Null) => Ok(String::new()),
        Some(Value::String(text)) => {
            if let Some(encoded) = text.strip_prefix(HERMES_CONTENT_JSON_PREFIX) {
                match serde_json::from_str::<Value>(encoded) {
                    Ok(parsed) => hermes_content_text(Some(&parsed)),
                    Err(_) => Ok(encoded.to_owned()),
                }
            } else {
                Ok(text.clone())
            }
        }
        Some(Value::Array(items)) => Ok(blocks_text_from_array(items)),
        Some(Value::Object(_)) => relaxed_json(value.unwrap()),
        Some(other) => Ok(other.to_string()),
    }
}

fn blocks_text_from_array(items: &[Value]) -> String {
    let mut parts = Vec::new();
    for item in items {
        let Some(object) = item.as_object() else {
            continue;
        };
        let type_name = object.get("type").and_then(Value::as_str);
        if matches!(
            type_name,
            Some("text" | "input_text" | "output_text") | None
        ) {
            if let Some(text) = object
                .get("text")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty())
            {
                parts.push(text.to_owned());
            }
        } else if type_name == Some("image") {
            parts.push("[image]".into());
        }
    }
    parts.join("\n")
}

fn hermes_reasoning_text(row: &Value) -> String {
    if let Some(text) =
        string_value(row.get("reasoning_content")).filter(|value| !value.trim().is_empty())
    {
        return text.to_owned();
    }
    if let Some(text) = string_value(row.get("reasoning")).filter(|value| !value.trim().is_empty())
    {
        return text.to_owned();
    }
    String::new()
}

#[allow(clippy::cast_possible_truncation)]
fn hermes_timestamp(value: Option<&Value>) -> Option<i64> {
    if let Some(number) = value
        .and_then(Value::as_f64)
        .filter(|value| value.is_finite() && *value > 0.0)
    {
        let milliseconds = if number > 1e11 {
            number
        } else {
            number * 1_000.0
        };
        // Epoch seconds/millis for Hermes stay well within i64 after rounding.
        return Some(milliseconds.round() as i64);
    }
    value.and_then(|item| match item {
        Value::String(text) => DateTime::parse_from_rfc3339(text)
            .ok()
            .map(|value| value.timestamp_millis())
            .or_else(|| {
                // Accept values without zone by treating them as UTC.
                chrono::NaiveDateTime::parse_from_str(text, "%Y-%m-%dT%H:%M:%S%.f")
                    .ok()
                    .map(|value| value.and_utc().timestamp_millis())
            }),
        _ => None,
    })
}

fn hermes_row_id(row: &Value) -> Option<(String, Option<i64>)> {
    if let Some(number) = row.get("id").and_then(Value::as_i64) {
        return Some((number.to_string(), Some(number)));
    }
    if let Some(text) = string_value(row.get("id")).filter(|value| !value.is_empty()) {
        return Some((text.to_owned(), None));
    }
    None
}

fn resolve_hermes_group_id(session: Option<&Value>, messages: &[Value]) -> Option<String> {
    if let Some(id) = session
        .and_then(|value| string_value(value.get("id")))
        .filter(|value| !value.is_empty())
    {
        return Some(id.to_owned());
    }
    for row in messages {
        if let Some(id) = string_value(row.get("session_id")).filter(|value| !value.is_empty()) {
            return Some(id.to_owned());
        }
    }
    None
}

fn hermes_is_inactive(row: &Value) -> bool {
    match row.get("active") {
        Some(Value::Bool(false)) => true,
        Some(Value::Number(number)) => number.as_i64() == Some(0),
        _ => false,
    }
}

fn first_string<'a>(left: Option<&'a str>, right: Option<&'a str>) -> Option<&'a str> {
    left.filter(|value| !value.is_empty())
        .or_else(|| right.filter(|value| !value.is_empty()))
}

fn invalid_hermes_transcript() -> TrajectoryError {
    TrajectoryError::new(
        "invalid_input",
        "Hermes transcript must be a JSON array of session-store message rows or an object with a messages array.",
    )
}

fn decode_ahp(bytes: &[u8], partial: bool) -> Result<DecodedSession, TrajectoryError> {
    let mut diagnostics = Vec::new();
    let mut events = Vec::new();
    let mut model_invocations = Vec::new();
    let root: Value =
        serde_json::from_slice(bytes).map_err(|_| invalid_ahp_snapshot())?;
    let root_obj = root.as_object().ok_or_else(invalid_ahp_snapshot)?;

    validate_ahp_protocol_version(root_obj, &mut diagnostics)?;
    let chat = root_obj
        .get("chat")
        .and_then(Value::as_object)
        .ok_or_else(invalid_ahp_snapshot)?;
    let session = root_obj.get("session").and_then(Value::as_object);
    let provider = session
        .and_then(|session| string_value(session.get("provider")))
        .filter(|value| !value.is_empty())
        .map(str::to_owned);

    let group_id = string_value(chat.get("resource"))
        .filter(|value| !value.is_empty())
        .map(str::to_owned);
    let cwd = first_ahp_working_directory(chat, session);
    let mut model: Option<String> = None;
    let mut created_at_ms: Option<i64> = None;

    let turns = collect_ahp_turns(chat, partial, &mut diagnostics);
    for turn in turns {
        let Some(turn_obj) = turn.as_object() else {
            continue;
        };
        let turn_id = string_value(turn_obj.get("id"))
            .filter(|value| !value.is_empty())
            .map(str::to_owned);
        let timestamp_ms = ahp_timestamp(turn_obj.get("startedAt"));
        let timestamp_precise = string_value(turn_obj.get("startedAt"))
            .filter(|value| !value.is_empty())
            .map(str::to_owned);
        if created_at_ms.is_none() {
            created_at_ms = timestamp_ms;
        }
        // Turn-local model from this turn's Message.model — never stick a prior
        // turn's model onto later turns when usage.model is absent.
        let mut turn_model: Option<String> = None;
        let mut component_index = 0usize;
        let mut emit = |mut event: DecodedEvent| {
            event.component_index = component_index;
            component_index += 1;
            if event.native_id.is_some() {
                event.source_offset = 0;
                event.source_sequence = Some(0);
            } else {
                event.source_offset = i64::try_from(events.len()).unwrap_or(0);
                event.source_sequence = Some(i64::try_from(events.len()).unwrap_or(0));
            }
            events.push(event);
        };

        if let Some(message) = turn_obj.get("message").and_then(Value::as_object) {
            if let Some(message_model) = emit_ahp_message(
                message,
                turn_id.as_deref(),
                timestamp_ms,
                timestamp_precise.as_deref(),
                &mut emit,
                &mut diagnostics,
            ) {
                if model.is_none() {
                    model = Some(message_model.clone());
                }
                turn_model = Some(message_model);
            }
        }

        if let Some(parts) = turn_obj.get("responseParts").and_then(Value::as_array) {
            emit_ahp_response_parts(
                parts,
                turn_id.as_deref(),
                timestamp_ms,
                timestamp_precise.as_deref(),
                &mut emit,
                &mut diagnostics,
            )?;
        }

        if let Some(usage) = turn_obj.get("usage").and_then(Value::as_object) {
            let usage_model = string_value(usage.get("model"))
                .filter(|value| !value.is_empty())
                .map(str::to_owned);
            if model.is_none() {
                model.clone_from(&usage_model);
            }
            let resolved_model = usage_model.or_else(|| turn_model.clone());
            model_invocations.push(DecodedModelInvocation {
                native_id: turn_id.clone(),
                source_offset: None,
                provider: provider.clone(),
                api_family: None,
                requested_model: resolved_model.clone(),
                response_model: resolved_model,
                response_id: None,
                stop_reason: None,
                producer_version: None,
                usage: Some(ModelTokenUsage {
                    input_tokens: number_as_i64(usage.get("inputTokens")),
                    output_tokens: number_as_i64(usage.get("outputTokens")),
                    cache_read_tokens: number_as_i64(usage.get("cacheReadTokens")),
                    cache_write_tokens: None,
                    total_tokens: None,
                }),
                started_at_ms: timestamp_ms,
                started_at_precise: timestamp_precise.clone(),
                completed_at_ms: timestamp_ms,
                completed_at_precise: timestamp_precise.clone(),
            });
        }
    }

    Ok(DecodedSession {
        source: TrajectorySource::Ahp,
        source_name: "ahp",
        group_id,
        cwd,
        git_branch: None,
        model,
        producer_version: None,
        created_at_ms,
        events,
        model_invocations,
        diagnostics,
    })
}

fn validate_ahp_protocol_version(
    root: &Map<String, Value>,
    diagnostics: &mut Vec<Diagnostic>,
) -> Result<(), TrajectoryError> {
    match root.get("ahpProtocolVersion") {
        None | Some(Value::Null) => {
            diagnostics.push(Diagnostic {
                code: "ahp_version_missing".into(),
                message: "Snapshot lacks ahpProtocolVersion; assumed pinned 0.7.x.".into(),
                input_line: None,
                record_index: None,
                count: None,
            });
            Ok(())
        }
        Some(Value::String(version)) => {
            if is_compatible_ahp_version(version) {
                Ok(())
            } else {
                Err(TrajectoryError::new(
                    "invalid_input",
                    format!(
                        "Unsupported AHP protocol version '{version}'. Expected 0.7.x."
                    ),
                ))
            }
        }
        Some(_) => Err(TrajectoryError::new(
            "invalid_input",
            "AHP ahpProtocolVersion must be a string.",
        )),
    }
}

fn is_compatible_ahp_version(version: &str) -> bool {
    if version.is_empty() {
        return false;
    }
    let core = version.split('-').next().unwrap_or(version);
    let parts: Vec<&str> = core.split('.').collect();
    parts.len() >= 2
        && parts[0] == "0"
        && parts[1] == "7"
        && parts.iter().all(|part| !part.is_empty() && part.chars().all(|c| c.is_ascii_digit()))
}

fn collect_ahp_turns(
    chat: &Map<String, Value>,
    partial: bool,
    diagnostics: &mut Vec<Diagnostic>,
) -> Vec<Value> {
    let mut turns: Vec<(Value, Option<i64>, String)> = Vec::new();
    if let Some(array) = chat.get("turns").and_then(Value::as_array) {
        for turn in array {
            if !turn.is_object() {
                continue;
            }
            let id = string_value(turn.get("id")).unwrap_or("").to_owned();
            let started = ahp_timestamp(turn.get("startedAt"));
            turns.push((turn.clone(), started, id));
        }
    }
    // Nulls-last: missing startedAt after present timestamps, then UTF-8 id.
    turns.sort_by(|left, right| {
        match (left.1, right.1) {
            (Some(a), Some(b)) if a != b => a.cmp(&b),
            (Some(_), None) => std::cmp::Ordering::Less,
            (None, Some(_)) => std::cmp::Ordering::Greater,
            _ => compare_utf8(&left.2, &right.2),
        }
    });

    if let Some(active) = chat.get("activeTurn").filter(|value| value.is_object()) {
        if partial {
            // Partial mode: append open activeTurn after completed turns (§5.5).
            let id = string_value(active.get("id")).unwrap_or("").to_owned();
            let started = ahp_timestamp(active.get("startedAt"));
            turns.push((active.clone(), started, id));
        } else {
            diagnostics.push(Diagnostic {
                code: "ahp_active_turn_omitted".into(),
                message: "Omitted incomplete activeTurn (snapshot whole-mode policy).".into(),
                input_line: None,
                record_index: None,
                count: None,
            });
        }
    }

    turns.into_iter().map(|(value, _, _)| value).collect()
}

/// Emits the turn message and returns this message's model id when present
/// (turn-local; does not mutate session model).
fn emit_ahp_message(
    message: &Map<String, Value>,
    turn_id: Option<&str>,
    timestamp_ms: Option<i64>,
    timestamp_precise: Option<&str>,
    emit: &mut dyn FnMut(DecodedEvent),
    diagnostics: &mut Vec<Diagnostic>,
) -> Option<String> {
    let origin_kind = message
        .get("origin")
        .and_then(Value::as_object)
        .and_then(|origin| string_value(origin.get("kind")));
    let Some(origin_kind) = origin_kind else {
        diagnostics.push(Diagnostic {
            code: "ahp_unknown_message_origin".into(),
            message: "Dropped a message with an unknown origin kind.".into(),
            input_line: None,
            record_index: None,
            count: None,
        });
        return None;
    };
    if origin_kind == "tool" {
        return None;
    }

    let role = match origin_kind {
        "user" => Role::User,
        "agent" | "assistant" => Role::Assistant,
        "system" | "systemNotification" => {
            diagnostics.push(Diagnostic {
                code: "ahp_system_as_assistant".into(),
                message: "Mapped a system message origin to assistant.".into(),
                input_line: None,
                record_index: None,
                count: None,
            });
            Role::Assistant
        }
        _other => {
            // Fixed message only — do not echo free-form origin.kind (content-safety).
            diagnostics.push(Diagnostic {
                code: "ahp_unknown_message_origin".into(),
                message: "Dropped a message with an unknown origin kind.".into(),
                input_line: None,
                record_index: None,
                count: None,
            });
            return None;
        }
    };

    let turn_model = message
        .get("model")
        .and_then(Value::as_object)
        .and_then(|m| string_value(m.get("id")))
        .filter(|value| !value.is_empty())
        .map(str::to_owned);

    let text = string_value(message.get("text")).unwrap_or("").to_owned();
    if text.is_empty() {
        return turn_model;
    }

    emit(DecodedEvent {
        kind: EventKind::Message,
        role,
        content: Some(text),
        tool_call_id: None,
        tool_name: None,
        arguments_json: None,
        is_error: None,
        native_id: turn_id.map(str::to_owned),
        producer_version: None,
        source_sequence: None,
        source_offset: 0,
        input_line: None,
        timestamp_ms,
        timestamp_precise: timestamp_precise.map(str::to_owned),
        component_index: 0,
        model: turn_model.clone(),
    });
    turn_model
}

fn emit_ahp_response_parts(
    parts: &[Value],
    turn_id: Option<&str>,
    timestamp_ms: Option<i64>,
    timestamp_precise: Option<&str>,
    emit: &mut dyn FnMut(DecodedEvent),
    diagnostics: &mut Vec<Diagnostic>,
) -> Result<(), TrajectoryError> {
    let mut markdown_buffer: Vec<(String, String)> = Vec::new();
    let flush_markdown = |buffer: &mut Vec<(String, String)>, emit: &mut dyn FnMut(DecodedEvent)| {
        if buffer.is_empty() {
            return;
        }
        let content: String = buffer.iter().map(|(_, content)| content.as_str()).collect();
        let native_id = if buffer[0].0.is_empty() {
            turn_id.map(str::to_owned)
        } else {
            Some(buffer[0].0.clone())
        };
        buffer.clear();
        if content.is_empty() {
            return;
        }
        emit(DecodedEvent {
            kind: EventKind::Message,
            role: Role::Assistant,
            content: Some(content),
            tool_call_id: None,
            tool_name: None,
            arguments_json: None,
            is_error: None,
            native_id,
            producer_version: None,
            source_sequence: None,
            source_offset: 0,
            input_line: None,
            timestamp_ms,
            timestamp_precise: timestamp_precise.map(str::to_owned),
            component_index: 0,
            model: None,
        });
    };

    for part in parts {
        let Some(part_obj) = part.as_object() else {
            continue;
        };
        let kind = string_value(part_obj.get("kind")).unwrap_or("");
        if kind == "markdown" {
            let id = string_value(part_obj.get("id"))
                .or_else(|| string_value(part_obj.get("partId")))
                .unwrap_or("")
                .to_owned();
            let content = string_value(part_obj.get("content"))
                .unwrap_or("")
                .to_owned();
            markdown_buffer.push((id, content));
            continue;
        }
        flush_markdown(&mut markdown_buffer, emit);

        if kind == "reasoning" {
            let content = string_value(part_obj.get("content"))
                .unwrap_or("")
                .to_owned();
            if !content.trim().is_empty() {
                let id = string_value(part_obj.get("id"))
                    .or_else(|| string_value(part_obj.get("partId")))
                    .filter(|value| !value.is_empty())
                    .map(str::to_owned)
                    .or_else(|| turn_id.map(str::to_owned));
                emit(DecodedEvent {
                    kind: EventKind::Reasoning,
                    role: Role::Reasoning,
                    content: Some(content),
                    tool_call_id: None,
                    tool_name: None,
                    arguments_json: None,
                    is_error: None,
                    native_id: id,
                    producer_version: None,
                    source_sequence: None,
                    source_offset: 0,
                    input_line: None,
                    timestamp_ms,
                    timestamp_precise: timestamp_precise.map(str::to_owned),
                    component_index: 0,
                    model: None,
                });
            }
            continue;
        }

        if kind == "toolCall" {
            emit_ahp_tool_call(part_obj, timestamp_ms, timestamp_precise, emit)?;
            continue;
        }

        if kind == "inputRequest" {
            diagnostics.push(Diagnostic {
                code: "ahp_input_request_skipped".into(),
                message: "Skipped an inputRequest response part.".into(),
                input_line: None,
                record_index: None,
                count: None,
            });
            continue;
        }

        if kind == "resource" {
            // v1 does not fetch content-by-reference resource bodies.
            diagnostics.push(Diagnostic {
                code: "ahp_unresolved_content_ref".into(),
                message: "Dropped a resource response part without fetching content-by-reference."
                    .into(),
                input_line: None,
                record_index: None,
                count: None,
            });
            continue;
        }
        // systemNotification and unknown kinds: non-identity meta; ignore body for v1.
    }
    flush_markdown(&mut markdown_buffer, emit);
    Ok(())
}

fn emit_ahp_tool_call(
    part: &Map<String, Value>,
    timestamp_ms: Option<i64>,
    timestamp_precise: Option<&str>,
    emit: &mut dyn FnMut(DecodedEvent),
) -> Result<(), TrajectoryError> {
    let Some(tool_call) = part.get("toolCall").and_then(Value::as_object) else {
        return Ok(());
    };
    let tool_call_id = string_value(tool_call.get("toolCallId"))
        .filter(|value| !value.is_empty())
        .map(str::to_owned);
    let tool_name = string_value(tool_call.get("toolName"))
        .filter(|value| !value.is_empty())
        .map(str::to_owned);
    let arguments_json = ahp_tool_arguments_json(tool_call)?;

    emit(DecodedEvent {
        kind: EventKind::ToolCall,
        role: Role::Assistant,
        content: None,
        tool_call_id: tool_call_id.clone(),
        tool_name: tool_name.clone(),
        arguments_json,
        is_error: None,
        native_id: tool_call_id.clone(),
        producer_version: None,
        source_sequence: None,
        source_offset: 0,
        input_line: None,
        timestamp_ms,
        timestamp_precise: timestamp_precise.map(str::to_owned),
        component_index: 0,
        model: None,
    });

    let status = string_value(tool_call.get("status"));
    let success = match tool_call.get("success") {
        Some(Value::Bool(value)) => Some(*value),
        _ => None,
    };
    let is_terminal = matches!(
        status,
        Some("completed" | "cancelled" | "denied" | "error")
    );
    if !is_terminal && success.is_none() {
        return Ok(());
    }
    let is_error = success == Some(false)
        || matches!(status, Some("cancelled" | "denied" | "error"));
    let content = ahp_tool_result_content(tool_call, is_error)?;
    emit(DecodedEvent {
        kind: EventKind::ToolResult,
        role: Role::Tool,
        content: Some(content),
        tool_call_id,
        tool_name,
        arguments_json: None,
        is_error: Some(is_error),
        native_id: tool_call
            .get("toolCallId")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .map(str::to_owned),
        producer_version: None,
        source_sequence: None,
        source_offset: 0,
        input_line: None,
        timestamp_ms,
        timestamp_precise: timestamp_precise.map(str::to_owned),
        component_index: 0,
        model: None,
    });
    Ok(())
}

fn ahp_tool_arguments_json(
    tool_call: &Map<String, Value>,
) -> Result<Option<String>, TrajectoryError> {
    if let Some(parameters) = tool_call.get("parameters") {
        if parameters.is_object() || parameters.is_array() {
            return Ok(Some(relaxed_json(parameters)?));
        }
    }
    if let Some(input) = string_value(tool_call.get("toolInput")).filter(|value| !value.is_empty())
    {
        return Ok(Some(input.to_owned()));
    }
    // Omit when source has neither parameters nor toolInput; normalizer defaults empty object.
    Ok(None)
}

fn ahp_tool_result_content(
    tool_call: &Map<String, Value>,
    is_error: bool,
) -> Result<String, TrajectoryError> {
    if let Some(content) = tool_call.get("content").and_then(Value::as_array) {
        let mut parts = Vec::new();
        for block in content {
            let Some(block_obj) = block.as_object() else {
                continue;
            };
            let type_name = string_value(block_obj.get("type"));
            if matches!(type_name, Some("text") | None) {
                if let Some(text) =
                    string_value(block_obj.get("text")).filter(|value| !value.is_empty())
                {
                    parts.push(text.to_owned());
                }
            }
        }
        if !parts.is_empty() {
            return Ok(parts.join("\n"));
        }
    }
    if let Some(structured) = tool_call.get("structuredContent") {
        if !structured.is_null() {
            // Canonical JSON so .NET/TS/Rust structuredContent strings match cross-runtime.
            return canonical_json(structured);
        }
    }
    if let Some(past) = ahp_string_or_markdown(tool_call.get("pastTenseMessage")) {
        return Ok(past);
    }
    if is_error {
        if let Some(reason_message) = ahp_string_or_markdown(tool_call.get("reasonMessage")) {
            return Ok(reason_message);
        }
        if let Some(reason) = string_value(tool_call.get("reason")).filter(|value| !value.is_empty())
        {
            return Ok(reason.to_owned());
        }
        // ToolCallCompletedState carries error.message when success is false.
        if let Some(error) = tool_call.get("error").and_then(Value::as_object) {
            if let Some(message) =
                string_value(error.get("message")).filter(|value| !value.is_empty())
            {
                return Ok(message.to_owned());
            }
        }
        let status = string_value(tool_call.get("status"));
        return Ok(match status {
            Some("cancelled" | "denied") => "cancelled".into(),
            _ => "error".into(),
        });
    }
    Ok(String::new())
}

/// AHP StringOrMarkdown: plain string or `{ "markdown": "..." }`.
fn ahp_string_or_markdown(value: Option<&Value>) -> Option<String> {
    match value {
        Some(Value::String(text)) if !text.is_empty() => Some(text.clone()),
        Some(Value::Object(obj)) => string_value(obj.get("markdown"))
            .filter(|text| !text.is_empty())
            .map(str::to_owned),
        _ => None,
    }
}

fn first_ahp_working_directory(
    chat: &Map<String, Value>,
    session: Option<&Map<String, Value>>,
) -> Option<String> {
    for source in [Some(chat), session].into_iter().flatten() {
        let Some(dirs) = source.get("workingDirectories").and_then(Value::as_array) else {
            continue;
        };
        for dir in dirs {
            let Some(uri) = dir.as_str().filter(|value| !value.is_empty()) else {
                continue;
            };
            if let Some(path) = uri.strip_prefix("file://") {
                return Some(if path.is_empty() {
                    uri.to_owned()
                } else {
                    path.to_owned()
                });
            }
            return Some(uri.to_owned());
        }
    }
    None
}

fn ahp_timestamp(value: Option<&Value>) -> Option<i64> {
    let text = string_value(value)?;
    DateTime::parse_from_rfc3339(text)
        .ok()
        .map(|dt| dt.timestamp_millis())
}

fn compare_utf8(left: &str, right: &str) -> std::cmp::Ordering {
    left.as_bytes().cmp(right.as_bytes())
}

#[allow(clippy::cast_possible_truncation)]
fn number_as_i64(value: Option<&Value>) -> Option<i64> {
    value.and_then(Value::as_i64).or_else(|| {
        value
            .and_then(Value::as_f64)
            .map(|number| number.trunc() as i64)
    })
}

fn invalid_ahp_snapshot() -> TrajectoryError {
    TrajectoryError::new(
        "invalid_input",
        "AHP snapshot must be a JSON object with a chat object (Shape A export).",
    )
}

fn codex_output_text(value: Option<&Value>) -> Result<String, TrajectoryError> {
    match value {
        Some(Value::String(value)) => Ok(value.clone()),
        Some(Value::Array(_)) => {
            let text = read_blocks_text(value);
            if text.is_empty() {
                relaxed_json(value.expect("array is present"))
            } else {
                Ok(text)
            }
        }
        Some(Value::Object(object)) => {
            if let Some(content) = non_empty(string_value(object.get("content"))) {
                Ok(content)
            } else {
                relaxed_json(value.expect("object is present"))
            }
        }
        Some(Value::Null) | None => Ok(String::new()),
        Some(Value::Bool(value)) => Ok(value.to_string()),
        Some(Value::Number(value)) => Ok(value.to_string()),
    }
}

fn non_empty(value: Option<&str>) -> Option<String> {
    value.filter(|value| !value.is_empty()).map(str::to_owned)
}

fn non_empty_owned(value: Option<String>) -> Option<String> {
    value.filter(|value| !value.is_empty())
}

fn select_earlier(
    current: &mut Option<ContextCandidate>,
    value: Option<&str>,
    timestamp: i64,
    tie: &str,
) {
    let Some(value) = value.filter(|value| !value.is_empty()) else {
        return;
    };
    let replace = current.as_ref().is_none_or(|candidate| {
        timestamp < candidate.timestamp
            || (timestamp == candidate.timestamp && utf16_compare(tie, &candidate.tie).is_lt())
    });
    if replace {
        *current = Some(ContextCandidate {
            value: value.into(),
            timestamp,
            tie: tie.into(),
        });
    }
}

#[allow(clippy::too_many_arguments)]
fn decode_claude_message(
    row_type: &str,
    row: &Map<String, Value>,
    message: &Map<String, Value>,
    line: usize,
    offset: i64,
    timestamp: Option<&(i64, String)>,
    events: &mut Vec<DecodedEvent>,
    diagnostics: &mut Vec<Diagnostic>,
) -> Result<(), TrajectoryError> {
    let native_id = string_value(row.get("uuid")).map(str::to_owned);
    let producer_version = scalar_string(row.get("version"));
    let model = string_value(message.get("model")).map(str::to_owned);
    let mut component_index = 0_usize;
    let mut emit = |kind: EventKind,
                    role: Role,
                    content: Option<String>,
                    tool_call_id: Option<String>,
                    tool_name: Option<String>,
                    arguments_json: Option<String>,
                    is_error: Option<bool>| {
        events.push(DecodedEvent {
            kind,
            role,
            content,
            tool_call_id,
            tool_name,
            arguments_json,
            is_error,
            native_id: native_id.clone(),
            producer_version: producer_version.clone(),
            source_sequence: None,
            source_offset: offset,
            input_line: Some(line),
            timestamp_ms: timestamp.map(|value| value.0),
            timestamp_precise: timestamp.map(|value| value.1.clone()),
            component_index,
            model: if matches!(role, Role::Assistant | Role::Reasoning) {
                model.clone()
            } else {
                None
            },
        });
        component_index += 1;
    };

    let content = message.get("content");
    if row_type == "user" {
        match content {
            Some(Value::String(value)) => emit(
                EventKind::Message,
                Role::User,
                Some(value.clone()),
                None,
                None,
                None,
                None,
            ),
            Some(Value::Array(blocks)) => {
                let mut text_parts = Vec::new();
                for block in blocks {
                    let Value::Object(block) = block else {
                        continue;
                    };
                    match string_value(block.get("type")) {
                        Some("tool_result") => emit(
                            EventKind::ToolResult,
                            Role::Tool,
                            Some(read_blocks_text(block.get("content"))),
                            string_value(block.get("tool_use_id")).map(str::to_owned),
                            None,
                            None,
                            Some(block.get("is_error") == Some(&Value::Bool(true))),
                        ),
                        Some("text") => {
                            if let Some(text) =
                                string_value(block.get("text")).filter(|value| !value.is_empty())
                            {
                                text_parts.push(text.to_owned());
                            }
                        }
                        Some("image") => text_parts.push("[image]".into()),
                        _ => diagnostics.push(Diagnostic {
                            code: "unknown_content_block".into(),
                            message: format!(
                                "Skipped an unknown Claude Code user content block on line {line}."
                            ),
                            input_line: Some(line),
                            record_index: None,
                            count: None,
                        }),
                    }
                }
                if !text_parts.is_empty() {
                    emit(
                        EventKind::Message,
                        Role::User,
                        Some(text_parts.join("\n")),
                        None,
                        None,
                        None,
                        None,
                    );
                }
            }
            _ => {}
        }
        return Ok(());
    }

    match content {
        Some(Value::String(value)) => emit(
            EventKind::Message,
            Role::Assistant,
            Some(value.clone()),
            None,
            None,
            None,
            None,
        ),
        Some(Value::Array(blocks)) => {
            for block in blocks {
                let Value::Object(block) = block else {
                    continue;
                };
                match string_value(block.get("type")) {
                    Some("thinking") => emit(
                        EventKind::Reasoning,
                        Role::Reasoning,
                        Some(
                            string_value(block.get("thinking"))
                                .unwrap_or_default()
                                .into(),
                        ),
                        None,
                        None,
                        None,
                        None,
                    ),
                    Some("text") => emit(
                        EventKind::Message,
                        Role::Assistant,
                        Some(string_value(block.get("text")).unwrap_or_default().into()),
                        None,
                        None,
                        None,
                        None,
                    ),
                    Some("tool_use") => emit(
                        EventKind::ToolCall,
                        Role::Assistant,
                        None,
                        string_value(block.get("id")).map(str::to_owned),
                        string_value(block.get("name")).map(str::to_owned),
                        Some(
                            block
                                .get("input")
                                .map(relaxed_json)
                                .transpose()?
                                .unwrap_or_else(|| "{}".into()),
                        ),
                        None,
                    ),
                    Some("fallback") => {}
                    _ => diagnostics.push(Diagnostic {
                        code: "unknown_content_block".into(),
                        message: format!(
                            "Skipped an unknown Claude Code assistant content block on line {line}."
                        ),
                        input_line: Some(line),
                        record_index: None,
                        count: None,
                    }),
                }
            }
        }
        _ => {}
    }
    Ok(())
}

fn decode_message(
    row: &Map<String, Value>,
    message: &Map<String, Value>,
    line: usize,
    offset: i64,
    options: PiFamilyOptions,
    events: &mut Vec<DecodedEvent>,
) -> Result<(), TrajectoryError> {
    let role = string_value(message.get("role"));
    let native_id = string_value(row.get("id")).map(str::to_owned);
    let timestamp = row
        .get("timestamp")
        .and_then(parse_timestamp)
        .or_else(|| message.get("timestamp").and_then(parse_timestamp));
    let model = options.exclude_model(string_value(message.get("model")));
    let source_sequence = i64::try_from(line - 1).map_err(|_| {
        TrajectoryError::new(
            "invalid_input",
            "Transcript line sequence exceeds signed 64-bit range.",
        )
    })?;
    let mut component_index = 0_usize;
    let mut emit = |kind: EventKind,
                    event_role: Role,
                    content: Option<String>,
                    tool_call_id: Option<String>,
                    tool_name: Option<String>,
                    arguments_json: Option<String>,
                    is_error: Option<bool>| {
        events.push(DecodedEvent {
            kind,
            role: event_role,
            content,
            tool_call_id,
            tool_name,
            arguments_json,
            is_error,
            native_id: native_id.clone(),
            producer_version: None,
            source_sequence: Some(source_sequence),
            source_offset: offset,
            input_line: Some(line),
            timestamp_ms: timestamp.as_ref().map(|value| value.0),
            timestamp_precise: timestamp.as_ref().map(|value| value.1.clone()),
            component_index,
            model: if event_role == Role::Assistant || event_role == Role::Reasoning {
                model.clone()
            } else {
                None
            },
        });
        component_index += 1;
    };

    match role {
        Some("user") => {
            let content = read_blocks_text(message.get("content"));
            if !content.is_empty() {
                emit(
                    EventKind::Message,
                    Role::User,
                    Some(content),
                    None,
                    None,
                    None,
                    None,
                );
            }
        }
        Some("assistant") => match message.get("content") {
            Some(Value::String(content)) if !content.is_empty() => emit(
                EventKind::Message,
                Role::Assistant,
                Some(content.clone()),
                None,
                None,
                None,
                None,
            ),
            Some(Value::Array(parts)) => {
                for part in parts {
                    let Value::Object(part) = part else {
                        continue;
                    };
                    match string_value(part.get("type")) {
                        Some("thinking") => {
                            if let Some(content) = string_value(part.get("thinking")) {
                                if !content.is_empty() {
                                    emit(
                                        EventKind::Reasoning,
                                        Role::Reasoning,
                                        Some(content.into()),
                                        None,
                                        None,
                                        None,
                                        None,
                                    );
                                }
                            }
                        }
                        Some("text") => {
                            if let Some(content) = string_value(part.get("text")) {
                                if !content.is_empty() {
                                    emit(
                                        EventKind::Message,
                                        Role::Assistant,
                                        Some(content.into()),
                                        None,
                                        None,
                                        None,
                                        None,
                                    );
                                }
                            }
                        }
                        Some("toolCall") => {
                            let arguments = match part.get("arguments") {
                                None => "{}".into(),
                                Some(Value::String(value)) => value.clone(),
                                Some(value) => relaxed_json(value)?,
                            };
                            emit(
                                EventKind::ToolCall,
                                Role::Assistant,
                                None,
                                string_value(part.get("id")).map(str::to_owned),
                                string_value(part.get("name")).map(str::to_owned),
                                Some(arguments),
                                None,
                            );
                        }
                        _ => {}
                    }
                }
            }
            _ => {}
        },
        Some("toolResult" | "tool") => {
            let mut content = read_blocks_text(message.get("content"));
            let is_error = message.get("isError") == Some(&Value::Bool(true));
            if is_error && !content.to_lowercase().starts_with("error") {
                content = format!("Error: {content}");
            }
            emit(
                EventKind::ToolResult,
                Role::Tool,
                Some(content),
                string_value(message.get("toolCallId")).map(str::to_owned),
                string_value(message.get("toolName")).map(str::to_owned),
                None,
                Some(is_error),
            );
        }
        _ => {}
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn normalize_event(
    event: &DecodedEvent,
    event_index: usize,
    record_index: usize,
    group_id: &str,
    config: &AppliedConfig,
    partial: bool,
    plan: &mut Plan,
    diagnostics: &mut Vec<Diagnostic>,
) -> Result<Option<IrRecord>, TrajectoryError> {
    if matches!(event.kind, EventKind::Message | EventKind::Reasoning) {
        let content = event.content.clone().unwrap_or_default();
        if content.trim().is_empty() {
            return Ok(None);
        }
        let role = if event.kind == EventKind::Reasoning {
            Role::Reasoning
        } else {
            event.role
        };
        if role == Role::User
            && [
                "<local-command-caveat>",
                "<command-name>",
                "<command-message>",
                "<local-command-stdout>",
            ]
            .iter()
            .any(|prefix| content.trim_start().starts_with(prefix))
        {
            diagnostics.push(event_diagnostic(
                "noise_record_dropped",
                "Dropped a harness-noise user record.".into(),
                event,
                record_index,
            ));
            return Ok(None);
        }
        let bucket = if event.kind == EventKind::Reasoning {
            "reasoning"
        } else {
            "message"
        };
        return create_record(
            event,
            record_index,
            group_id,
            plan.ordinals[event_index],
            format!("{bucket}:{}", plan.ordinals[event_index]),
            role,
            Some(content),
            Vec::new(),
            None,
            None,
            None,
            config.base_byte_offset,
        )
        .map(Some);
    }

    if event.kind == EventKind::ToolCall {
        let call = plan.calls.get(&event_index).expect("every call is planned");
        if call.synthesized {
            diagnostics.push(event_diagnostic(
                "tool_call_id_synthesized",
                format!("Synthesized tool-call ID {}.", quote(&call.source_id)),
                event,
                record_index,
            ));
        }
        if call.renamed {
            diagnostics.push(event_diagnostic(
                "duplicate_tool_call_id",
                format!(
                    "Renamed duplicate tool-call ID {} to {}.",
                    quote(&call.source_id),
                    quote(&call.final_id)
                ),
                event,
                record_index,
            ));
        }
        let name = event
            .tool_name
            .clone()
            .unwrap_or_else(|| "unknown_tool".into());
        if event.tool_name.is_none() {
            diagnostics.push(event_diagnostic(
                "unknown_tool_name",
                format!("Substituted {} for a missing tool name.", quote(&name)),
                event,
                record_index,
            ));
        }
        let shrunk = shrink_arguments(
            event.arguments_json.as_deref(),
            config.bounds.tool_arguments_max_characters,
        )?;
        if shrunk.reshaped {
            diagnostics.push(event_diagnostic(
                "tool_arguments_reshaped",
                format!(
                    "Reshaped arguments for tool call {} into a JSON object.",
                    quote(&call.final_id)
                ),
                event,
                record_index,
            ));
        }
        if shrunk.truncated {
            diagnostics.push(event_diagnostic(
                "tool_arguments_truncated",
                format!(
                    "Truncated arguments for tool call {} to at most {} Unicode code points.",
                    quote(&call.final_id),
                    config
                        .bounds
                        .tool_arguments_max_characters
                        .expect("truncation requires a bound")
                ),
                event,
                record_index,
            ));
        }
        return create_record(
            event,
            record_index,
            group_id,
            plan.ordinals[event_index],
            format!("tool-call:{}", call.final_id),
            Role::Assistant,
            None,
            vec![ToolCall {
                id: call.final_id.clone(),
                name,
                arguments_json: shrunk.arguments,
            }],
            None,
            None,
            None,
            config.base_byte_offset,
        )
        .map(Some);
    }

    let source_id = event.tool_call_id.as_deref().unwrap_or_default();
    let entries = plan.open_calls.get_mut(source_id);
    let open_index = entries
        .as_ref()
        .and_then(|values| values.iter().position(|entry| !entry.consumed));
    let cross_chunk = open_index.is_none()
        && partial
        && !source_id.is_empty()
        && entries.as_ref().is_none_or(|values| values.is_empty());
    if open_index.is_none() && !cross_chunk {
        let duplicate = entries.as_ref().is_some_and(|values| !values.is_empty());
        diagnostics.push(event_diagnostic(
            if duplicate {
                "duplicate_tool_result"
            } else {
                "orphan_tool_result"
            },
            if duplicate {
                format!(
                    "Dropped a duplicate result for tool call {}.",
                    quote(source_id)
                )
            } else {
                format!(
                    "Dropped a tool result without a preceding call for {}.",
                    quote(source_id)
                )
            },
            event,
            record_index,
        ));
        return Ok(None);
    }
    let final_id = if let (Some(values), Some(index)) = (entries, open_index) {
        values[index].consumed = true;
        values[index].final_id.clone()
    } else {
        source_id.to_owned()
    };
    if !config.filters.include_tool_results {
        return Ok(None);
    }
    let original = event.content.clone().unwrap_or_default();
    let content = truncate_result(
        &original,
        config.bounds.tool_results_max_characters,
        config.bounds.tool_results_strategy,
    );
    if content != original {
        diagnostics.push(event_diagnostic(
            "tool_result_truncated",
            format!(
                "Truncated the result for tool call {} to at most {} Unicode code points using the {} strategy.",
                quote(&final_id),
                config
                    .bounds
                    .tool_results_max_characters
                    .expect("truncation requires a bound"),
                quote(config.bounds.tool_results_strategy.wire_name())
            ),
            event,
            record_index,
        ));
    }
    create_record(
        event,
        record_index,
        group_id,
        plan.ordinals[event_index],
        format!("tool-result:{final_id}"),
        Role::Tool,
        Some(content),
        Vec::new(),
        Some(final_id),
        event.tool_name.clone(),
        Some(event.is_error.unwrap_or(false)),
        config.base_byte_offset,
    )
    .map(Some)
}

#[allow(clippy::too_many_arguments)]
fn create_record(
    event: &DecodedEvent,
    record_index: usize,
    group_id: &str,
    ordinal: usize,
    component_key: String,
    role: Role,
    content: Option<String>,
    tool_calls: Vec<ToolCall>,
    tool_call_id: Option<String>,
    tool_name: Option<String>,
    is_error: Option<bool>,
    base_byte_offset: i64,
) -> Result<IrRecord, TrajectoryError> {
    let absolute_offset = event
        .source_offset
        .checked_add(base_byte_offset)
        .ok_or_else(|| TrajectoryError::new("invalid_input", "Byte anchor is out of range."))?;
    let stable_id = event
        .native_id
        .clone()
        .unwrap_or_else(|| sha256(&format!("{group_id}|byte|{absolute_offset}")));
    let provenance = Provenance {
        stable_source_record_id: stable_id.clone(),
        source_identity_kind: if event.native_id.is_some() {
            "native"
        } else {
            "location"
        },
        source_order_id: format!(
            "1|{}|{:020}|{}",
            event
                .timestamp_ms
                .map_or_else(|| Ok("0000-00-00T00:00:00.001Z".into()), format_ms)?,
            event.source_sequence.unwrap_or(0),
            stable_id
        ),
        component_key: component_key.clone(),
        component_index: event.component_index,
        component_type_ordinal: ordinal,
        native_record_id: event.native_id.clone(),
        producer_version: event.producer_version.clone(),
        source_sequence: event.source_sequence,
        source_offset: Some(if event.native_id.is_some() {
            event.source_offset
        } else {
            absolute_offset
        }),
        source_anchor_kind: Some("byte"),
    };
    let kind = if !tool_calls.is_empty() {
        RecordKind::AssistantToolCalls
    } else if role == Role::Tool {
        RecordKind::ToolResult
    } else {
        RecordKind::Message
    };
    Ok(IrRecord {
        id: sha256(&relaxed_json(&Value::Array(vec![
            Value::String(group_id.into()),
            Value::String(stable_id),
            Value::String(component_key),
        ]))?),
        kind,
        role,
        order: i64::try_from(record_index - 1).map_err(|_| {
            TrajectoryError::new("invalid_input", "Record order exceeds signed 64-bit range.")
        })?,
        source_timestamp_ms: event.timestamp_ms,
        source_timestamp_precise: event.timestamp_precise.clone(),
        timestamp_ms: None,
        content,
        source_name: None,
        cwd: None,
        git_branch: None,
        model: None,
        producer_version: None,
        tool_calls,
        tool_call_id,
        tool_name,
        is_error,
        provenance,
        hashes: RecordHashes {
            content_sha256: String::new(),
            record_sha256: String::new(),
        },
    })
}

fn create_meta(
    group_id: &str,
    source_name: &str,
    cwd: Option<String>,
    git_branch: Option<String>,
    model: Option<String>,
    producer_version: Option<String>,
) -> Result<IrRecord, TrajectoryError> {
    let mut record = IrRecord {
        id: sha256(&relaxed_json(&Value::Array(vec![
            Value::String(group_id.into()),
            Value::String("meta".into()),
            Value::String("meta".into()),
        ]))?),
        kind: RecordKind::Meta,
        role: Role::Meta,
        order: -1,
        source_timestamp_ms: None,
        source_timestamp_precise: None,
        timestamp_ms: None,
        content: None,
        source_name: Some(source_name.into()),
        cwd,
        git_branch,
        model,
        producer_version,
        tool_calls: Vec::new(),
        tool_call_id: None,
        tool_name: None,
        is_error: None,
        provenance: Provenance {
            stable_source_record_id: "meta".into(),
            source_identity_kind: "synthetic",
            source_order_id: "0|0000-00-00T00:00:00.000Z|00000000000000000000|meta".into(),
            component_key: "meta".into(),
            component_index: 0,
            component_type_ordinal: 0,
            native_record_id: None,
            producer_version: None,
            source_sequence: None,
            source_offset: None,
            source_anchor_kind: None,
        },
        hashes: RecordHashes {
            content_sha256: String::new(),
            record_sha256: String::new(),
        },
    };
    record.hashes = hash_record(&record)?;
    Ok(record)
}

fn normalize_execution(
    decoded: &[DecodedModelInvocation],
    group_id: &str,
    base_byte_offset: i64,
) -> Result<TrajectoryExecution, TrajectoryError> {
    let model_invocations = decoded
        .iter()
        .map(|invocation| {
            let source_offset = invocation
                .source_offset
                .map(|offset| {
                    offset.checked_add(base_byte_offset).ok_or_else(|| {
                        TrajectoryError::new(
                            "invalid_input",
                            "Model invocation byte anchor is out of range.",
                        )
                    })
                })
                .transpose()?;
            let identity = invocation.native_id.clone().unwrap_or_else(|| {
                source_offset.map_or_else(
                    || {
                        invocation
                            .response_id
                            .clone()
                            .unwrap_or_else(|| "model-invocation".into())
                    },
                    |offset| sha256(&format!("{group_id}|byte|{offset}")),
                )
            });
            Ok(ModelInvocation {
                id: sha256(&relaxed_json(&Value::Array(vec![
                    Value::String(group_id.into()),
                    Value::String(identity),
                    Value::String("model-invocation".into()),
                ]))?),
                native_record_id: invocation.native_id.clone(),
                source_offset,
                provider: invocation.provider.clone(),
                api_family: invocation.api_family.clone(),
                requested_model: invocation.requested_model.clone(),
                response_model: invocation.response_model.clone(),
                response_id: invocation.response_id.clone(),
                stop_reason: invocation.stop_reason.clone(),
                producer_version: invocation.producer_version.clone(),
                usage: invocation.usage.clone().filter(|usage| {
                    usage.input_tokens.is_some()
                        || usage.output_tokens.is_some()
                        || usage.cache_read_tokens.is_some()
                        || usage.cache_write_tokens.is_some()
                        || usage.total_tokens.is_some()
                }),
                started_at_ms: invocation.started_at_ms,
                started_at_precise: invocation.started_at_precise.clone(),
                completed_at_ms: invocation.completed_at_ms,
                completed_at_precise: invocation.completed_at_precise.clone(),
            })
        })
        .collect::<Result<Vec<_>, TrajectoryError>>()?;
    Ok(TrajectoryExecution { model_invocations })
}

fn hash_record(record: &IrRecord) -> Result<RecordHashes, TrajectoryError> {
    let semantic = match record.kind {
        RecordKind::Meta => {
            let mut value = Map::new();
            value.insert(
                "source".into(),
                Value::String(
                    record
                        .source_name
                        .clone()
                        .expect("meta source name is populated"),
                ),
            );
            if let Some(cwd) = &record.cwd {
                value.insert("cwd".into(), Value::String(cwd.clone()));
            }
            if let Some(git_branch) = &record.git_branch {
                value.insert("git_branch".into(), Value::String(git_branch.clone()));
            }
            if let Some(model) = &record.model {
                value.insert("model".into(), Value::String(model.clone()));
            }
            Value::Object(value)
        }
        RecordKind::AssistantToolCalls => {
            let call = &record.tool_calls[0];
            let mut value = Map::new();
            value.insert("name".into(), Value::String(call.name.clone()));
            value.insert("args".into(), Value::String(call.arguments_json.clone()));
            Value::Object(value)
        }
        RecordKind::Message | RecordKind::ToolResult => {
            let mut value = Map::new();
            value.insert(
                "content".into(),
                Value::String(record.content.clone().unwrap_or_default()),
            );
            Value::Object(value)
        }
    };
    let mut envelope = Map::new();
    envelope.insert("type".into(), Value::String(record_type(record).into()));
    envelope.insert("content".into(), semantic);
    Ok(RecordHashes {
        content_sha256: sha256(&canonical_json(&Value::Object(envelope))?),
        record_sha256: sha256(&canonical_json(&to_letta_record(record))?),
    })
}

fn plan_events(events: &[DecodedEvent]) -> Plan {
    let mut calls = HashMap::new();
    let mut open_calls: HashMap<String, Vec<PlannedCall>> = HashMap::new();
    let mut used = HashSet::new();
    let mut ordinals = Vec::with_capacity(events.len());
    let mut seen: HashMap<(i64, EventKind), usize> = HashMap::new();
    let mut occurrence = -1_i64;
    for (index, event) in events.iter().enumerate() {
        if event.component_index == 0 {
            occurrence += 1;
        }
        let key = (occurrence, event.kind);
        let ordinal = *seen.get(&key).unwrap_or(&0);
        ordinals.push(ordinal);
        seen.insert(key, ordinal + 1);
        if event.kind != EventKind::ToolCall {
            continue;
        }
        let source_id = event
            .tool_call_id
            .clone()
            .unwrap_or_else(|| format!("call_{}", index + 1));
        let mut final_id = source_id.clone();
        let mut suffix = 2;
        while used.contains(&final_id) {
            final_id = format!("{source_id}__{suffix}");
            suffix += 1;
        }
        let call = PlannedCall {
            source_id: source_id.clone(),
            final_id: final_id.clone(),
            synthesized: event.tool_call_id.is_none(),
            renamed: final_id != source_id,
            consumed: false,
        };
        used.insert(final_id);
        calls.insert(index, call.clone());
        open_calls.entry(source_id).or_default().push(call);
    }
    Plan {
        calls,
        open_calls,
        ordinals,
    }
}

fn fill_timestamps(
    count: usize,
    anchors: &BTreeMap<usize, i64>,
    created_at_ms: Option<i64>,
    diagnostics: &mut Vec<Diagnostic>,
) -> Result<Vec<i64>, TrajectoryError> {
    if count == 0 {
        return Ok(Vec::new());
    }
    if anchors.is_empty() {
        diagnostics.push(Diagnostic {
            code: "timestamps_synthesized".into(),
            message: format!("Synthesized timestamps for {count} normalized records."),
            input_line: None,
            record_index: None,
            count: Some(count),
        });
        let start = created_at_ms.unwrap_or(SYNTHETIC_BASE);
        return (0..count)
            .map(|index| {
                let step = i64::try_from(index)
                    .ok()
                    .and_then(|value| value.checked_mul(15_000))
                    .ok_or_else(|| {
                        TrajectoryError::new(
                            "invalid_input",
                            "Synthesized timestamp is out of range.",
                        )
                    })?;
                start.checked_add(step).ok_or_else(|| {
                    TrajectoryError::new("invalid_input", "Synthesized timestamp is out of range.")
                })
            })
            .collect();
    }
    let mut output = vec![0; count];
    let indexes = anchors.keys().copied().collect::<Vec<_>>();
    let first = indexes[0];
    let last = *indexes.last().expect("anchors are not empty");
    for index in 0..first {
        let distance = i64::try_from(first - index).map_err(|_| {
            TrajectoryError::new("invalid_input", "Interpolated timestamp is out of range.")
        })?;
        output[index] = anchors[&first]
            .checked_sub(distance.saturating_mul(1_000))
            .ok_or_else(|| {
                TrajectoryError::new("invalid_input", "Interpolated timestamp is out of range.")
            })?;
    }
    for pair in indexes.windows(2) {
        let start_index = pair[0];
        let end_index = pair[1];
        let start = anchors[&start_index];
        let span = i128::from(anchors[&end_index]) - i128::from(start);
        output[start_index] = start;
        for index in start_index + 1..end_index {
            let numerator = span * i128::try_from(index - start_index).expect("usize fits i128");
            let denominator = i128::try_from(end_index - start_index).expect("usize fits i128");
            let value = i128::from(start) + numerator / denominator;
            output[index] = i64::try_from(value).map_err(|_| {
                TrajectoryError::new("invalid_input", "Interpolated timestamp is out of range.")
            })?;
        }
    }
    output[last] = anchors[&last];
    for index in last + 1..count {
        let distance = i64::try_from(index - last).map_err(|_| {
            TrajectoryError::new("invalid_input", "Interpolated timestamp is out of range.")
        })?;
        output[index] = anchors[&last]
            .checked_add(distance.saturating_mul(1_000))
            .ok_or_else(|| {
                TrajectoryError::new("invalid_input", "Interpolated timestamp is out of range.")
            })?;
    }
    let interpolated = count - anchors.len();
    if interpolated > 0 {
        diagnostics.push(Diagnostic {
            code: "timestamps_interpolated".into(),
            message: format!("Interpolated timestamps for {interpolated} normalized records."),
            input_line: None,
            record_index: None,
            count: Some(interpolated),
        });
    }
    Ok(output)
}

struct ShrunkArguments {
    arguments: String,
    reshaped: bool,
    truncated: bool,
}

fn shrink_arguments(
    raw_input: Option<&str>,
    limit: Option<usize>,
) -> Result<ShrunkArguments, TrajectoryError> {
    let raw = raw_input.filter(|value| !value.is_empty()).unwrap_or("{}");
    let parsed = serde_json::from_str::<Value>(raw).ok();
    let Some(Value::Object(mut object)) = parsed else {
        let mut wrapped = Map::new();
        wrapped.insert("_raw".into(), Value::String(raw.into()));
        let full = relaxed_json(&Value::Object(wrapped))?;
        if limit.is_none_or(|maximum| full.chars().count() <= maximum) {
            return Ok(ShrunkArguments {
                arguments: full,
                reshaped: true,
                truncated: false,
            });
        }
        return Ok(ShrunkArguments {
            arguments: wrap_raw(raw, limit.expect("bounded branch"))?,
            reshaped: true,
            truncated: true,
        });
    };
    if limit.is_none_or(|maximum| raw.chars().count() <= maximum) {
        return Ok(ShrunkArguments {
            arguments: raw.into(),
            reshaped: false,
            truncated: false,
        });
    }
    let maximum = limit.expect("bounded branch");
    let paths = string_leaf_paths(&Value::Object(object.clone()));
    let mut lengths = paths
        .iter()
        .map(|path| {
            leaf_string(&Value::Object(object.clone()), path)
                .map_or(0, |value| value.chars().count())
        })
        .collect::<Vec<_>>();
    let mut serialized = relaxed_json(&Value::Object(object.clone()))?;
    while serialized.chars().count() > maximum {
        let largest = lengths
            .iter()
            .enumerate()
            .filter(|(_, length)| **length > 0)
            .max_by_key(|(index, length)| (**length, std::cmp::Reverse(*index)))
            .map(|(index, _)| index);
        let Some(index) = largest else {
            break;
        };
        set_leaf_empty(&mut object, &paths[index]);
        lengths[index] = 0;
        serialized = relaxed_json(&Value::Object(object.clone()))?;
    }
    if serialized.chars().count() <= maximum {
        Ok(ShrunkArguments {
            arguments: serialized,
            reshaped: false,
            truncated: true,
        })
    } else {
        Ok(ShrunkArguments {
            arguments: wrap_raw(raw, maximum)?,
            reshaped: true,
            truncated: true,
        })
    }
}

#[derive(Clone)]
enum PathPart {
    Key(String),
    Index(usize),
}

fn string_leaf_paths(value: &Value) -> Vec<Vec<PathPart>> {
    fn visit(value: &Value, path: &mut Vec<PathPart>, output: &mut Vec<Vec<PathPart>>) {
        match value {
            Value::Object(values) => {
                for (key, value) in values {
                    path.push(PathPart::Key(key.clone()));
                    if value.is_string() {
                        output.push(path.clone());
                    } else {
                        visit(value, path, output);
                    }
                    path.pop();
                }
            }
            Value::Array(values) => {
                for (index, value) in values.iter().enumerate() {
                    path.push(PathPart::Index(index));
                    if value.is_string() {
                        output.push(path.clone());
                    } else {
                        visit(value, path, output);
                    }
                    path.pop();
                }
            }
            _ => {}
        }
    }
    let mut output = Vec::new();
    visit(value, &mut Vec::new(), &mut output);
    output
}

fn leaf_string<'a>(value: &'a Value, path: &[PathPart]) -> Option<&'a str> {
    let mut current = value;
    for part in path {
        current = match part {
            PathPart::Key(key) => current.get(key)?,
            PathPart::Index(index) => current.get(*index)?,
        };
    }
    current.as_str()
}

fn set_leaf_empty(object: &mut Map<String, Value>, path: &[PathPart]) {
    let mut current = object
        .get_mut(match &path[0] {
            PathPart::Key(key) => key,
            PathPart::Index(_) => unreachable!("root argument is an object"),
        })
        .expect("collected path exists");
    for part in &path[1..] {
        current = match part {
            PathPart::Key(key) => current.get_mut(key).expect("collected path exists"),
            PathPart::Index(index) => current.get_mut(*index).expect("collected path exists"),
        };
    }
    *current = Value::String(String::new());
}

fn wrap_raw(raw: &str, limit: usize) -> Result<String, TrajectoryError> {
    let points = raw.chars().collect::<Vec<_>>();
    let mut low = 0;
    let mut high = points.len().min(limit);
    let mut best = "{}".to_owned();
    while low <= high {
        let keep = low + (high - low) / 2;
        let mut value = Map::new();
        value.insert(
            "_raw".into(),
            Value::String(
                points[..keep].iter().collect::<String>()
                    + if points.len() > keep { "…" } else { "" },
            ),
        );
        let candidate = canonical_json(&Value::Object(value))?;
        if candidate.chars().count() <= limit {
            best = candidate;
            low = keep + 1;
        } else if keep == 0 {
            break;
        } else {
            high = keep - 1;
        }
    }
    Ok(best)
}

fn truncate_result(text: &str, limit: Option<usize>, strategy: TruncationStrategy) -> String {
    let points = text.chars().collect::<Vec<_>>();
    let Some(limit) = limit else {
        return text.into();
    };
    if points.len() <= limit {
        return text.into();
    }
    let mut low = 0;
    let mut high = (points.len() - 1).min(limit);
    let mut keep = None;
    while low <= high {
        let candidate = low + (high - low) / 2;
        if candidate < limit {
            keep = Some(candidate);
            low = candidate + 1;
        } else if candidate == 0 {
            break;
        } else {
            high = candidate - 1;
        }
    }
    let keep = keep.unwrap_or_else(|| limit.saturating_sub(1));
    if strategy == TruncationStrategy::Head {
        return points[..keep].iter().collect::<String>() + "…";
    }
    let head = keep.div_ceil(2);
    points[..head].iter().collect::<String>()
        + "…"
        + &points[points.len() - (keep - head)..]
            .iter()
            .collect::<String>()
}

fn resolve_config(request: NormalizeRequest<'_>) -> Result<AppliedConfig, TrajectoryError> {
    let arguments = request
        .options
        .tool_arguments_max_characters
        .unwrap_or(Some(20_000));
    let results = request
        .options
        .tool_results_max_characters
        .unwrap_or(Some(2_500));
    if arguments == Some(0) || arguments == Some(1) || results == Some(0) {
        return Err(TrajectoryError::new(
            "invalid_input",
            "Normalization bounds must be positive and tool argument bounds must fit {}.",
        ));
    }
    Ok(AppliedConfig {
        bounds: Bounds {
            tool_arguments_max_characters: arguments,
            tool_results_max_characters: results,
            tool_results_strategy: request
                .options
                .tool_results_strategy
                .unwrap_or(TruncationStrategy::HeadTail),
        },
        filters: Filters {
            include_tool_results: request.options.include_tool_results.unwrap_or(true),
        },
        source_group_id: request.source_context.group_id.map(str::to_owned),
        base_byte_offset: request.source_context.base_byte_offset,
        partial: request.source_context.partial,
    })
}

fn parse_timestamp(value: &Value) -> Option<(i64, String)> {
    if let Some(milliseconds) = value.as_i64() {
        if milliseconds > 100_000_000_000 {
            let date = Utc.timestamp_millis_opt(milliseconds).single()?;
            return Some((
                milliseconds,
                date.to_rfc3339_opts(SecondsFormat::Millis, true)
                    .replace('Z', "0000+00:00"),
            ));
        }
    }
    let text = value.as_str()?;
    let parsed = chrono::DateTime::parse_from_rfc3339(text).ok()?;
    let milliseconds = parsed.timestamp_millis();
    let fraction = text
        .split_once('.')
        .map(|(_, value)| {
            value
                .chars()
                .take_while(char::is_ascii_digit)
                .take(7)
                .collect::<String>()
        })
        .unwrap_or_default();
    let seven = format!("{fraction:0<7}");
    let base = Utc
        .timestamp_millis_opt(milliseconds)
        .single()?
        .format("%Y-%m-%dT%H:%M:%S")
        .to_string();
    Some((milliseconds, format!("{base}.{seven}+00:00")))
}

fn read_blocks_text(value: Option<&Value>) -> String {
    match value {
        Some(Value::String(value)) => value.clone(),
        Some(Value::Array(values)) => values
            .iter()
            .filter_map(|item| {
                let Value::Object(item) = item else {
                    return None;
                };
                match string_value(item.get("type")) {
                    Some("image") => Some("[image]".into()),
                    None | Some("text" | "input_text" | "output_text") => {
                        string_value(item.get("text"))
                            .filter(|value| !value.is_empty())
                            .map(str::to_owned)
                    }
                    _ => None,
                }
            })
            .collect::<Vec<_>>()
            .join("\n"),
        _ => String::new(),
    }
}

fn scalar_string(value: Option<&Value>) -> Option<String> {
    match value {
        Some(Value::String(value)) => Some(value.clone()),
        Some(Value::Number(value)) => Some(value.to_string()),
        _ => None,
    }
}

fn string_value(value: Option<&Value>) -> Option<&str> {
    value.and_then(Value::as_str)
}

fn event_diagnostic(
    code: &str,
    message: String,
    event: &DecodedEvent,
    record_index: usize,
) -> Diagnostic {
    Diagnostic {
        code: code.into(),
        message,
        input_line: event.input_line,
        record_index: Some(record_index),
        count: None,
    }
}

fn quote(value: &str) -> String {
    relaxed_json(&Value::String(value.into())).expect("string serialization cannot fail")
}

#[cfg(test)]
mod tests {
    use serde_json::Value;

    use super::{NormalizeRequest, SourceAdapter};
    use crate::{
        ClaudeCodeSourceAdapter, CodexSourceAdapter, PiSourceAdapter, SourceContext,
        project_canonical, project_hypabolic, project_letta,
    };

    #[test]
    fn byte_slice_is_the_primary_boundary() {
        let transcript = br#"{"type":"session","id":"test"}
{"type":"message","id":"u","message":{"role":"user","content":"hello"}}
{"type":"message","id":"a","message":{"role":"assistant","content":"world"}}
"#;
        let result = PiSourceAdapter
            .normalize(NormalizeRequest {
                transcript,
                source_context: SourceContext::default(),
                options: Default::default(),
            })
            .unwrap();
        assert_eq!(result.group_id, "test");
        assert_eq!(result.records.len(), 3);
    }

    #[test]
    fn claude_native_uuid_identity_matches_the_shared_golden() {
        let result = ClaudeCodeSourceAdapter
            .normalize(NormalizeRequest {
                transcript: include_bytes!(
                    "../../../../conformance/cases/claude-code/tool-call/input.jsonl"
                ),
                source_context: SourceContext::default(),
                options: Default::default(),
            })
            .expect("shared Claude case normalizes");
        let expected = include_str!(
            "../../../../conformance/cases/claude-code/tool-call/expected.canonical.json"
        );
        let expected = expected
            .strip_suffix("\r\n")
            .or_else(|| expected.strip_suffix('\n'))
            .unwrap_or(expected);
        assert_eq!(
            project_canonical(&result).expect("canonical projection"),
            expected
        );
    }

    #[test]
    fn claude_context_is_selected_by_source_time_and_execution_is_retained() {
        let transcript = br#"{"type":"assistant","uuid":"a","sessionId":"s","version":"2","timestamp":"2026-01-01T00:00:02Z","cwd":"/late","gitBranch":"late","message":{"id":"response","role":"assistant","model":"claude","stop_reason":"end_turn","usage":{"input_tokens":7,"output_tokens":3},"content":"done"}}
{"type":"user","uuid":"u","sessionId":"s","version":"1","timestamp":"2026-01-01T00:00:01Z","cwd":"/early","gitBranch":"early","message":{"role":"user","content":"go"}}
"#;
        let result = ClaudeCodeSourceAdapter
            .normalize(NormalizeRequest {
                transcript,
                source_context: SourceContext::default(),
                options: Default::default(),
            })
            .expect("reversed-arrival transcript normalizes");
        let letta = project_letta(&result).expect("Letta projection");
        assert!(letta.contains(r#""cwd":"/early""#));
        assert!(letta.contains(r#""git_branch":"early""#));
        assert_eq!(result.producer_version.as_deref(), Some("1"));
        let hypabolic = project_hypabolic(&result).expect("Hypabolic projection");
        assert!(hypabolic.contains(r#""type":"claude-code""#));
        assert!(hypabolic.contains(r#""producer_version":"2""#));
        let invocation = &result.execution.model_invocations[0];
        assert_eq!(invocation.response_id.as_deref(), Some("response"));
        assert_eq!(
            invocation
                .usage
                .as_ref()
                .and_then(|usage| usage.input_tokens),
            Some(7)
        );
    }

    #[test]
    fn claude_multiple_session_ids_are_a_typed_fatal_error() {
        let transcript =
            br#"{"type":"user","sessionId":"b","message":{"role":"user","content":"go"}}
{"type":"assistant","sessionId":"a","message":{"role":"assistant","content":"done"}}
"#;
        let error = ClaudeCodeSourceAdapter
            .normalize(NormalizeRequest {
                transcript,
                source_context: SourceContext::default(),
                options: Default::default(),
            })
            .expect_err("multiple native groups conflict");
        assert_eq!(error.code, "source_group_conflict");
        assert_eq!(
            error.message,
            r#"Claude Code transcript contains multiple session ids: "a", "b"."#
        );
    }

    #[test]
    fn codex_arbitrary_chunks_preserve_whole_canonical_identity() {
        let transcript = include_bytes!("../../../../conformance/cases/codex/chunks/input.jsonl");
        let whole = CodexSourceAdapter
            .normalize(NormalizeRequest {
                transcript,
                source_context: SourceContext::default(),
                options: Default::default(),
            })
            .expect("whole Codex rollout normalizes");
        let whole_value: Value =
            serde_json::from_str(&project_canonical(&whole).expect("whole canonical projection"))
                .expect("canonical JSON");
        let expected = whole_value["records"]
            .as_array()
            .expect("records array")
            .clone();

        for split_after_line in [2, 3] {
            let split = transcript
                .iter()
                .enumerate()
                .filter(|(_, value)| **value == b'\n')
                .nth(split_after_line - 1)
                .map(|(index, _)| index + 1)
                .expect("fixture has split line");
            let first = CodexSourceAdapter
                .normalize(NormalizeRequest {
                    transcript: &transcript[..split],
                    source_context: SourceContext {
                        group_id: None,
                        base_byte_offset: 0,
                        partial: true,
                    },
                    options: Default::default(),
                })
                .expect("initial chunk normalizes");
            let second = CodexSourceAdapter
                .normalize(NormalizeRequest {
                    transcript: &transcript[split..],
                    source_context: SourceContext {
                        group_id: Some("codex-chunk-session"),
                        base_byte_offset: i64::try_from(split).expect("fixture offset fits i64"),
                        partial: true,
                    },
                    options: Default::default(),
                })
                .expect("continuation chunk normalizes");
            let mut actual = serde_json::from_str::<Value>(
                &project_canonical(&first).expect("initial canonical projection"),
            )
            .expect("canonical JSON")["records"]
                .as_array()
                .expect("records array")
                .clone();
            actual.extend(
                serde_json::from_str::<Value>(
                    &project_canonical(&second).expect("continuation canonical projection"),
                )
                .expect("canonical JSON")["records"]
                    .as_array()
                    .expect("records array")
                    .clone(),
            );
            assert_eq!(actual, expected);
        }
    }

    #[test]
    fn codex_hypabolic_projection_retains_source_and_tool_families() {
        let result = CodexSourceAdapter
            .normalize(NormalizeRequest {
                transcript: include_bytes!("../../../../conformance/cases/codex/full/input.jsonl"),
                source_context: SourceContext::default(),
                options: Default::default(),
            })
            .expect("full Codex rollout normalizes");
        let output = project_hypabolic(&result).expect("Hypabolic projection");
        assert!(output.contains(r#""type":"codex""#));
        assert!(output.contains(r#""producer_version":"0.140.0""#));
        assert!(output.contains(r#""name":"apply_patch""#));
        assert!(output.contains(r#""name":"tool_search""#));
    }

    #[test]
    fn codex_requires_a_group_only_for_canonical_identity() {
        let trajectory = CodexSourceAdapter
            .normalize(NormalizeRequest {
                transcript: include_bytes!(
                    "../../../../conformance/cases/codex/missing-group/input.jsonl"
                ),
                source_context: SourceContext::default(),
                options: Default::default(),
            })
            .expect("non-canonical normalization permits the default group");
        assert!(!trajectory.source_group_resolved);
        project_letta(&trajectory).expect("Letta projection does not require canonical identity");
        let error =
            project_canonical(&trajectory).expect_err("canonical identity requires a group");
        assert_eq!(error.code, "source_group_required");
    }
}
