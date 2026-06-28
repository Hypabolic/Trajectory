use std::collections::{BTreeMap, HashMap, HashSet};

use chrono::{SecondsFormat, TimeZone as _, Utc};
use serde_json::{Map, Value};

use crate::canonical::{canonical_json, relaxed_json, utf16_compare};
use crate::model::{
    AppliedConfig, Bounds, Diagnostic, Filters, IrRecord, NormalizeRequest, Provenance,
    RecordHashes, RecordKind, Role, ToolCall, Trajectory, TrajectoryError, TruncationStrategy,
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
    source_sequence: i64,
    source_offset: i64,
    input_line: usize,
    timestamp_ms: Option<i64>,
    timestamp_precise: Option<String>,
    component_index: usize,
    model: Option<String>,
}

struct DecodedSession {
    group_id: Option<String>,
    cwd: Option<String>,
    producer_version: Option<String>,
    created_at_ms: Option<i64>,
    events: Vec<DecodedEvent>,
    diagnostics: Vec<Diagnostic>,
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
    let decoded = decode_pi(request.transcript)?;
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
    let model = model_counts.first().map(|value| value.0.clone());
    let meta = create_meta(
        &group_id,
        decoded.cwd,
        model,
        decoded.producer_version.clone(),
    )?;
    records.insert(0, meta);
    Ok(Trajectory {
        group_id,
        producer_version: decoded.producer_version,
        records,
        diagnostics,
        config,
    })
}

fn decode_pi(bytes: &[u8]) -> Result<DecodedSession, TrajectoryError> {
    let mut events = Vec::new();
    let mut diagnostics = Vec::new();
    let mut group_id = None;
    let mut cwd = None;
    let mut producer_version = None;
    let mut created_at_ms = None;
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
                    } else if row_type == Some("message") {
                        if let Some(Value::Object(message)) = row.get("message") {
                            saw_message = true;
                            decode_message(
                                &row,
                                message,
                                line,
                                i64::try_from(offset).map_err(|_| {
                                    TrajectoryError::new(
                                        "invalid_input",
                                        "Transcript byte offset exceeds signed 64-bit range.",
                                    )
                                })?,
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
            "Pi transcript must be session JSONL containing a session header or message entries.",
        ));
    }
    Ok(DecodedSession {
        group_id,
        cwd,
        producer_version,
        created_at_ms,
        events,
        diagnostics,
    })
}

fn decode_message(
    row: &Map<String, Value>,
    message: &Map<String, Value>,
    line: usize,
    offset: i64,
    events: &mut Vec<DecodedEvent>,
) -> Result<(), TrajectoryError> {
    let role = string_value(message.get("role"));
    let native_id = string_value(row.get("id")).map(str::to_owned);
    let timestamp = row
        .get("timestamp")
        .and_then(parse_timestamp)
        .or_else(|| message.get("timestamp").and_then(parse_timestamp));
    let model = string_value(message.get("model")).map(str::to_owned);
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
            source_sequence,
            source_offset: offset,
            input_line: line,
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
            event.source_sequence,
            stable_id
        ),
        component_key: component_key.clone(),
        component_index: event.component_index,
        component_type_ordinal: ordinal,
        native_record_id: event.native_id.clone(),
        source_sequence: Some(event.source_sequence),
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
    cwd: Option<String>,
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
        source_name: Some("pi".into()),
        cwd,
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

fn hash_record(record: &IrRecord) -> Result<RecordHashes, TrajectoryError> {
    let semantic = match record.kind {
        RecordKind::Meta => {
            let mut value = Map::new();
            value.insert("source".into(), Value::String("pi".into()));
            if let Some(cwd) = &record.cwd {
                value.insert("cwd".into(), Value::String(cwd.clone()));
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
        input_line: Some(event.input_line),
        record_index: Some(record_index),
        count: None,
    }
}

fn quote(value: &str) -> String {
    relaxed_json(&Value::String(value.into())).expect("string serialization cannot fail")
}

#[cfg(test)]
mod tests {
    use super::{NormalizeRequest, SourceAdapter};
    use crate::{PiSourceAdapter, SourceContext};

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
}
