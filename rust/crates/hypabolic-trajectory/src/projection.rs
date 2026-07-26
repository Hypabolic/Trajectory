use chrono::{SecondsFormat, TimeZone as _, Utc};
use serde_json::{Map, Number, Value};
use sha2::{Digest as _, Sha256};

use crate::canonical::{canonical_json, relaxed_json};
use crate::model::{Diagnostic, IrRecord, RecordKind, Role, Trajectory};
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

/// Projects a trajectory through the JSON-oriented schema registry bridge.
pub fn project_schema(trajectory: &Trajectory, schema_id: &str) -> Result<String, TrajectoryError> {
    match schema_id {
        schema_ids::LETTA_TRAJECTORY_V1 => project_letta(trajectory),
        schema_ids::LETTA_CANONICAL_V1 => project_canonical(trajectory),
        schema_ids::HYPOBOLIC_TRAJECTORY_V1 => project_hypabolic(trajectory),
        _ => Err(TrajectoryError::new(
            "unknown_output_schema",
            format!("No output adapter is registered for schema '{schema_id}'."),
        )),
    }
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
        Value::String("pi".into()),
        Value::String(trajectory.group_id.clone()),
    ]))?);
    let mut source = map([
        ("type", Value::String("pi".into())),
        ("name", Value::String("pi".into())),
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
                ("source", Value::String("pi".into())),
            ]);
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
        ("source_type", Value::String("pi".into())),
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
            output.insert("source_name".into(), Value::String("pi".into()));
            if let Some(cwd) = &record.cwd {
                output.insert("cwd".into(), Value::String(cwd.clone()));
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
