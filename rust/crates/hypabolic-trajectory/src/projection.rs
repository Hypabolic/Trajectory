use std::collections::BTreeMap;
use std::io::Write;

use chrono::{SecondsFormat, TimeZone as _, Utc};
use serde_json::{Map, Number, Value};
use sha2::{Digest as _, Sha256};

use crate::canonical::{canonical_json, relaxed_json};
use crate::model::{Diagnostic, IrRecord, RecordKind, Role, Trajectory, TrajectorySource};
use crate::{NORMALIZER_CONTRACT_VERSION, TrajectoryError, schema_ids};

/// Ecosystem-native typed output adapter boundary.
pub trait OutputAdapter {
    /// Adapter output.
    type Output;

    /// Language-neutral public schema ID.
    fn schema_id(&self) -> &'static str;

    /// Side-effect-free projection.
    fn project(&self, trajectory: &Trajectory) -> Result<Self::Output, TrajectoryError>;
}

/// Projects and serializes `letta-trajectory-v1`.
pub fn project_letta(trajectory: &Trajectory) -> Result<String, TrajectoryError> {
    serialize_projection(&letta_value(trajectory))
}

/// Projects and serializes `letta-canonical-v1`.
pub fn project_canonical(trajectory: &Trajectory) -> Result<String, TrajectoryError> {
    serialize_projection(&canonical_value(trajectory)?)
}

/// Projects and serializes `hypabolic-trajectory-v1`.
pub fn project_hypabolic(trajectory: &Trajectory) -> Result<String, TrajectoryError> {
    serialize_projection(&hypabolic_value(trajectory)?)
}

/// Projects and serializes `openai-chat-messages`.
pub fn project_openai(trajectory: &Trajectory) -> Result<String, TrajectoryError> {
    serialize_projection(&openai_value(trajectory))
}

/// Projects `jsonl-minimal`, including its required final newline.
pub fn project_minimal_jsonl(trajectory: &Trajectory) -> Result<String, TrajectoryError> {
    let mut output = Vec::new();
    write_minimal_jsonl(&mut output, trajectory)?;
    String::from_utf8(output)
        .map_err(|_| TrajectoryError::new("invalid_output", "Projection was not valid UTF-8."))
}

/// Projects and serializes deterministic `otel-genai-spans-v1`.
pub fn project_opentelemetry(trajectory: &Trajectory) -> Result<String, TrajectoryError> {
    serialize_projection(&opentelemetry_value(trajectory)?)
}

/// Writes an already selected projection to an ecosystem-native byte writer.
pub fn write_schema<W: Write>(
    destination: &mut W,
    trajectory: &Trajectory,
    schema_id: &str,
) -> Result<(), TrajectoryError> {
    if schema_id == schema_ids::MINIMAL_JSONL_V1 {
        return write_minimal_jsonl(destination, trajectory);
    }
    let output = project_schema(trajectory, schema_id)?;
    destination
        .write_all(output.as_bytes())
        .map_err(|error| TrajectoryError::new("io_error", error.to_string()))
}

/// Writes `jsonl-minimal` record-by-record without materializing the full output.
pub fn write_minimal_jsonl<W: Write>(
    destination: &mut W,
    trajectory: &Trajectory,
) -> Result<(), TrajectoryError> {
    for record in &trajectory.records {
        let line = relaxed_json(&minimal_record(record)?)?;
        destination
            .write_all(line.as_bytes())
            .and_then(|()| destination.write_all(b"\n"))
            .map_err(|error| TrajectoryError::new("io_error", error.to_string()))?;
    }
    Ok(())
}

/// Projects a trajectory through the JSON-oriented schema registry bridge.
pub fn project_schema(trajectory: &Trajectory, schema_id: &str) -> Result<String, TrajectoryError> {
    match schema_id {
        schema_ids::LETTA_TRAJECTORY_V1 => project_letta(trajectory),
        schema_ids::LETTA_CANONICAL_V1 => project_canonical(trajectory),
        schema_ids::HYPOBOLIC_TRAJECTORY_V1 => project_hypabolic(trajectory),
        schema_ids::OPENAI_CHAT_MESSAGES_V1 => project_openai(trajectory),
        schema_ids::MINIMAL_JSONL_V1 => project_minimal_jsonl(trajectory),
        schema_ids::OTEL_GENAI_SPANS_V1 => project_opentelemetry(trajectory),
        _ => Err(TrajectoryError::new(
            "unknown_output_schema",
            format!("No output adapter is registered for schema '{schema_id}'."),
        )),
    }
}

/// Returns `OpenAI` chat messages without metadata or reasoning records.
#[must_use]
pub fn openai_value(trajectory: &Trajectory) -> Value {
    let mut messages = Vec::new();
    for record in &trajectory.records {
        if record.kind == RecordKind::Meta || record.role == Role::Reasoning {
            continue;
        }
        match record.kind {
            RecordKind::AssistantToolCalls => messages.push(object([
                ("role", Value::String("assistant".into())),
                (
                    "tool_calls",
                    Value::Array(
                        record
                            .tool_calls
                            .iter()
                            .map(|call| {
                                object([
                                    ("id", Value::String(call.id.clone())),
                                    ("type", Value::String("function".into())),
                                    (
                                        "function",
                                        object([
                                            ("name", Value::String(call.name.clone())),
                                            (
                                                "arguments",
                                                Value::String(call.arguments_json.clone()),
                                            ),
                                        ]),
                                    ),
                                ])
                            })
                            .collect(),
                    ),
                ),
            ])),
            RecordKind::ToolResult => {
                let mut message = map([
                    ("role", Value::String("tool".into())),
                    (
                        "content",
                        Value::String(record.content.clone().unwrap_or_default()),
                    ),
                    (
                        "tool_call_id",
                        Value::String(record.tool_call_id.clone().unwrap_or_default()),
                    ),
                ]);
                if let Some(name) = &record.tool_name {
                    message.insert("name".into(), Value::String(name.clone()));
                }
                messages.push(Value::Object(message));
            }
            RecordKind::Message => messages.push(object([
                ("role", Value::String(record.role.wire_name().into())),
                (
                    "content",
                    Value::String(record.content.clone().unwrap_or_default()),
                ),
            ])),
            RecordKind::Meta => {}
        }
    }
    Value::Array(messages)
}

/// Returns the deterministic OpenTelemetry `GenAI` span-set value.
pub fn opentelemetry_value(trajectory: &Trajectory) -> Result<Value, TrajectoryError> {
    let trace_id = non_zero(
        &sha256(&format!(
            "{}|{}",
            trajectory.source_name, trajectory.group_id
        ))[..32],
    );
    let body = trajectory
        .records
        .iter()
        .filter(|record| record.kind != RecordKind::Meta)
        .collect::<Vec<_>>();
    let mut spans = Vec::new();
    let mut diagnostics = Vec::new();
    let mut turns = Vec::new();
    let users = body
        .iter()
        .enumerate()
        .filter(|(_, record)| record.role == Role::User)
        .collect::<Vec<_>>();
    for (position, (start_index, first)) in users.iter().enumerate() {
        let end_index = users
            .get(position + 1)
            .map_or(body.len(), |(index, _)| *index);
        let segment = &body[*start_index..end_index];
        let Some(last) = segment
            .iter()
            .rev()
            .find(|record| record.source_timestamp_ms.is_some())
        else {
            continue;
        };
        let (Some(start), Some(end)) = (first.source_timestamp_ms, last.source_timestamp_ms) else {
            continue;
        };
        let span_id = span_id_for(&format!("agent|{}", first.id));
        spans.push(span_value(
            &trace_id,
            &span_id,
            None,
            "invoke_agent",
            "INTERNAL",
            precise_record(first)?,
            if end < start {
                precise_record(first)?
            } else {
                precise_record(last)?
            },
            "UNSET",
            attributes([
                (
                    "gen_ai.operation.name",
                    AttributeValue::String("invoke_agent".into()),
                ),
                (
                    "gen_ai.conversation.id",
                    AttributeValue::String(trajectory.group_id.clone()),
                ),
                (
                    "hypabolic.trajectory.id",
                    AttributeValue::String(trace_id.clone()),
                ),
                (
                    "hypabolic.trajectory.source",
                    AttributeValue::String(trajectory.source_name.clone()),
                ),
                (
                    "hypabolic.trajectory.record.id",
                    AttributeValue::String(first.id.clone()),
                ),
            ]),
        ));
        turns.push((*start_index, end_index, start, end, span_id));
    }

    for invocation in &trajectory.execution.model_invocations {
        let (Some(start), Some(end)) = (invocation.started_at_ms, invocation.completed_at_ms)
        else {
            diagnostics.push(model_span_omitted(&invocation.id));
            continue;
        };
        if invocation.provider.is_none()
            && invocation.requested_model.is_none()
            && invocation.response_model.is_none()
        {
            diagnostics.push(model_span_omitted(&invocation.id));
            continue;
        }
        let parent = turns
            .iter()
            .rev()
            .find(|(_, _, turn_start, turn_end, _)| start >= *turn_start && start <= *turn_end)
            .map(|(_, _, _, _, span_id)| span_id.as_str());
        let mut model_attributes = vec![
            (
                "gen_ai.operation.name",
                AttributeValue::String("chat".into()),
            ),
            (
                "hypabolic.trajectory.invocation.id",
                AttributeValue::String(invocation.id.clone()),
            ),
        ];
        push_string_attribute(
            &mut model_attributes,
            "gen_ai.provider.name",
            invocation.provider.as_deref(),
        );
        push_string_attribute(
            &mut model_attributes,
            "gen_ai.request.model",
            invocation.requested_model.as_deref(),
        );
        push_string_attribute(
            &mut model_attributes,
            "gen_ai.response.model",
            invocation.response_model.as_deref(),
        );
        push_string_attribute(
            &mut model_attributes,
            "gen_ai.response.id",
            invocation.response_id.as_deref(),
        );
        push_string_attribute(
            &mut model_attributes,
            "hypabolic.trajectory.api_family",
            invocation.api_family.as_deref(),
        );
        if let Some(reason) = &invocation.stop_reason {
            model_attributes.push((
                "gen_ai.response.finish_reasons",
                AttributeValue::Strings(vec![reason.clone()]),
            ));
        }
        if let Some(usage) = &invocation.usage {
            push_integer_attribute(
                &mut model_attributes,
                "gen_ai.usage.input_tokens",
                usage.input_tokens,
            );
            push_integer_attribute(
                &mut model_attributes,
                "gen_ai.usage.output_tokens",
                usage.output_tokens,
            );
            push_integer_attribute(
                &mut model_attributes,
                "gen_ai.usage.cache_read.input_tokens",
                usage.cache_read_tokens,
            );
            push_integer_attribute(
                &mut model_attributes,
                "gen_ai.usage.cache_creation.input_tokens",
                usage.cache_write_tokens,
            );
        }
        let model = invocation
            .requested_model
            .as_ref()
            .or(invocation.response_model.as_ref());
        spans.push(span_value(
            &trace_id,
            &span_id_for(&format!("model|{}", invocation.id)),
            parent,
            &model.map_or_else(|| "chat".into(), |value| format!("chat {value}")),
            "CLIENT",
            precise_invocation(start, invocation.started_at_precise.as_deref())?,
            if end < start {
                precise_invocation(start, invocation.started_at_precise.as_deref())?
            } else {
                precise_invocation(end, invocation.completed_at_precise.as_deref())?
            },
            "UNSET",
            attributes(model_attributes),
        ));
    }

    let results = body
        .iter()
        .filter(|record| record.kind == RecordKind::ToolResult)
        .filter_map(|record| {
            record
                .tool_call_id
                .as_ref()
                .map(|id| (id.as_str(), *record))
        })
        .collect::<BTreeMap<_, _>>();
    for (record_index, record) in body.iter().enumerate() {
        if record.kind != RecordKind::AssistantToolCalls {
            continue;
        }
        for call in &record.tool_calls {
            let Some(result) = results.get(call.id.as_str()) else {
                continue;
            };
            let (Some(start), Some(end)) = (record.source_timestamp_ms, result.source_timestamp_ms)
            else {
                continue;
            };
            let parent = turns
                .iter()
                .rev()
                .find(|(first, last, _, _, _)| record_index >= *first && record_index < *last)
                .map(|(_, _, _, _, span_id)| span_id.as_str());
            spans.push(span_value(
                &trace_id,
                &span_id_for(&format!("tool|{}|{}", call.id, record.id)),
                parent,
                &format!("execute_tool {}", call.name),
                "INTERNAL",
                precise_record(record)?,
                if end < start {
                    precise_record(record)?
                } else {
                    precise_record(result)?
                },
                if result.is_error == Some(true) {
                    "ERROR"
                } else {
                    "UNSET"
                },
                attributes([
                    (
                        "gen_ai.operation.name",
                        AttributeValue::String("execute_tool".into()),
                    ),
                    (
                        "gen_ai.tool.name",
                        AttributeValue::String(call.name.clone()),
                    ),
                    (
                        "gen_ai.tool.call.id",
                        AttributeValue::String(call.id.clone()),
                    ),
                    (
                        "hypabolic.trajectory.call_record.id",
                        AttributeValue::String(record.id.clone()),
                    ),
                    (
                        "hypabolic.trajectory.result_record.id",
                        AttributeValue::String(result.id.clone()),
                    ),
                ]),
            ));
        }
    }
    spans.sort_by(|left, right| {
        string_field(left, "start_time")
            .cmp(string_field(right, "start_time"))
            .then_with(|| string_field(left, "name").cmp(string_field(right, "name")))
            .then_with(|| string_field(left, "span_id").cmp(string_field(right, "span_id")))
    });
    Ok(object([
        (
            "schema_url",
            Value::String("https://opentelemetry.io/schemas/gen-ai/1.42.0".into()),
        ),
        ("trace_id", Value::String(trace_id)),
        (
            "instrumentation_scope",
            Value::String("Hypabolic.Trajectory.OpenTelemetry".into()),
        ),
        ("instrumentation_version", Value::String("0.1.0".into())),
        ("resource_attributes", Value::Array(Vec::new())),
        ("spans", Value::Array(spans)),
        ("diagnostics", Value::Array(diagnostics)),
        (
            "content_policy",
            object([
                ("messages_included", Value::Bool(false)),
                ("tool_arguments_included", Value::Bool(false)),
                ("tool_results_included", Value::Bool(false)),
                ("maximum_characters", number(1_024)),
            ]),
        ),
    ]))
}

/// Returns the Letta trajectory wire value.
#[must_use]
pub fn letta_value(trajectory: &Trajectory) -> Value {
    object([
        (
            "records",
            Value::Array(trajectory.records.iter().map(to_letta_record).collect()),
        ),
        (
            "diagnostics",
            diagnostics_value(&trajectory.diagnostics, false),
        ),
    ])
}

/// Returns the canonical wire value.
pub fn canonical_value(trajectory: &Trajectory) -> Result<Value, TrajectoryError> {
    if trajectory.source == TrajectorySource::Codex && !trajectory.source_group_resolved {
        return Err(TrajectoryError::new(
            "source_group_required",
            "Canonical Codex normalization requires a source group: include session_meta or pass sourceContext.groupId.",
        ));
    }
    let records = trajectory
        .records
        .iter()
        .filter(|record| trajectory.config.base_byte_offset == 0 || record.kind != RecordKind::Meta)
        .map(|record| canonical_record(trajectory, record))
        .collect::<Result<Vec<_>, _>>()?;
    Ok(object([
        ("records", Value::Array(records)),
        (
            "diagnostics",
            diagnostics_value(&trajectory.diagnostics, false),
        ),
        (
            "normalizer_version",
            Value::String(NORMALIZER_CONTRACT_VERSION.into()),
        ),
        ("canonical_schema_version", number(1)),
        (
            "config",
            object([
                (
                    "bounds",
                    object([
                        (
                            "toolArguments",
                            object([(
                                "maxCharacters",
                                optional_usize(
                                    trajectory.config.bounds.tool_arguments_max_characters,
                                ),
                            )]),
                        ),
                        (
                            "toolResults",
                            object([
                                (
                                    "maxCharacters",
                                    optional_usize(
                                        trajectory.config.bounds.tool_results_max_characters,
                                    ),
                                ),
                                (
                                    "strategy",
                                    Value::String(
                                        trajectory
                                            .config
                                            .bounds
                                            .tool_results_strategy
                                            .wire_name()
                                            .into(),
                                    ),
                                ),
                            ]),
                        ),
                    ]),
                ),
                (
                    "filters",
                    object([(
                        "toolResults",
                        Value::String(
                            if trajectory.config.filters.include_tool_results {
                                "include"
                            } else {
                                "omit"
                            }
                            .into(),
                        ),
                    )]),
                ),
            ]),
        ),
    ]))
}

/// Returns the Hypabolic wire value.
pub fn hypabolic_value(trajectory: &Trajectory) -> Result<Value, TrajectoryError> {
    let trajectory_id = sha256(&relaxed_json(&Value::Array(vec![
        Value::String(trajectory.source.wire_name().into()),
        Value::String(trajectory.group_id.clone()),
    ]))?);
    let mut source = map([
        ("type", Value::String(trajectory.source.wire_name().into())),
        ("name", Value::String(trajectory.source_name.clone())),
        ("group_id", Value::String(trajectory.group_id.clone())),
    ]);
    if let Some(version) = &trajectory.producer_version {
        source.insert("producer_version".into(), Value::String(version.clone()));
    }
    Ok(object([
        (
            "schema_id",
            Value::String(schema_ids::HYPOBOLIC_TRAJECTORY_V1.into()),
        ),
        ("schema_version", number(1)),
        ("trajectory_id", Value::String(trajectory_id)),
        ("source", Value::Object(source)),
        (
            "segment",
            object([
                (
                    "partial",
                    Value::Bool(
                        trajectory.config.partial || trajectory.config.base_byte_offset > 0,
                    ),
                ),
                (
                    "base_byte_offset",
                    Value::Number(Number::from(trajectory.config.base_byte_offset)),
                ),
            ]),
        ),
        (
            "normalizer",
            object([
                ("name", Value::String("Hypabolic.Trajectory".into())),
                ("version", Value::String("0.1.0".into())),
            ]),
        ),
        (
            "config",
            object([
                (
                    "bounds",
                    object([
                        (
                            "tool_arguments",
                            object([(
                                "max_characters",
                                optional_usize(
                                    trajectory.config.bounds.tool_arguments_max_characters,
                                ),
                            )]),
                        ),
                        (
                            "tool_results",
                            object([
                                (
                                    "max_characters",
                                    optional_usize(
                                        trajectory.config.bounds.tool_results_max_characters,
                                    ),
                                ),
                                (
                                    "strategy",
                                    Value::String(
                                        trajectory
                                            .config
                                            .bounds
                                            .tool_results_strategy
                                            .wire_name()
                                            .into(),
                                    ),
                                ),
                            ]),
                        ),
                    ]),
                ),
                (
                    "filters",
                    object([(
                        "tool_results",
                        Value::String(
                            if trajectory.config.filters.include_tool_results {
                                "include"
                            } else {
                                "omit"
                            }
                            .into(),
                        ),
                    )]),
                ),
            ]),
        ),
        (
            "records",
            Value::Array(
                trajectory
                    .records
                    .iter()
                    .map(hypabolic_record)
                    .collect::<Result<Vec<_>, _>>()?,
            ),
        ),
        (
            "diagnostics",
            diagnostics_value(&trajectory.diagnostics, true),
        ),
    ]))
}

/// Serializes a projection using the contract's compact escaping and no final newline.
pub fn serialize_projection(value: &Value) -> Result<String, TrajectoryError> {
    relaxed_json(value)
}

pub(crate) fn to_letta_record(record: &IrRecord) -> Value {
    match record.kind {
        RecordKind::Meta => {
            let mut value = map([
                ("role", Value::String("meta".into())),
                (
                    "source",
                    Value::String(
                        record
                            .source_name
                            .clone()
                            .expect("meta source name is populated"),
                    ),
                ),
            ]);
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
            object([
                ("role", Value::String("assistant".into())),
                ("content", Value::Null),
                (
                    "tool_calls",
                    Value::Array(vec![object([
                        ("id", Value::String(call.id.clone())),
                        ("name", Value::String(call.name.clone())),
                        ("args", Value::String(call.arguments_json.clone())),
                    ])]),
                ),
                (
                    "timestamp",
                    Value::String(
                        format_ms(record.timestamp_ms.expect("body timestamp is filled"))
                            .expect("normalized timestamp is valid"),
                    ),
                ),
            ])
        }
        RecordKind::ToolResult => object([
            ("role", Value::String("tool".into())),
            (
                "tool_call_id",
                Value::String(record.tool_call_id.clone().unwrap_or_default()),
            ),
            (
                "content",
                Value::String(record.content.clone().unwrap_or_default()),
            ),
            (
                "timestamp",
                Value::String(
                    format_ms(record.timestamp_ms.expect("body timestamp is filled"))
                        .expect("normalized timestamp is valid"),
                ),
            ),
        ]),
        RecordKind::Message => object([
            ("role", Value::String(record.role.wire_name().into())),
            (
                "content",
                Value::String(record.content.clone().unwrap_or_default()),
            ),
            (
                "timestamp",
                Value::String(
                    format_ms(record.timestamp_ms.expect("body timestamp is filled"))
                        .expect("normalized timestamp is valid"),
                ),
            ),
        ]),
    }
}

fn canonical_record(trajectory: &Trajectory, record: &IrRecord) -> Result<Value, TrajectoryError> {
    let call = record.tool_calls.first();
    Ok(object([
        (
            "source_type",
            Value::String(trajectory.source.wire_name().into()),
        ),
        (
            "source_group_id",
            Value::String(trajectory.group_id.clone()),
        ),
        (
            "stable_source_record_id",
            Value::String(record.provenance.stable_source_record_id.clone()),
        ),
        (
            "source_identity_kind",
            Value::String(record.provenance.source_identity_kind.into()),
        ),
        (
            "source_order_id",
            Value::String(record.provenance.source_order_id.clone()),
        ),
        (
            "component_index",
            Value::Number(Number::from(record.provenance.component_index as u64)),
        ),
        ("record_type", Value::String(record_type(record).into())),
        ("record_id", Value::String(record.id.clone())),
        (
            "record_hash",
            Value::String(record.hashes.record_sha256.clone()),
        ),
        (
            "content_hash",
            Value::String(record.hashes.content_sha256.clone()),
        ),
        (
            "source_timestamp",
            optional_timestamp(record.source_timestamp_ms)?,
        ),
        ("record_timestamp", optional_timestamp(record.timestamp_ms)?),
        (
            "content",
            if record.kind == RecordKind::Message {
                Value::String(record.content.clone().unwrap_or_default())
            } else {
                Value::Null
            },
        ),
        (
            "tool_call_id",
            call.map_or_else(
                || {
                    record
                        .tool_call_id
                        .as_ref()
                        .map_or(Value::Null, |value| Value::String(value.clone()))
                },
                |value| Value::String(value.id.clone()),
            ),
        ),
        (
            "tool_name",
            call.map_or(Value::Null, |value| Value::String(value.name.clone())),
        ),
        (
            "tool_arguments_json",
            call.map_or(Value::Null, |value| {
                Value::String(value.arguments_json.clone())
            }),
        ),
        (
            "tool_result_json",
            if record.kind == RecordKind::ToolResult {
                Value::String(record.content.clone().unwrap_or_default())
            } else {
                Value::Null
            },
        ),
        (
            "record_json",
            Value::String(canonical_json(&to_letta_record(record))?),
        ),
    ]))
}

fn hypabolic_record(record: &IrRecord) -> Result<Value, TrajectoryError> {
    let mut output = map([
        ("id", Value::String(record.id.clone())),
        ("kind", Value::String(record.kind.wire_name().into())),
        ("role", Value::String(record.role.wire_name().into())),
        ("order", Value::Number(Number::from(record.order))),
        (
            "source_timestamp",
            optional_timestamp(record.source_timestamp_ms)?,
        ),
        ("timestamp", optional_timestamp(record.timestamp_ms)?),
    ]);
    match record.kind {
        RecordKind::Meta => {
            output.insert(
                "source_name".into(),
                Value::String(
                    record
                        .source_name
                        .clone()
                        .expect("meta source name is populated"),
                ),
            );
            if let Some(cwd) = &record.cwd {
                output.insert("cwd".into(), Value::String(cwd.clone()));
            }
            if let Some(git_branch) = &record.git_branch {
                output.insert("git_branch".into(), Value::String(git_branch.clone()));
            }
            if let Some(model) = &record.model {
                output.insert("model".into(), Value::String(model.clone()));
            }
            if let Some(version) = &record.producer_version {
                output.insert("producer_version".into(), Value::String(version.clone()));
            }
        }
        RecordKind::AssistantToolCalls => {
            output.insert("content".into(), Value::Null);
            output.insert(
                "tool_calls".into(),
                Value::Array(
                    record
                        .tool_calls
                        .iter()
                        .map(|call| {
                            object([
                                ("id", Value::String(call.id.clone())),
                                ("name", Value::String(call.name.clone())),
                                ("arguments_json", Value::String(call.arguments_json.clone())),
                            ])
                        })
                        .collect(),
                ),
            );
        }
        RecordKind::Message | RecordKind::ToolResult => {
            if let Some(content) = &record.content {
                output.insert("content".into(), Value::String(content.clone()));
            }
        }
    }
    if let Some(id) = &record.tool_call_id {
        output.insert("tool_call_id".into(), Value::String(id.clone()));
    }
    if let Some(name) = &record.tool_name {
        output.insert("tool_name".into(), Value::String(name.clone()));
    }
    if let Some(is_error) = record.is_error {
        output.insert("is_error".into(), Value::Bool(is_error));
    }
    let mut provenance = map([
        (
            "stable_source_record_id",
            Value::String(record.provenance.stable_source_record_id.clone()),
        ),
        (
            "source_identity_kind",
            Value::String(record.provenance.source_identity_kind.into()),
        ),
        (
            "source_order_id",
            Value::String(record.provenance.source_order_id.clone()),
        ),
        (
            "component_key",
            Value::String(record.provenance.component_key.clone()),
        ),
        ("component_index", number(record.provenance.component_index)),
        (
            "component_type_ordinal",
            number(record.provenance.component_type_ordinal),
        ),
    ]);
    if let Some(version) = &record.provenance.producer_version {
        provenance.insert("producer_version".into(), Value::String(version.clone()));
    }
    if let Some(native_id) = &record.provenance.native_record_id {
        provenance.insert("native_record_id".into(), Value::String(native_id.clone()));
    }
    if let Some(sequence) = record.provenance.source_sequence {
        provenance.insert("source_sequence".into(), Number::from(sequence).into());
    }
    if let Some(offset) = record.provenance.source_offset {
        provenance.insert("source_offset".into(), Number::from(offset).into());
    }
    if let Some(kind) = record.provenance.source_anchor_kind {
        provenance.insert("source_anchor_kind".into(), Value::String(kind.into()));
    }
    output.insert("provenance".into(), Value::Object(provenance));
    output.insert(
        "hashes".into(),
        object([
            (
                "content_sha256",
                Value::String(record.hashes.content_sha256.clone()),
            ),
            (
                "record_sha256",
                Value::String(record.hashes.record_sha256.clone()),
            ),
        ]),
    );
    Ok(Value::Object(output))
}

fn minimal_record(record: &IrRecord) -> Result<Value, TrajectoryError> {
    let mut value = map([
        ("id", Value::String(record.id.clone())),
        ("order", Value::Number(Number::from(record.order))),
        (
            "kind",
            Value::String(record.kind.wire_name().replace('_', "")),
        ),
        ("role", Value::String(record.role.wire_name().into())),
    ]);
    if let Some(timestamp) = record.timestamp_ms {
        value.insert(
            "timestamp".into(),
            Value::String(format_ms(timestamp)?.replace('Z', "+00:00")),
        );
    }
    if let Some(content) = &record.content {
        value.insert("content".into(), Value::String(content.clone()));
    }
    if let Some(call_id) = &record.tool_call_id {
        value.insert("tool_call_id".into(), Value::String(call_id.clone()));
    }
    if let Some(name) = &record.tool_name {
        value.insert("tool_name".into(), Value::String(name.clone()));
    }
    if let Some(is_error) = record.is_error {
        value.insert("is_error".into(), Value::Bool(is_error));
    }
    if !record.tool_calls.is_empty() {
        value.insert(
            "tool_calls".into(),
            Value::Array(
                record
                    .tool_calls
                    .iter()
                    .map(|call| {
                        object([
                            ("id", Value::String(call.id.clone())),
                            ("name", Value::String(call.name.clone())),
                            ("arguments_json", Value::String(call.arguments_json.clone())),
                        ])
                    })
                    .collect(),
            ),
        );
    }
    Ok(Value::Object(value))
}

#[derive(Debug)]
enum AttributeValue {
    String(String),
    Integer(i64),
    Strings(Vec<String>),
}

fn attributes<'a>(items: impl IntoIterator<Item = (&'a str, AttributeValue)>) -> Value {
    let sorted = items
        .into_iter()
        .map(|(key, value)| (key.to_owned(), value))
        .collect::<BTreeMap<_, _>>();
    Value::Array(
        sorted
            .into_iter()
            .map(|(key, value)| match value {
                AttributeValue::String(value) => object([
                    ("key", Value::String(key)),
                    ("string_value", Value::String(value)),
                ]),
                AttributeValue::Integer(value) => object([
                    ("key", Value::String(key)),
                    ("integer_value", Value::Number(Number::from(value))),
                ]),
                AttributeValue::Strings(value) => object([
                    ("key", Value::String(key)),
                    (
                        "string_values",
                        Value::Array(value.into_iter().map(Value::String).collect()),
                    ),
                ]),
            })
            .collect(),
    )
}

fn push_string_attribute<'a>(
    attributes: &mut Vec<(&'a str, AttributeValue)>,
    key: &'a str,
    value: Option<&str>,
) {
    if let Some(value) = value {
        attributes.push((key, AttributeValue::String(value.into())));
    }
}

fn push_integer_attribute<'a>(
    attributes: &mut Vec<(&'a str, AttributeValue)>,
    key: &'a str,
    value: Option<i64>,
) {
    if let Some(value) = value {
        attributes.push((key, AttributeValue::Integer(value)));
    }
}

fn model_span_omitted(invocation_id: &str) -> Value {
    object([
        ("code", Value::String("model_span_omitted".into())),
        (
            "message",
            Value::String(
                "Model span omitted because source-native timing or provider/model metadata is incomplete."
                    .into(),
            ),
        ),
        ("record_id", Value::String(invocation_id.into())),
    ])
}

#[allow(clippy::too_many_arguments)]
fn span_value(
    trace_id: &str,
    span_id: &str,
    parent_span_id: Option<&str>,
    name: &str,
    kind: &str,
    start_time: String,
    end_time: String,
    status: &str,
    attributes: Value,
) -> Value {
    let mut span = map([
        ("trace_id", Value::String(trace_id.into())),
        ("span_id", Value::String(span_id.into())),
    ]);
    if let Some(parent) = parent_span_id {
        span.insert("parent_span_id".into(), Value::String(parent.into()));
    }
    span.extend([
        ("name".into(), Value::String(name.into())),
        ("kind".into(), Value::String(kind.into())),
        ("start_time".into(), Value::String(start_time)),
        ("end_time".into(), Value::String(end_time)),
        ("status".into(), Value::String(status.into())),
        ("attributes".into(), attributes),
        ("links".into(), Value::Array(Vec::new())),
        ("events".into(), Value::Array(Vec::new())),
    ]);
    Value::Object(span)
}

fn precise_record(record: &IrRecord) -> Result<String, TrajectoryError> {
    if let Some(value) = &record.source_timestamp_precise {
        return Ok(value.clone());
    }
    let milliseconds = record
        .source_timestamp_ms
        .ok_or_else(|| TrajectoryError::new("invalid_input", "Source timestamp is unavailable."))?;
    let value = format_ms(milliseconds)?;
    Ok(value
        .strip_suffix('Z')
        .map_or(value.clone(), |prefix| format!("{prefix}0000+00:00")))
}

fn precise_invocation(milliseconds: i64, precise: Option<&str>) -> Result<String, TrajectoryError> {
    precise.map_or_else(
        || {
            let value = format_ms(milliseconds)?;
            Ok(value
                .strip_suffix('Z')
                .map_or(value.clone(), |prefix| format!("{prefix}0000+00:00")))
        },
        |value| Ok(value.into()),
    )
}

fn span_id_for(value: &str) -> String {
    non_zero(&sha256(value)[..16])
}

fn non_zero(value: &str) -> String {
    if value.bytes().all(|byte| byte == b'0') {
        format!("{}1", &value[..value.len() - 1])
    } else {
        value.into()
    }
}

fn string_field<'a>(value: &'a Value, name: &str) -> &'a str {
    value
        .get(name)
        .and_then(Value::as_str)
        .expect("span string field is populated")
}

fn diagnostics_value(diagnostics: &[Diagnostic], snake_case: bool) -> Value {
    Value::Array(
        diagnostics
            .iter()
            .map(|diagnostic| {
                let mut value = map([
                    ("code", Value::String(diagnostic.code.clone())),
                    ("message", Value::String(diagnostic.message.clone())),
                ]);
                if let Some(line) = diagnostic.input_line {
                    value.insert(
                        if snake_case {
                            "input_line"
                        } else {
                            "inputLine"
                        }
                        .into(),
                        number(line),
                    );
                }
                if let Some(index) = diagnostic.record_index {
                    value.insert(
                        if snake_case {
                            "record_index"
                        } else {
                            "recordIndex"
                        }
                        .into(),
                        number(index),
                    );
                }
                if let Some(count) = diagnostic.count {
                    value.insert("count".into(), number(count));
                }
                Value::Object(value)
            })
            .collect(),
    )
}

pub(crate) fn format_ms(milliseconds: i64) -> Result<String, TrajectoryError> {
    Utc.timestamp_millis_opt(milliseconds)
        .single()
        .map(|value| value.to_rfc3339_opts(SecondsFormat::Millis, true))
        .ok_or_else(|| TrajectoryError::new("invalid_input", "Timestamp is out of range."))
}

fn optional_timestamp(milliseconds: Option<i64>) -> Result<Value, TrajectoryError> {
    milliseconds.map_or(Ok(Value::Null), |value| format_ms(value).map(Value::String))
}

pub(crate) fn record_type(record: &IrRecord) -> &'static str {
    match record.kind {
        RecordKind::Meta => "meta",
        RecordKind::AssistantToolCalls => "assistant-tool-call",
        RecordKind::ToolResult => "tool",
        RecordKind::Message => match record.role {
            Role::User => "user",
            Role::Reasoning => "reasoning",
            _ => "assistant",
        },
    }
}

pub(crate) fn sha256(value: &str) -> String {
    format!("{:x}", Sha256::digest(value.as_bytes()))
}

fn optional_usize(value: Option<usize>) -> Value {
    value.map_or(Value::Null, number)
}

fn number(value: impl TryInto<u64>) -> Value {
    Value::Number(Number::from(
        value.try_into().ok().expect("wire integer fits u64"),
    ))
}

fn object<const N: usize>(entries: [(&str, Value); N]) -> Value {
    Value::Object(map(entries))
}

fn map<const N: usize>(entries: [(&str, Value); N]) -> Map<String, Value> {
    entries
        .into_iter()
        .map(|(key, value)| (key.to_owned(), value))
        .collect()
}

#[cfg(test)]
mod tests {
    use crate::{
        NormalizeOptions, NormalizeRequest, PiSourceAdapter, SourceAdapter, TruncationStrategy,
    };

    use super::{
        project_minimal_jsonl, project_openai, project_opentelemetry, write_minimal_jsonl,
        write_schema,
    };

    fn unicode_trajectory() -> crate::Trajectory {
        PiSourceAdapter
            .normalize(NormalizeRequest {
                transcript: include_bytes!(
                    "../../../../conformance/cases/pi/unicode-boundaries/input.jsonl"
                ),
                source_context: Default::default(),
                options: NormalizeOptions {
                    tool_arguments_max_characters: Some(Some(120)),
                    tool_results_max_characters: Some(Some(10)),
                    tool_results_strategy: Some(TruncationStrategy::HeadTail),
                    include_tool_results: Some(true),
                },
            })
            .expect("shared Unicode fixture normalizes")
    }

    #[test]
    fn remaining_outputs_match_shared_goldens() {
        let trajectory = unicode_trajectory();
        assert_eq!(
            project_openai(&trajectory).expect("OpenAI projection"),
            include_str!(
                "../../../../conformance/cases/pi/unicode-boundaries/expected.openai.json"
            )
            .trim_end_matches('\n')
        );
        assert_eq!(
            project_minimal_jsonl(&trajectory).expect("minimal projection"),
            include_str!(
                "../../../../conformance/cases/pi/unicode-boundaries/expected.minimal.jsonl"
            )
        );
        assert_eq!(
            project_opentelemetry(&trajectory).expect("OpenTelemetry projection"),
            include_str!("../../../../conformance/cases/pi/unicode-boundaries/expected.otel.json")
                .trim_end_matches('\n')
        );
    }

    #[test]
    fn writer_surfaces_preserve_exact_projection_bytes() {
        let trajectory = unicode_trajectory();
        let expected = project_minimal_jsonl(&trajectory).expect("minimal projection");
        let mut direct = Vec::new();
        write_minimal_jsonl(&mut direct, &trajectory).expect("direct minimal writer");
        assert_eq!(direct, expected.as_bytes());

        let mut registered = Vec::new();
        write_schema(
            &mut registered,
            &trajectory,
            crate::schema_ids::MINIMAL_JSONL_V1,
        )
        .expect("registered writer");
        assert_eq!(registered, expected.as_bytes());
    }
}
