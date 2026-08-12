//! Live session streaming core (LS-03 / LS-04 / LS-05).
//! Pure algorithm: no filesystem watchers, network, or SQLite.

use serde_json::{Map, Value};

use crate::model::{
    NormalizeOptions, NormalizeRequest, SourceContext, TrajectoryError, TrajectorySource,
};
use crate::normalize::{
    normalize_ahp, normalize_claude_code, normalize_codex, normalize_grok_build, normalize_hermes,
    normalize_openclaw, normalize_pi,
};
use crate::projection::{hypabolic_value, sha256};

/// Wire schema id for stream snapshots and deltas.
pub const STREAM_SCHEMA_ID: &str = "trajectory-stream-v1";

/// Delivery mode for stream updates.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum StreamDelivery {
    /// Include both snapshot and delta (default).
    #[default]
    Both,
    /// Snapshot only.
    Snapshot,
    /// Delta only.
    Delta,
}

/// Public stream options.
#[derive(Debug, Clone)]
pub struct StreamOptions {
    /// Source family.
    pub source: TrajectorySource,
    /// Optional group id hint.
    pub group_id: Option<String>,
    /// Delivery preference.
    pub delivery: StreamDelivery,
    /// Include provisional records.
    pub include_provisional: bool,
    /// Require LF-terminated lines for ordinary apply.
    pub require_complete_lines: bool,
    /// Finalize records on finish.
    pub finalize_on_close: bool,
    /// Normalize options.
    pub normalize: NormalizeOptions,
    /// Optional pending buffer limit.
    pub max_pending_bytes: Option<i64>,
    /// Optional line limit.
    pub max_line_bytes: Option<i64>,
}

impl StreamOptions {
    /// Create options for a source.
    #[must_use]
    pub fn new(source: TrajectorySource) -> Self {
        Self {
            source,
            group_id: None,
            delivery: StreamDelivery::Both,
            include_provisional: true,
            require_complete_lines: true,
            finalize_on_close: true,
            normalize: NormalizeOptions::default(),
            max_pending_bytes: None,
            max_line_bytes: None,
        }
    }

    /// Set group id.
    #[must_use]
    pub fn with_group_id(mut self, group_id: impl Into<String>) -> Self {
        self.group_id = Some(group_id.into());
        self
    }
}

/// Byte cursor position.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BytePosition {
    /// Offset after last committed complete record.
    pub next_byte_offset: i64,
    /// Pending incomplete byte length.
    pub pending_byte_length: i64,
}

/// Public stream cursor.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StreamCursor {
    /// Wire cursor version.
    pub cursor_version: u32,
    /// Source wire name.
    pub source: String,
    /// Group id.
    pub group_id: String,
    /// Generation.
    pub generation: u64,
    /// Byte position.
    pub position: BytePosition,
    /// Host source revision.
    pub source_revision: Option<String>,
    /// Prefix fingerprint.
    pub prefix_sha256: Option<String>,
}

/// Stream revision metadata.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StreamRevision {
    /// Monotonic revision within generation.
    pub revision: u64,
    /// Deterministic revision id.
    pub revision_id: String,
    /// Parent revision id.
    pub parent_revision_id: Option<String>,
    /// Stream complete flag.
    pub complete: bool,
    /// Generation.
    pub generation: u64,
}

/// Stream diagnostic (snake_case wire fields).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StreamDiagnostic {
    /// Code.
    pub code: String,
    /// Content-safe message.
    pub message: String,
    /// Optional input line.
    pub input_line: Option<i64>,
    /// Optional record index.
    pub record_index: Option<i64>,
    /// Optional count.
    pub count: Option<i64>,
}

/// Stream record wrapper.
#[derive(Debug, Clone, PartialEq)]
pub struct StreamRecord {
    /// Lifecycle status.
    pub status: String,
    /// Hypabolic-shaped record body.
    pub record: Value,
    /// Optional provisional id.
    pub provisional_id: Option<String>,
    /// Optional id of the provisional record this replaces.
    pub replaces_provisional_id: Option<String>,
    /// Optional id of the provisional record this finalizes.
    pub finalizes_provisional_id: Option<String>,
}

/// Stream snapshot.
#[derive(Debug, Clone, PartialEq)]
pub struct StreamSnapshot {
    /// Schema id.
    pub schema_id: String,
    /// Source.
    pub source: String,
    /// Group id.
    pub group_id: String,
    /// Revision.
    pub revision: StreamRevision,
    /// Records.
    pub records: Vec<StreamRecord>,
    /// Diagnostics.
    pub diagnostics: Vec<StreamDiagnostic>,
    /// Complete flag.
    pub complete: bool,
}

/// Stream delta operation.
#[derive(Debug, Clone, PartialEq)]
pub struct StreamDeltaOperation {
    /// Operation kind.
    pub op: String,
    /// Payload fields.
    pub payload: Map<String, Value>,
}

/// Stream delta.
#[derive(Debug, Clone, PartialEq)]
pub struct StreamDelta {
    /// Schema id.
    pub schema_id: String,
    /// Base revision id.
    pub base_revision_id: Option<String>,
    /// Revision.
    pub revision: StreamRevision,
    /// Ordered operations.
    pub operations: Vec<StreamDeltaOperation>,
}

/// Stream reset metadata.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StreamReset {
    /// Reason.
    pub reason: String,
    /// Prior cursor.
    pub prior_cursor: Option<StreamCursor>,
    /// Requires snapshot.
    pub requires_snapshot: bool,
    /// Dropped record ids.
    pub dropped_record_ids: Vec<String>,
}

/// Caller reset request.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StreamResetRequest {
    /// Reason.
    pub reason: String,
    /// Optional explicit generation (defaults to prior + 1).
    pub generation: Option<u64>,
    /// Host source revision after reset.
    pub source_revision: Option<String>,
    /// Optional prior cursor override.
    pub prior_cursor: Option<StreamCursor>,
    /// Optional material to install immediately after reset.
    pub material: Option<Vec<u8>>,
}

/// Stream input kind for pure `apply(state, input)`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StreamInputKind {
    /// Append complete-line segment.
    AppendBytes,
    /// Full snapshot material.
    SnapshotBytes,
    /// End-of-stream finish.
    Finish,
    /// Explicit generation reset.
    Reset,
    /// AHP action batch (stub until LS-05+).
    AhpActions,
    /// AHP Shape A snapshot (stub until LS-05+).
    AhpSnapshot,
    /// Hermes export (requires optional provider).
    HermesExport,
}

/// Pure apply input envelope.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StreamInput {
    /// Input kind.
    pub kind: StreamInputKind,
    /// Bytes payload (append/snapshot).
    pub data: Option<Vec<u8>>,
    /// Source revision.
    pub source_revision: Option<String>,
    /// Optional cursor check.
    pub cursor: Option<StreamCursor>,
    /// Reset request when kind is Reset.
    pub reset: Option<StreamResetRequest>,
}

/// Provisional lifecycle summary on a stream update envelope.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct StreamProvisionalInfo {
    /// Whether provisional records are included.
    pub include: bool,
    /// Provisional ids present in this update's snapshot.
    pub provisional_ids: Vec<String>,
    /// Provisional ids finalized in this update.
    pub finalized_ids: Vec<String>,
}

/// Consumed-progress summary on a stream update envelope.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct StreamConsumed {
    /// Complete records produced this apply.
    pub complete_records: u64,
    /// Bytes of committed material this apply.
    pub bytes: u64,
    /// First source byte position (inclusive), when material non-empty.
    pub first_source_position: Option<i64>,
    /// Last source byte position (inclusive), when material non-empty.
    pub last_source_position: Option<i64>,
}

/// Stream update envelope.
#[derive(Debug, Clone, PartialEq)]
pub struct StreamUpdate {
    /// Update kind.
    pub kind: String,
    /// Revision.
    pub revision: StreamRevision,
    /// Cursor.
    pub cursor: StreamCursor,
    /// Snapshot.
    pub snapshot: Option<StreamSnapshot>,
    /// Delta.
    pub delta: Option<StreamDelta>,
    /// Diagnostics.
    pub diagnostics: Vec<StreamDiagnostic>,
    /// Provisional lifecycle summary.
    pub provisional: StreamProvisionalInfo,
    /// Consumed-progress summary.
    pub consumed: StreamConsumed,
    /// Reset metadata.
    pub reset: Option<StreamReset>,
    /// Error.
    pub error: Option<(String, String)>,
}

/// Runtime-local stream state.
#[derive(Debug, Clone)]
pub struct StreamState {
    /// Options.
    pub options: StreamOptions,
    /// Cursor.
    pub cursor: StreamCursor,
    /// Pending incomplete bytes.
    pub pending_bytes: Vec<u8>,
    /// Committed complete-line prefix.
    pub committed_prefix: Vec<u8>,
    /// Last snapshot.
    pub snapshot: Option<StreamSnapshot>,
    /// Generation.
    pub generation: u64,
    /// Next revision number.
    pub next_revision: u64,
    /// Finished flag.
    pub finished: bool,
    /// Whether group is locked from material.
    pub group_locked: bool,
    /// Last accepted append-bytes segment (raw input). Re-supply is idempotent.
    pub last_append_segment: Option<Vec<u8>>,
}

/// Split complete LF-terminated lines from a pending tail.
#[must_use]
pub fn split_complete_lines(data: &[u8]) -> (Vec<u8>, Vec<u8>) {
    if data.is_empty() {
        return (Vec::new(), Vec::new());
    }
    let Some(last_lf) = data.iter().rposition(|b| *b == b'\n') else {
        return (Vec::new(), data.to_vec());
    };
    (data[..=last_lf].to_vec(), data[last_lf + 1..].to_vec())
}

/// Normative match key for a stream record (dict form).
pub fn match_key_value(record: &Value) -> Result<String, TrajectoryError> {
    if let Some(pid) = record.get("provisional_id").and_then(Value::as_str) {
        if !pid.is_empty() {
            return Ok(pid.to_string());
        }
    }
    record
        .get("record")
        .and_then(|body| body.get("id"))
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .ok_or_else(|| TrajectoryError::new("invalid_input", "stream record missing match key"))
}

/// Diagnostic key encoding.
#[must_use]
pub fn diagnostic_key(code: &str, input_line: Option<i64>, record_index: Option<i64>) -> String {
    let line = input_line.map_or_else(|| "-".to_string(), |n| n.to_string());
    let index = record_index.map_or_else(|| "-".to_string(), |n| n.to_string());
    format!("{code}|{line}|{index}")
}

fn record_to_value(r: &StreamRecord) -> Value {
    let mut map = Map::new();
    map.insert("status".into(), Value::String(r.status.clone()));
    map.insert("record".into(), r.record.clone());
    if let Some(pid) = &r.provisional_id {
        map.insert("provisional_id".into(), Value::String(pid.clone()));
    }
    if let Some(id) = &r.replaces_provisional_id {
        map.insert("replaces_provisional_id".into(), Value::String(id.clone()));
    }
    if let Some(id) = &r.finalizes_provisional_id {
        map.insert("finalizes_provisional_id".into(), Value::String(id.clone()));
    }
    Value::Object(map)
}

fn diagnostic_to_value(d: &StreamDiagnostic) -> Value {
    let mut map = Map::new();
    map.insert("code".into(), Value::String(d.code.clone()));
    map.insert("message".into(), Value::String(d.message.clone()));
    if let Some(line) = d.input_line {
        map.insert("input_line".into(), Value::from(line));
    }
    if let Some(index) = d.record_index {
        map.insert("record_index".into(), Value::from(index));
    }
    if let Some(count) = d.count {
        map.insert("count".into(), Value::from(count));
    }
    Value::Object(map)
}

fn revision_to_value(r: &StreamRevision) -> Value {
    let mut map = Map::new();
    map.insert("revision".into(), Value::from(r.revision));
    map.insert("revision_id".into(), Value::String(r.revision_id.clone()));
    map.insert(
        "parent_revision_id".into(),
        r.parent_revision_id
            .as_ref()
            .map_or(Value::Null, |s| Value::String(s.clone())),
    );
    map.insert("complete".into(), Value::Bool(r.complete));
    map.insert("generation".into(), Value::from(r.generation));
    Value::Object(map)
}

/// Serialize snapshot to JSON value.
#[must_use]
pub fn snapshot_to_value(s: &StreamSnapshot) -> Value {
    let mut map = Map::new();
    map.insert("schema_id".into(), Value::String(s.schema_id.clone()));
    map.insert("source".into(), Value::String(s.source.clone()));
    map.insert("group_id".into(), Value::String(s.group_id.clone()));
    map.insert("revision".into(), revision_to_value(&s.revision));
    map.insert(
        "records".into(),
        Value::Array(s.records.iter().map(record_to_value).collect()),
    );
    map.insert(
        "diagnostics".into(),
        Value::Array(s.diagnostics.iter().map(diagnostic_to_value).collect()),
    );
    map.insert("complete".into(), Value::Bool(s.complete));
    Value::Object(map)
}

/// Serialize stream update to JSON value (wire snake_case).
#[must_use]
pub fn update_to_value(u: &StreamUpdate) -> Value {
    let mut map = Map::new();
    map.insert("kind".into(), Value::String(u.kind.clone()));
    map.insert("revision".into(), revision_to_value(&u.revision));
    map.insert("cursor".into(), cursor_to_value(&u.cursor));
    map.insert(
        "snapshot".into(),
        u.snapshot
            .as_ref()
            .map_or(Value::Null, snapshot_to_value),
    );
    map.insert(
        "delta".into(),
        u.delta.as_ref().map_or(Value::Null, delta_to_value),
    );
    map.insert(
        "diagnostics".into(),
        Value::Array(u.diagnostics.iter().map(diagnostic_to_value).collect()),
    );
    let mut provisional = Map::new();
    provisional.insert("include".into(), Value::Bool(u.provisional.include));
    provisional.insert(
        "provisional_ids".into(),
        Value::Array(
            u.provisional
                .provisional_ids
                .iter()
                .cloned()
                .map(Value::String)
                .collect(),
        ),
    );
    provisional.insert(
        "finalized_ids".into(),
        Value::Array(
            u.provisional
                .finalized_ids
                .iter()
                .cloned()
                .map(Value::String)
                .collect(),
        ),
    );
    map.insert("provisional".into(), Value::Object(provisional));
    let mut consumed = Map::new();
    consumed.insert(
        "complete_records".into(),
        Value::from(u.consumed.complete_records),
    );
    consumed.insert("bytes".into(), Value::from(u.consumed.bytes));
    if let Some(pos) = u.consumed.first_source_position {
        consumed.insert("first_source_position".into(), Value::from(pos));
    }
    if let Some(pos) = u.consumed.last_source_position {
        consumed.insert("last_source_position".into(), Value::from(pos));
    }
    map.insert("consumed".into(), Value::Object(consumed));
    if let Some(reset) = &u.reset {
        map.insert("reset".into(), reset_to_value(reset));
    }
    if let Some((code, message)) = &u.error {
        let mut err = Map::new();
        err.insert("code".into(), Value::String(code.clone()));
        err.insert("message".into(), Value::String(message.clone()));
        map.insert("error".into(), Value::Object(err));
    }
    Value::Object(map)
}

/// Serialize delta to JSON value.
#[must_use]
pub fn delta_to_value(d: &StreamDelta) -> Value {
    let mut map = Map::new();
    map.insert("schema_id".into(), Value::String(d.schema_id.clone()));
    map.insert(
        "base_revision_id".into(),
        d.base_revision_id
            .as_ref()
            .map_or(Value::Null, |s| Value::String(s.clone())),
    );
    map.insert("revision".into(), revision_to_value(&d.revision));
    let ops = d
        .operations
        .iter()
        .map(|op| {
            let mut m = op.payload.clone();
            m.insert("op".into(), Value::String(op.op.clone()));
            Value::Object(m)
        })
        .collect();
    map.insert("operations".into(), Value::Array(ops));
    Value::Object(map)
}

/// Diff two snapshots into ordered delta ops.
#[must_use]
pub fn diff_snapshots(
    prior: Option<&StreamSnapshot>,
    current: &StreamSnapshot,
    revision: &StreamRevision,
) -> StreamDelta {
    let prior_records = prior.map(|s| s.records.as_slice()).unwrap_or(&[]);
    let mut prior_keys: Vec<(String, &StreamRecord)> = prior_records
        .iter()
        .filter_map(|r| {
            r.record
                .get("id")
                .and_then(Value::as_str)
                .map(|id| (r.provisional_id.clone().unwrap_or_else(|| id.to_string()), r))
        })
        .collect();
    prior_keys.sort_by(|a, b| a.0.cmp(&b.0));
    let curr_map: std::collections::BTreeMap<String, &StreamRecord> = current
        .records
        .iter()
        .filter_map(|r| {
            r.record
                .get("id")
                .and_then(Value::as_str)
                .map(|id| (r.provisional_id.clone().unwrap_or_else(|| id.to_string()), r))
        })
        .collect();

    let mut operations = Vec::new();
    for (key, _) in &prior_keys {
        if !curr_map.contains_key(key) {
            let mut payload = Map::new();
            payload.insert("record_id".into(), Value::String(key.clone()));
            payload.insert("reason".into(), Value::String("source-rewrite".into()));
            operations.push(StreamDeltaOperation {
                op: "remove".into(),
                payload,
            });
        }
    }
    for rec in &current.records {
        let key = rec
            .provisional_id
            .clone()
            .or_else(|| {
                rec.record
                    .get("id")
                    .and_then(Value::as_str)
                    .map(str::to_string)
            })
            .unwrap_or_default();
        let prev = prior_keys.iter().find(|(k, _)| k == &key).map(|(_, r)| *r);
        if prev.is_none_or(|p| p.record != rec.record || p.status != rec.status) {
            if prev.is_some_and(|p| p.record == rec.record && p.status != rec.status) {
                let mut payload = Map::new();
                payload.insert("record_id".into(), Value::String(key));
                payload.insert("status".into(), Value::String(rec.status.clone()));
                operations.push(StreamDeltaOperation {
                    op: "state_change".into(),
                    payload,
                });
            } else {
                let mut payload = Map::new();
                payload.insert("record".into(), record_to_value(rec));
                operations.push(StreamDeltaOperation {
                    op: "upsert".into(),
                    payload,
                });
            }
        }
    }

    let prior_diags: std::collections::BTreeMap<String, &StreamDiagnostic> = prior
        .map(|s| s.diagnostics.as_slice())
        .unwrap_or(&[])
        .iter()
        .map(|d| {
            (
                diagnostic_key(&d.code, d.input_line, d.record_index),
                d,
            )
        })
        .collect();
    let curr_diags: std::collections::BTreeMap<String, &StreamDiagnostic> = current
        .diagnostics
        .iter()
        .map(|d| {
            (
                diagnostic_key(&d.code, d.input_line, d.record_index),
                d,
            )
        })
        .collect();
    for key in prior_diags.keys() {
        if !curr_diags.contains_key(key) {
            let mut payload = Map::new();
            payload.insert("diagnostic_key".into(), Value::String(key.clone()));
            operations.push(StreamDeltaOperation {
                op: "diagnostic_remove".into(),
                payload,
            });
        }
    }
    for (key, d) in &curr_diags {
        let changed = prior_diags
            .get(key)
            .is_none_or(|prev| diagnostic_to_value(prev) != diagnostic_to_value(d));
        if changed {
            let mut payload = Map::new();
            payload.insert("diagnostic".into(), diagnostic_to_value(d));
            operations.push(StreamDeltaOperation {
                op: "diagnostic_add".into(),
                payload,
            });
        }
    }

    StreamDelta {
        schema_id: STREAM_SCHEMA_ID.into(),
        base_revision_id: prior.map(|s| s.revision.revision_id.clone()),
        revision: revision.clone(),
        operations,
    }
}

/// Apply delta ops to a prior snapshot value (delta-apply law).
pub fn apply_delta_to_snapshot(
    prior: Option<&Value>,
    delta: &Value,
) -> Result<Value, TrajectoryError> {
    let mut base = prior.cloned().unwrap_or_else(|| {
        let mut m = Map::new();
        m.insert("schema_id".into(), Value::String(STREAM_SCHEMA_ID.into()));
        m.insert("records".into(), Value::Array(vec![]));
        m.insert("diagnostics".into(), Value::Array(vec![]));
        Value::Object(m)
    });
    let mut records = base
        .get("records")
        .and_then(Value::as_array)
        .cloned()
        .ok_or_else(|| TrajectoryError::new("invalid_input", "snapshot records malformed"))?;
    let mut diagnostics = base
        .get("diagnostics")
        .and_then(Value::as_array)
        .cloned()
        .ok_or_else(|| TrajectoryError::new("invalid_input", "snapshot diagnostics malformed"))?;

    let ops = delta
        .get("operations")
        .and_then(Value::as_array)
        .ok_or_else(|| TrajectoryError::new("invalid_input", "delta.operations required"))?;

    for op in ops {
        let kind = op
            .get("op")
            .and_then(Value::as_str)
            .ok_or_else(|| TrajectoryError::new("invalid_input", "operation.op required"))?;
        match kind {
            "upsert" => {
                let entry = op
                    .get("record")
                    .cloned()
                    .ok_or_else(|| TrajectoryError::new("invalid_input", "upsert requires record"))?;
                let key = match_key_value(&entry)?;
                if let Some(slot) = records
                    .iter_mut()
                    .find(|r| match_key_value(r).ok().as_deref() == Some(key.as_str()))
                {
                    *slot = entry;
                } else {
                    records.push(entry);
                }
            }
            "remove" => {
                let rid = op
                    .get("record_id")
                    .and_then(Value::as_str)
                    .ok_or_else(|| TrajectoryError::new("invalid_input", "remove requires record_id"))?
                    .to_string();
                records.retain(|r| match_key_value(r).ok().as_deref() != Some(rid.as_str()));
            }
            "state_change" => {
                let rid = op
                    .get("record_id")
                    .and_then(Value::as_str)
                    .ok_or_else(|| {
                        TrajectoryError::new("invalid_input", "state_change requires record_id")
                    })?;
                let status = op
                    .get("status")
                    .and_then(Value::as_str)
                    .ok_or_else(|| {
                        TrajectoryError::new("invalid_input", "state_change requires status")
                    })?;
                if let Some(slot) = records
                    .iter_mut()
                    .find(|r| match_key_value(r).ok().as_deref() == Some(rid))
                {
                    if let Some(obj) = slot.as_object_mut() {
                        obj.insert("status".into(), Value::String(status.into()));
                    }
                }
            }
            "diagnostic_add" => {
                let d = op.get("diagnostic").cloned().ok_or_else(|| {
                    TrajectoryError::new("invalid_input", "diagnostic_add requires diagnostic")
                })?;
                let code = d.get("code").and_then(Value::as_str).unwrap_or("");
                let line = d.get("input_line").and_then(Value::as_i64);
                let index = d.get("record_index").and_then(Value::as_i64);
                let key = diagnostic_key(code, line, index);
                diagnostics.retain(|x| {
                    let c = x.get("code").and_then(Value::as_str).unwrap_or("");
                    let l = x.get("input_line").and_then(Value::as_i64);
                    let i = x.get("record_index").and_then(Value::as_i64);
                    diagnostic_key(c, l, i) != key
                });
                diagnostics.push(d);
            }
            "diagnostic_remove" => {
                let key = op
                    .get("diagnostic_key")
                    .and_then(Value::as_str)
                    .unwrap_or("");
                diagnostics.retain(|x| {
                    let c = x.get("code").and_then(Value::as_str).unwrap_or("");
                    let l = x.get("input_line").and_then(Value::as_i64);
                    let i = x.get("record_index").and_then(Value::as_i64);
                    diagnostic_key(c, l, i) != key
                });
            }
            "finalize" => {
                let pid = op
                    .get("provisional_id")
                    .and_then(Value::as_str)
                    .unwrap_or("");
                records.retain(|r| {
                    r.get("provisional_id").and_then(Value::as_str) != Some(pid)
                        && match_key_value(r).ok().as_deref() != Some(pid)
                });
                if let Some(entry) = op.get("record").cloned() {
                    let key = match_key_value(&entry)?;
                    if let Some(slot) = records
                        .iter_mut()
                        .find(|r| match_key_value(r).ok().as_deref() == Some(key.as_str()))
                    {
                        *slot = entry;
                    } else {
                        records.push(entry);
                    }
                }
            }
            "reset" => {
                records.clear();
                diagnostics.clear();
            }
            _ => {}
        }
    }

    if let Some(obj) = base.as_object_mut() {
        obj.insert("records".into(), Value::Array(records));
        obj.insert("diagnostics".into(), Value::Array(diagnostics));
        if let Some(rev) = delta.get("revision") {
            obj.insert("revision".into(), rev.clone());
            if let Some(complete) = rev.get("complete") {
                obj.insert("complete".into(), complete.clone());
            }
        }
    }
    Ok(base)
}

/// Create a new stream state.
#[must_use]
pub fn create_stream(options: StreamOptions) -> StreamState {
    let group_id = options
        .group_id
        .clone()
        .unwrap_or_else(|| "default".into());
    let source = options.source.wire_name().to_string();
    StreamState {
        cursor: StreamCursor {
            cursor_version: 1,
            source,
            group_id: group_id.clone(),
            generation: 0,
            position: BytePosition {
                next_byte_offset: 0,
                pending_byte_length: 0,
            },
            source_revision: None,
            prefix_sha256: None,
        },
        options,
        pending_bytes: Vec::new(),
        committed_prefix: Vec::new(),
        snapshot: None,
        generation: 0,
        next_revision: 0,
        finished: false,
        group_locked: false,
        last_append_segment: None,
    }
}

fn normalize_source(
    source: TrajectorySource,
    request: NormalizeRequest<'_>,
) -> Result<crate::model::Trajectory, TrajectoryError> {
    match source {
        TrajectorySource::Pi => normalize_pi(request),
        TrajectorySource::ClaudeCode => normalize_claude_code(request),
        TrajectorySource::Codex => normalize_codex(request),
        TrajectorySource::OpenClaw => normalize_openclaw(request),
        TrajectorySource::Hermes => normalize_hermes(request),
        TrajectorySource::Ahp => normalize_ahp(request),
        TrajectorySource::GrokBuild => normalize_grok_build(request),
    }
}

fn revision_id(
    generation: u64,
    revision: u64,
    source: &str,
    group_id: &str,
    prefix_sha: &str,
    record_ids: &[String],
) -> String {
    sha256(&format!(
        "{generation}|{revision}|{source}|{group_id}|{prefix_sha}|{}",
        record_ids.join(",")
    ))
}

fn empty_provisional(state: &StreamState) -> StreamProvisionalInfo {
    StreamProvisionalInfo {
        include: state.options.include_provisional,
        provisional_ids: vec![],
        finalized_ids: vec![],
    }
}

fn empty_consumed() -> StreamConsumed {
    StreamConsumed {
        complete_records: 0,
        bytes: 0,
        first_source_position: None,
        last_source_position: None,
    }
}

fn unchanged(state: &StreamState) -> StreamUpdate {
    let revision = state.snapshot.as_ref().map_or_else(
        || StreamRevision {
            revision: 0,
            revision_id: "unchanged".into(),
            parent_revision_id: None,
            complete: state.finished,
            generation: state.generation,
        },
        |s| s.revision.clone(),
    );
    StreamUpdate {
        kind: "unchanged".into(),
        revision,
        cursor: state.cursor.clone(),
        snapshot: None,
        delta: None,
        diagnostics: vec![],
        provisional: empty_provisional(state),
        consumed: empty_consumed(),
        reset: None,
        error: None,
    }
}

fn reset_required(state: &StreamState, reason: &str, code: &str, message: &str) -> StreamUpdate {
    let revision = state.snapshot.as_ref().map_or_else(
        || StreamRevision {
            revision: 0,
            revision_id: "reset-required".into(),
            parent_revision_id: None,
            complete: false,
            generation: state.generation,
        },
        |s| s.revision.clone(),
    );
    let dropped = state
        .snapshot
        .as_ref()
        .map(|s| {
            s.records
                .iter()
                .filter_map(|r| r.record.get("id").and_then(Value::as_str).map(str::to_string))
                .collect()
        })
        .unwrap_or_default();
    StreamUpdate {
        kind: "reset-required".into(),
        revision,
        cursor: state.cursor.clone(),
        snapshot: None,
        delta: None,
        diagnostics: vec![StreamDiagnostic {
            code: code.into(),
            message: message.into(),
            input_line: None,
            record_index: None,
            count: None,
        }],
        provisional: empty_provisional(state),
        consumed: empty_consumed(),
        reset: Some(StreamReset {
            reason: reason.into(),
            prior_cursor: Some(state.cursor.clone()),
            requires_snapshot: true,
            dropped_record_ids: dropped,
        }),
        error: None,
    }
}

fn error_update(state: &StreamState, code: &str, message: &str) -> StreamUpdate {
    let revision = state.snapshot.as_ref().map_or_else(
        || StreamRevision {
            revision: 0,
            revision_id: "error".into(),
            parent_revision_id: None,
            complete: false,
            generation: state.generation,
        },
        |s| s.revision.clone(),
    );
    StreamUpdate {
        kind: "error".into(),
        revision,
        cursor: state.cursor.clone(),
        snapshot: None,
        delta: None,
        diagnostics: vec![],
        provisional: empty_provisional(state),
        consumed: empty_consumed(),
        reset: None,
        error: Some((code.into(), message.into())),
    }
}

/// True when any complete LF-terminated line in `data` exceeds `max_line_bytes`.
fn any_line_too_long(data: &[u8], max_line_bytes: i64) -> bool {
    let mut start = 0usize;
    for (i, b) in data.iter().enumerate() {
        if *b == b'\n' {
            let line_len = (i - start + 1) as i64;
            if line_len > max_line_bytes {
                return true;
            }
            start = i + 1;
        }
    }
    false
}

/// Convert a byte length into non-negative int64 domain, or error message.
fn len_as_i64(len: usize) -> Result<i64, &'static str> {
    i64::try_from(len).map_err(|_| "Stream material length exceeds non-negative int64 domain.")
}

fn is_whitespace_only(data: &[u8]) -> bool {
    data.iter()
        .all(|b| matches!(b, b' ' | b'\t' | b'\r' | b'\n'))
}

fn reset_to_value(reset: &StreamReset) -> Value {
    let mut map = Map::new();
    map.insert("reason".into(), Value::String(reset.reason.clone()));
    map.insert(
        "prior_cursor".into(),
        reset
            .prior_cursor
            .as_ref()
            .map_or(Value::Null, cursor_to_value),
    );
    map.insert(
        "requires_snapshot".into(),
        Value::Bool(reset.requires_snapshot),
    );
    map.insert(
        "dropped_record_ids".into(),
        Value::Array(
            reset
                .dropped_record_ids
                .iter()
                .cloned()
                .map(Value::String)
                .collect(),
        ),
    );
    Value::Object(map)
}

fn cursor_to_value(c: &StreamCursor) -> Value {
    let mut pos = Map::new();
    pos.insert("kind".into(), Value::String("byte".into()));
    pos.insert(
        "next_byte_offset".into(),
        Value::from(c.position.next_byte_offset),
    );
    pos.insert(
        "pending_byte_length".into(),
        Value::from(c.position.pending_byte_length),
    );
    let mut map = Map::new();
    map.insert("cursor_version".into(), Value::from(c.cursor_version));
    map.insert("source".into(), Value::String(c.source.clone()));
    map.insert("group_id".into(), Value::String(c.group_id.clone()));
    map.insert("generation".into(), Value::from(c.generation));
    map.insert("position".into(), Value::Object(pos));
    map.insert(
        "source_revision".into(),
        c.source_revision
            .as_ref()
            .map_or(Value::Null, |s| Value::String(s.clone())),
    );
    map.insert(
        "prefix_sha256".into(),
        c.prefix_sha256
            .as_ref()
            .map_or(Value::Null, |s| Value::String(s.clone())),
    );
    Value::Object(map)
}

fn cursor_conflict(state: &StreamState, cursor: Option<&StreamCursor>) -> Option<StreamUpdate> {
    let cursor = cursor?;
    if cursor.source != state.cursor.source || cursor.generation != state.cursor.generation {
        return Some(reset_required(
            state,
            "cursor-mismatch",
            "stream_cursor_conflict",
            "Supplied stream cursor does not match stream state.",
        ));
    }
    if state.group_locked && cursor.group_id != state.cursor.group_id {
        return Some(reset_required(
            state,
            "group-changed",
            "stream_cursor_conflict",
            "Supplied stream cursor does not match stream state.",
        ));
    }
    // Domain: non-negative int64 byte positions (streaming-cursor-v1).
    // Checked before position equality so out-of-domain offsets are invalid_input,
    // not cursor-mismatch (parity with Python/TS).
    if cursor.position.next_byte_offset < 0 || cursor.position.pending_byte_length < 0 {
        return Some(error_update(
            state,
            "invalid_input",
            "Stream cursor byte positions must be non-negative int64 values.",
        ));
    }
    if cursor.position.next_byte_offset != state.cursor.position.next_byte_offset {
        return Some(reset_required(
            state,
            "cursor-mismatch",
            "stream_cursor_conflict",
            "Supplied stream cursor does not match stream state.",
        ));
    }
    None
}

/// Apply a full snapshot of source material.
///
/// Optional `cursor` is compared against stream state; mismatch yields
/// `kind=reset-required` with `reason=cursor-mismatch` and leaves state unchanged.
pub fn apply_snapshot(
    state: &StreamState,
    material: &[u8],
    source_revision: &str,
    cursor: Option<&StreamCursor>,
) -> Result<(StreamState, StreamUpdate), TrajectoryError> {
    if state.finished {
        return Ok((
            state.clone(),
            error_update(state, "invalid_input", "Stream is already finished."),
        ));
    }

    if let Some(conflict) = cursor_conflict(state, cursor) {
        return Ok((state.clone(), conflict));
    }

    let (committed, pending) = if state.options.require_complete_lines {
        split_complete_lines(material)
    } else {
        (material.to_vec(), Vec::new())
    };

    let committed_len = match len_as_i64(committed.len()) {
        Ok(n) => n,
        Err(msg) => {
            return Ok((state.clone(), error_update(state, "invalid_input", msg)));
        }
    };
    let pending_len = match len_as_i64(pending.len()) {
        Ok(n) => n,
        Err(msg) => {
            return Ok((state.clone(), error_update(state, "invalid_input", msg)));
        }
    };

    if let Some(max) = state.options.max_pending_bytes {
        if max < 0 {
            return Ok((
                state.clone(),
                error_update(
                    state,
                    "invalid_input",
                    "Stream buffer limits must be non-negative int64 values.",
                ),
            ));
        }
        if pending_len > max {
            return Ok((
                state.clone(),
                error_update(state, "stream_buffer_limit", "Stream buffer limit exceeded."),
            ));
        }
    }

    if let Some(max) = state.options.max_line_bytes {
        if max < 0 {
            return Ok((
                state.clone(),
                error_update(
                    state,
                    "invalid_input",
                    "Stream buffer limits must be non-negative int64 values.",
                ),
            ));
        }
        if any_line_too_long(&committed, max) || pending_len > max {
            return Ok((
                state.clone(),
                error_update(state, "stream_buffer_limit", "Stream buffer limit exceeded."),
            ));
        }
    }

    let group_hint = if state.group_locked {
        Some(state.cursor.group_id.clone())
    } else {
        state.options.group_id.clone()
    };

    let (mut records, diagnostics, group_id) = if committed.is_empty() {
        (
            Vec::new(),
            Vec::new(),
            group_hint.unwrap_or_else(|| state.cursor.group_id.clone()),
        )
    } else {
        let group_ref = group_hint.as_deref();
        let request = NormalizeRequest {
            transcript: &committed,
            source_context: SourceContext {
                group_id: group_ref,
                base_byte_offset: 0,
                partial: true,
                include_encrypted_reasoning: false,
            },
            options: state.options.normalize,
        };
        let trajectory = match normalize_source(state.options.source, request) {
            Ok(t) => t,
            Err(err) if err.code == "source_group_conflict" => {
                return Ok((
                    state.clone(),
                    reset_required(
                        state,
                        "group-changed",
                        "stream_source_reset",
                        "Source group changed relative to the active stream.",
                    ),
                ));
            }
            Err(err) => {
                return Ok((state.clone(), error_update(state, &err.code, &err.message)));
            }
        };
        let hyp = hypabolic_value(&trajectory)?;
        let raw_records = hyp
            .get("records")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let has_backend_synth = trajectory
            .diagnostics
            .iter()
            .any(|d| d.code == "backend_tool_result_synthesized");
        let mark_provisional =
            has_backend_synth && state.options.source == TrajectorySource::GrokBuild;
        let records: Vec<StreamRecord> = raw_records
            .into_iter()
            .map(|record| {
                let mut status = "stable".to_string();
                let mut provisional_id = None;
                if mark_provisional && is_synthetic_backend_tool_result(&record) {
                    status = "provisional".into();
                    provisional_id = record
                        .get("id")
                        .and_then(Value::as_str)
                        .map(str::to_string);
                }
                StreamRecord {
                    status,
                    record,
                    provisional_id,
                    replaces_provisional_id: None,
                    finalizes_provisional_id: None,
                }
            })
            .collect();
        let diagnostics: Vec<StreamDiagnostic> = trajectory
            .diagnostics
            .iter()
            .map(|d| StreamDiagnostic {
                code: d.code.clone(),
                message: d.message.clone(),
                input_line: d.input_line.map(|n| n as i64),
                record_index: d.record_index.map(|n| n as i64),
                count: d.count.map(|n| n as i64),
            })
            .collect();
        (records, diagnostics, trajectory.group_id)
    };

    if !state.options.include_provisional {
        records.retain(|r| r.status != "provisional");
    }

    if state.snapshot.is_some() && committed_len < state.cursor.position.next_byte_offset {
        let (reason, message) = shrink_reset_reason(state, &committed);
        return Ok((
            state.clone(),
            reset_required(state, reason, "stream_source_reset", message),
        ));
    }

    // sha256 of empty string over UTF-8 — use bytes:
    let effective_prefix_sha = if committed.is_empty() {
        sha256_bytes(&[])
    } else {
        sha256_bytes(&committed)
    };

    if state.snapshot.is_some()
        && state.cursor.source_revision.as_deref() == Some(source_revision)
        && state.cursor.prefix_sha256.as_deref() == Some(effective_prefix_sha.as_str())
        && state.pending_bytes == pending
    {
        return Ok((state.clone(), unchanged(state)));
    }

    let mut new_state = state.clone();
    new_state.group_locked = true;
    let generation = new_state.generation;
    let parent_revision_id = new_state
        .snapshot
        .as_ref()
        .map(|s| s.revision.revision_id.clone());
    let revision_num = new_state.next_revision;
    let record_ids: Vec<String> = records
        .iter()
        .filter_map(|r| r.record.get("id").and_then(Value::as_str).map(str::to_string))
        .collect();
    let rev_id = revision_id(
        generation,
        revision_num,
        new_state.cursor.source.as_str(),
        &group_id,
        &effective_prefix_sha,
        &record_ids,
    );
    let revision = StreamRevision {
        revision: revision_num,
        revision_id: rev_id,
        parent_revision_id,
        complete: false,
        generation,
    };
    let provisional_ids: Vec<String> = records
        .iter()
        .filter_map(|r| r.provisional_id.clone())
        .collect();
    let snapshot = StreamSnapshot {
        schema_id: STREAM_SCHEMA_ID.into(),
        source: new_state.cursor.source.clone(),
        group_id: group_id.clone(),
        revision: revision.clone(),
        records: records.clone(),
        diagnostics: diagnostics.clone(),
        complete: false,
    };
    let delta = diff_snapshots(new_state.snapshot.as_ref(), &snapshot, &revision);
    let (out_snapshot, out_delta) = match new_state.options.delivery {
        StreamDelivery::Both => (Some(snapshot.clone()), Some(delta)),
        StreamDelivery::Snapshot => (Some(snapshot.clone()), None),
        StreamDelivery::Delta => (None, Some(delta)),
    };
    let cursor = StreamCursor {
        cursor_version: 1,
        source: new_state.cursor.source.clone(),
        group_id,
        generation,
        position: BytePosition {
            next_byte_offset: committed_len,
            pending_byte_length: pending_len,
        },
        source_revision: Some(source_revision.into()),
        prefix_sha256: Some(effective_prefix_sha),
    };
    let update = StreamUpdate {
        kind: "updated".into(),
        revision,
        cursor: cursor.clone(),
        snapshot: out_snapshot,
        delta: out_delta,
        diagnostics,
        provisional: StreamProvisionalInfo {
            include: state.options.include_provisional,
            provisional_ids,
            finalized_ids: vec![],
        },
        consumed: StreamConsumed {
            complete_records: records.len() as u64,
            bytes: committed_len as u64,
            first_source_position: if committed.is_empty() { None } else { Some(0) },
            last_source_position: if committed.is_empty() {
                None
            } else {
                Some(committed_len - 1)
            },
        },
        reset: None,
        error: None,
    };
    new_state.cursor = cursor;
    new_state.snapshot = Some(snapshot);
    new_state.pending_bytes = pending;
    new_state.committed_prefix = committed;
    new_state.next_revision = revision_num + 1;
    // Snapshot replaces committed material; clear append-replay fingerprint.
    new_state.last_append_segment = None;
    Ok((new_state, update))
}

/// Append complete-line segment for file JSONL sources.
///
/// Frames against the pending buffer, extends the committed prefix, then
/// re-normalizes the full committed prefix (oracle path). Append equals
/// full-prefix snapshot on every shared fixture. The oracle path *is* the
/// steady-state implementation (O(committed_prefix)); no separate incremental
/// decoder requires a performance fallback in this slice.
pub fn apply_append(
    state: &StreamState,
    segment: &[u8],
    cursor: Option<&StreamCursor>,
    source_revision: Option<&str>,
) -> Result<(StreamState, StreamUpdate), TrajectoryError> {
    if state.finished {
        return Ok((
            state.clone(),
            error_update(state, "invalid_input", "Stream is already finished."),
        ));
    }
    if let Some(conflict) = cursor_conflict(state, cursor) {
        return Ok((state.clone(), conflict));
    }
    if let Some(max) = state.options.max_pending_bytes {
        if max < 0 {
            return Ok((
                state.clone(),
                error_update(
                    state,
                    "invalid_input",
                    "Stream buffer limits must be non-negative int64 values.",
                ),
            ));
        }
    }
    if let Some(max) = state.options.max_line_bytes {
        if max < 0 {
            return Ok((
                state.clone(),
                error_update(
                    state,
                    "invalid_input",
                    "Stream buffer limits must be non-negative int64 values.",
                ),
            ));
        }
    }

    if segment.is_empty() && state.pending_bytes.is_empty() {
        return Ok((state.clone(), unchanged(state)));
    }

    // Replay of already-accepted append input is idempotent (unchanged).
    if state
        .last_append_segment
        .as_ref()
        .is_some_and(|last| last.as_slice() == segment)
    {
        return Ok((state.clone(), unchanged(state)));
    }

    let mut combined = state.pending_bytes.clone();
    combined.extend_from_slice(segment);
    let (complete, new_pending) = split_complete_lines(&combined);
    if let Some(max) = state.options.max_pending_bytes {
        let pending_len = match len_as_i64(new_pending.len()) {
            Ok(n) => n,
            Err(msg) => {
                return Ok((state.clone(), error_update(state, "invalid_input", msg)));
            }
        };
        if pending_len > max {
            return Ok((
                state.clone(),
                error_update(state, "stream_buffer_limit", "Stream buffer limit exceeded."),
            ));
        }
    }
    if let Some(max) = state.options.max_line_bytes {
        let pending_len = match len_as_i64(new_pending.len()) {
            Ok(n) => n,
            Err(msg) => {
                return Ok((state.clone(), error_update(state, "invalid_input", msg)));
            }
        };
        if any_line_too_long(&complete, max) || pending_len > max {
            return Ok((
                state.clone(),
                error_update(state, "stream_buffer_limit", "Stream buffer limit exceeded."),
            ));
        }
    }

    let pending_len = match len_as_i64(new_pending.len()) {
        Ok(n) => n,
        Err(msg) => {
            return Ok((state.clone(), error_update(state, "invalid_input", msg)));
        }
    };

    // No complete lines: only pending advanced (incomplete line / mid-UTF-8).
    // Visible records unchanged → kind=unchanged with patched pending cursor.
    if complete.is_empty() {
        if new_pending == state.pending_bytes {
            return Ok((state.clone(), unchanged(state)));
        }
        let mut new_state = state.clone();
        new_state.pending_bytes = new_pending;
        new_state.last_append_segment = Some(segment.to_vec());
        new_state.cursor.position.pending_byte_length = pending_len;
        let update = unchanged(&new_state);
        return Ok((new_state, update));
    }

    let mut new_prefix = state.committed_prefix.clone();
    new_prefix.extend_from_slice(&complete);
    let mut tmp = state.clone();
    tmp.pending_bytes.clear();
    let rev = source_revision
        .map(str::to_string)
        .or_else(|| state.cursor.source_revision.clone())
        .unwrap_or_default();
    let (mut new_state, mut update) = apply_snapshot(&tmp, &new_prefix, &rev, None)?;
    // Failure-atomic: failed/reset snapshot leaves prior state and pending intact.
    if update.kind != "updated" && update.kind != "unchanged" {
        return Ok((state.clone(), update));
    }

    new_state.pending_bytes = new_pending;
    new_state.last_append_segment = Some(segment.to_vec());
    new_state.cursor.position.pending_byte_length = pending_len;
    // Always copy patched cursor onto StreamUpdate (updated and unchanged).
    update.cursor = new_state.cursor.clone();
    if update.kind == "updated" {
        let prior_len = state.committed_prefix.len() as i64;
        let complete_len = complete.len() as i64;
        update.consumed = StreamConsumed {
            complete_records: update.consumed.complete_records,
            bytes: complete_len as u64,
            first_source_position: if complete_len > 0 {
                Some(prior_len)
            } else {
                None
            },
            last_source_position: if complete_len > 0 {
                Some(prior_len + complete_len - 1)
            } else {
                None
            },
        };
    }
    Ok((new_state, update))
}

fn shrink_reset_reason(state: &StreamState, committed: &[u8]) -> (&'static str, &'static str) {
    if state.committed_prefix.starts_with(committed) {
        return (
            "source-truncated",
            "Source material is shorter than the committed cursor.",
        );
    }
    if state.options.source == TrajectorySource::GrokBuild {
        return (
            "source-compacted",
            "Source material was compacted relative to the committed cursor.",
        );
    }
    (
        "source-replaced",
        "Source material was replaced relative to the committed cursor.",
    )
}

fn is_synthetic_backend_tool_result(record: &Value) -> bool {
    let role = record.get("role").and_then(Value::as_str);
    let content = record.get("content").and_then(Value::as_str);
    matches!((role, content), (Some("tool"), Some(c)) if c.starts_with("[backend "))
}

/// End-of-stream: optionally commit final unterminated line; finalize records.
pub fn finish_stream(
    state: &StreamState,
) -> Result<(StreamState, StreamUpdate), TrajectoryError> {
    if state.finished {
        return Ok((state.clone(), unchanged(state)));
    }

    let mut material = state.committed_prefix.clone();
    let pending = state.pending_bytes.clone();
    if !pending.is_empty() && !is_whitespace_only(&pending) {
        material.extend_from_slice(&pending);
        material.push(b'\n');
    }

    let rev = state
        .cursor
        .source_revision
        .clone()
        .unwrap_or_else(|| "finish".into());
    let (mid_state, mid_update) = apply_snapshot(state, &material, &rev, None)?;
    if mid_update.kind != "updated" && mid_update.kind != "unchanged" {
        return Ok((mid_state, mid_update));
    }

    let Some(base_snapshot) = mid_state.snapshot.clone() else {
        let mut finished = mid_state;
        finished.finished = true;
        return Ok((finished, mid_update));
    };

    let finalized: Vec<StreamRecord> = if state.options.finalize_on_close {
        base_snapshot
            .records
            .iter()
            .map(|rec| {
                if rec.status == "final" {
                    rec.clone()
                } else {
                    StreamRecord {
                        status: "final".into(),
                        record: rec.record.clone(),
                        provisional_id: rec.provisional_id.clone(),
                        replaces_provisional_id: rec.replaces_provisional_id.clone(),
                        finalizes_provisional_id: rec
                            .finalizes_provisional_id
                            .clone()
                            .or_else(|| rec.provisional_id.clone()),
                    }
                }
            })
            .collect()
    } else {
        base_snapshot.records.clone()
    };

    let generation = mid_state.generation;
    let parent_revision_id = Some(base_snapshot.revision.revision_id.clone());
    let revision_num = mid_state.next_revision;
    let prefix_sha = mid_state
        .cursor
        .prefix_sha256
        .clone()
        .unwrap_or_else(|| sha256_bytes(&[]));
    let record_ids: Vec<String> = finalized
        .iter()
        .filter_map(|r| r.record.get("id").and_then(Value::as_str).map(str::to_string))
        .collect();
    let rev_id = revision_id(
        generation,
        revision_num,
        mid_state.cursor.source.as_str(),
        &base_snapshot.group_id,
        &prefix_sha,
        &record_ids,
    );
    let revision = StreamRevision {
        revision: revision_num,
        revision_id: rev_id,
        parent_revision_id,
        complete: true,
        generation,
    };
    let snapshot = StreamSnapshot {
        schema_id: STREAM_SCHEMA_ID.into(),
        source: base_snapshot.source.clone(),
        group_id: base_snapshot.group_id.clone(),
        revision: revision.clone(),
        records: finalized.clone(),
        diagnostics: base_snapshot.diagnostics.clone(),
        complete: true,
    };
    let delta = diff_snapshots(Some(&base_snapshot), &snapshot, &revision);
    let (out_snapshot, out_delta) = match state.options.delivery {
        StreamDelivery::Both => (Some(snapshot.clone()), Some(delta)),
        StreamDelivery::Snapshot => (Some(snapshot.clone()), None),
        StreamDelivery::Delta => (None, Some(delta)),
    };
    let material_len = match len_as_i64(material.len()) {
        Ok(n) => n,
        Err(msg) => {
            return Ok((state.clone(), error_update(state, "invalid_input", msg)));
        }
    };
    let mut new_state = mid_state;
    new_state.finished = true;
    new_state.pending_bytes.clear();
    new_state.committed_prefix = material;
    new_state.snapshot = Some(snapshot.clone());
    new_state.cursor = StreamCursor {
        cursor_version: 1,
        source: new_state.cursor.source.clone(),
        group_id: snapshot.group_id.clone(),
        generation,
        position: BytePosition {
            next_byte_offset: material_len,
            pending_byte_length: 0,
        },
        source_revision: new_state.cursor.source_revision.clone(),
        prefix_sha256: Some(if new_state.committed_prefix.is_empty() {
            sha256_bytes(&[])
        } else {
            sha256_bytes(&new_state.committed_prefix)
        }),
    };
    new_state.next_revision = revision_num + 1;
    let update = StreamUpdate {
        kind: "updated".into(),
        revision,
        cursor: new_state.cursor.clone(),
        snapshot: out_snapshot,
        delta: out_delta,
        diagnostics: snapshot.diagnostics,
        provisional: StreamProvisionalInfo {
            include: state.options.include_provisional,
            provisional_ids: vec![],
            finalized_ids: finalized
                .iter()
                .filter_map(|r| r.provisional_id.clone())
                .collect(),
        },
        consumed: StreamConsumed {
            complete_records: finalized.len() as u64,
            bytes: material_len as u64,
            first_source_position: None,
            last_source_position: None,
        },
        reset: None,
        error: None,
    };
    Ok((new_state, update))
}

/// Install a new generation after reset-required or manual restart.
pub fn reset_stream(
    state: &StreamState,
    request: &StreamResetRequest,
) -> Result<(StreamState, StreamUpdate), TrajectoryError> {
    let generation = request.generation.unwrap_or(state.generation + 1);
    let group_id = state
        .options
        .group_id
        .clone()
        .unwrap_or_else(|| state.cursor.group_id.clone());
    let mut new_state = state.clone();
    new_state.generation = generation;
    new_state.next_revision = 0;
    new_state.finished = false;
    new_state.pending_bytes.clear();
    new_state.committed_prefix.clear();
    new_state.snapshot = None;
    new_state.group_locked = false;
    new_state.last_append_segment = None;
    new_state.cursor = StreamCursor {
        cursor_version: 1,
        source: state.cursor.source.clone(),
        group_id: group_id.clone(),
        generation,
        position: BytePosition {
            next_byte_offset: 0,
            pending_byte_length: 0,
        },
        source_revision: request.source_revision.clone(),
        prefix_sha256: None,
    };

    let dropped: Vec<String> = state
        .snapshot
        .as_ref()
        .map(|s| {
            s.records
                .iter()
                .filter_map(|r| r.record.get("id").and_then(Value::as_str).map(str::to_string))
                .collect()
        })
        .unwrap_or_default();
    let reset_meta = StreamReset {
        reason: request.reason.clone(),
        prior_cursor: request
            .prior_cursor
            .clone()
            .or_else(|| Some(state.cursor.clone())),
        requires_snapshot: request.material.is_none(),
        dropped_record_ids: dropped,
    };

    if let Some(material) = &request.material {
        let rev = request.source_revision.clone().unwrap_or_default();
        let (applied, mut update) = apply_snapshot(&new_state, material, &rev, None)?;
        if update.kind != "updated" && update.kind != "unchanged" {
            return Ok((applied, update));
        }
        if let Some(delta) = update.delta.as_mut() {
            let mut payload = Map::new();
            payload.insert("reset".into(), reset_to_value(&reset_meta));
            delta.operations.insert(
                0,
                StreamDeltaOperation {
                    op: "reset".into(),
                    payload,
                },
            );
        }
        update.reset = Some(reset_meta);
        return Ok((applied, update));
    }

    // Empty reset with no material → updated empty snapshot of new generation.
    let empty_sha = sha256_bytes(&[]);
    let revision = StreamRevision {
        revision: 0,
        revision_id: revision_id(
            generation,
            0,
            new_state.cursor.source.as_str(),
            &group_id,
            &empty_sha,
            &[],
        ),
        parent_revision_id: None,
        complete: false,
        generation,
    };
    let snapshot = StreamSnapshot {
        schema_id: STREAM_SCHEMA_ID.into(),
        source: new_state.cursor.source.clone(),
        group_id: group_id.clone(),
        revision: revision.clone(),
        records: vec![],
        diagnostics: vec![],
        complete: false,
    };
    let mut delta = diff_snapshots(None, &snapshot, &revision);
    let mut payload = Map::new();
    payload.insert("reset".into(), reset_to_value(&reset_meta));
    delta.operations.insert(
        0,
        StreamDeltaOperation {
            op: "reset".into(),
            payload,
        },
    );
    let (out_snapshot, out_delta) = match state.options.delivery {
        StreamDelivery::Both => (Some(snapshot.clone()), Some(delta)),
        StreamDelivery::Snapshot => (Some(snapshot.clone()), None),
        StreamDelivery::Delta => (None, Some(delta)),
    };
    new_state.snapshot = Some(snapshot);
    new_state.next_revision = 1;
    new_state.cursor = StreamCursor {
        cursor_version: 1,
        source: new_state.cursor.source.clone(),
        group_id,
        generation,
        position: BytePosition {
            next_byte_offset: 0,
            pending_byte_length: 0,
        },
        source_revision: request.source_revision.clone(),
        prefix_sha256: Some(empty_sha),
    };
    let update = StreamUpdate {
        kind: "updated".into(),
        revision,
        cursor: new_state.cursor.clone(),
        snapshot: out_snapshot,
        delta: out_delta,
        diagnostics: vec![],
        provisional: empty_provisional(state),
        consumed: empty_consumed(),
        reset: Some(reset_meta),
        error: None,
    };
    Ok((new_state, update))
}

/// Pure apply(state, input) → (state, update).
pub fn apply_stream(
    state: &StreamState,
    input: &StreamInput,
) -> Result<(StreamState, StreamUpdate), TrajectoryError> {
    match input.kind {
        StreamInputKind::SnapshotBytes => apply_snapshot(
            state,
            input.data.as_deref().unwrap_or(&[]),
            input.source_revision.as_deref().unwrap_or(""),
            input.cursor.as_ref(),
        ),
        StreamInputKind::AppendBytes => apply_append(
            state,
            input.data.as_deref().unwrap_or(&[]),
            input.cursor.as_ref(),
            input.source_revision.as_deref(),
        ),
        StreamInputKind::Finish => finish_stream(state),
        StreamInputKind::Reset => {
            let Some(request) = input.reset.as_ref() else {
                return Ok((
                    state.clone(),
                    error_update(
                        state,
                        "invalid_input",
                        "reset input requires a StreamResetRequest.",
                    ),
                ));
            };
            reset_stream(state, request)
        }
        StreamInputKind::AhpActions | StreamInputKind::AhpSnapshot => Ok((
            state.clone(),
            error_update(
                state,
                "stream_resync_required",
                "AHP stream apply is not available in this slice.",
            ),
        )),
        StreamInputKind::HermesExport => Ok((
            state.clone(),
            error_update(
                state,
                "stream_resync_required",
                "Hermes export stream apply requires an optional provider.",
            ),
        )),
    }
}

/// Mutable façade over [`StreamState`] for API parity with other runtimes.
#[derive(Debug, Clone)]
pub struct TrajectoryStream {
    state: StreamState,
}

impl TrajectoryStream {
    /// Create a new stream façade from options.
    #[must_use]
    pub fn create(options: StreamOptions) -> Self {
        Self {
            state: create_stream(options),
        }
    }

    /// Current cursor.
    #[must_use]
    pub fn cursor(&self) -> &StreamCursor {
        &self.state.cursor
    }

    /// Borrow the underlying pure state.
    #[must_use]
    pub fn state(&self) -> &StreamState {
        &self.state
    }

    /// Apply a full snapshot of source material.
    pub fn apply_snapshot(
        &mut self,
        material: &[u8],
        source_revision: &str,
        cursor: Option<&StreamCursor>,
    ) -> Result<StreamUpdate, TrajectoryError> {
        let (state, update) = apply_snapshot(&self.state, material, source_revision, cursor)?;
        self.state = state;
        Ok(update)
    }

    /// Append a complete-line segment.
    pub fn apply_append(
        &mut self,
        segment: &[u8],
        cursor: Option<&StreamCursor>,
        source_revision: Option<&str>,
    ) -> Result<StreamUpdate, TrajectoryError> {
        let (state, update) = apply_append(&self.state, segment, cursor, source_revision)?;
        self.state = state;
        Ok(update)
    }

    /// End-of-stream finish.
    pub fn finish(&mut self) -> Result<StreamUpdate, TrajectoryError> {
        let (state, update) = finish_stream(&self.state)?;
        self.state = state;
        Ok(update)
    }

    /// Explicit generation reset.
    pub fn reset(&mut self, request: &StreamResetRequest) -> Result<StreamUpdate, TrajectoryError> {
        let (state, update) = reset_stream(&self.state, request)?;
        self.state = state;
        Ok(update)
    }

    /// Pure apply envelope.
    pub fn apply(&mut self, input: &StreamInput) -> Result<StreamUpdate, TrajectoryError> {
        let (state, update) = apply_stream(&self.state, input)?;
        self.state = state;
        Ok(update)
    }
}

fn sha256_bytes(data: &[u8]) -> String {
    use sha2::{Digest as _, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(data);
    format!("{:x}", hasher.finalize())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::PathBuf;

    fn cases_root() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../../conformance/cases/streaming")
    }

    fn read_case(case: &str, name: &str) -> Vec<u8> {
        fs::read(cases_root().join(case).join(name)).expect("fixture")
    }

    #[test]
    fn empty_prefix_and_idempotent() {
        let opts = StreamOptions::new(TrajectorySource::Pi).with_group_id("stream-empty-prefix");
        let state = create_stream(opts);
        let (state, update) = apply_snapshot(&state, b"", "gen-0", None).unwrap();
        assert_eq!(update.kind, "updated");
        assert!(update.snapshot.as_ref().unwrap().records.is_empty());
        assert!(update.delta.is_some());
        assert!(update.provisional.include);
        assert_eq!(update.consumed.complete_records, 0);
        let (_, update2) = apply_snapshot(&state, b"", "gen-0", None).unwrap();
        assert_eq!(update2.kind, "unchanged");
    }

    #[test]
    fn snapshot_delta_equivalence() {
        let a = read_case("snapshot-delta-equivalence", "step-a.jsonl");
        let b = read_case("snapshot-delta-equivalence", "step-b.jsonl");
        let opts = StreamOptions::new(TrajectorySource::Pi)
            .with_group_id("stream-snapshot-delta-equivalence");
        let state = create_stream(opts);
        let (state, u1) = apply_snapshot(&state, &a, "gen-0", None).unwrap();
        assert_eq!(u1.kind, "updated");
        let prior = snapshot_to_value(u1.snapshot.as_ref().unwrap());
        let (_, u2) = apply_snapshot(&state, &b, "gen-0", None).unwrap();
        assert_eq!(u2.kind, "updated");
        let delta = delta_to_value(u2.delta.as_ref().unwrap());
        let recon = apply_delta_to_snapshot(Some(&prior), &delta).unwrap();
        let snap = snapshot_to_value(u2.snapshot.as_ref().unwrap());
        assert_eq!(recon.get("records"), snap.get("records"));
    }

    #[test]
    fn group_conflict_and_truncate() {
        let m1 = read_case("source-group-conflict", "step-matching.jsonl");
        let m2 = read_case("source-group-conflict", "step-foreign-group.jsonl");
        let opts =
            StreamOptions::new(TrajectorySource::Pi).with_group_id("stream-expected-group");
        let state = create_stream(opts);
        let (state, u1) = apply_snapshot(&state, &m1, "gen-0", None).unwrap();
        assert_eq!(u1.kind, "updated");
        let (state2, u2) = apply_snapshot(&state, &m2, "gen-0", None).unwrap();
        assert_eq!(u2.kind, "reset-required");
        assert_eq!(
            u2.reset.as_ref().unwrap().reason,
            "group-changed"
        );
        assert_eq!(
            state2.cursor.position.next_byte_offset,
            state.cursor.position.next_byte_offset
        );

        let long = read_case("file-truncate-reset", "step-long.jsonl");
        let short = read_case("file-truncate-reset", "step-truncated.jsonl");
        let opts = StreamOptions::new(TrajectorySource::Pi)
            .with_group_id("stream-file-truncate-reset");
        let state = create_stream(opts);
        let (state, _) = apply_snapshot(&state, &long, "gen-0", None).unwrap();
        let (_, u) = apply_snapshot(&state, &short, "gen-0", None).unwrap();
        assert_eq!(u.kind, "reset-required");
        assert_eq!(u.reset.as_ref().unwrap().reason, "source-truncated");
    }

    #[test]
    fn cursor_mismatch_atomic() {
        let opts = StreamOptions::new(TrajectorySource::Pi).with_group_id("g");
        let state = create_stream(opts);
        let (state, _) = apply_snapshot(&state, b"", "gen-0", None).unwrap();
        let mut bad = state.cursor.clone();
        bad.position.next_byte_offset = 99;
        let (state2, update) = apply_snapshot(&state, b"", "gen-0", Some(&bad)).unwrap();
        assert_eq!(update.kind, "reset-required");
        assert_eq!(update.reset.as_ref().unwrap().reason, "cursor-mismatch");
        assert_eq!(
            state2.cursor.position.next_byte_offset,
            state.cursor.position.next_byte_offset
        );
    }

    #[test]
    fn negative_next_byte_offset_is_invalid_input() {
        let opts = StreamOptions::new(TrajectorySource::Pi).with_group_id("g");
        let state = create_stream(opts);
        let (state, _) = apply_snapshot(&state, b"", "gen-0", None).unwrap();
        let mut bad = state.cursor.clone();
        bad.position.next_byte_offset = -1;
        let (state2, update) = apply_snapshot(&state, b"", "gen-0", Some(&bad)).unwrap();
        assert_eq!(update.kind, "error");
        assert_eq!(
            update.error.as_ref().map(|(c, _)| c.as_str()),
            Some("invalid_input")
        );
        assert_eq!(
            state2.cursor.position.next_byte_offset,
            state.cursor.position.next_byte_offset
        );
    }

    #[test]
    fn max_line_bytes_rejected() {
        let mut opts = StreamOptions::new(TrajectorySource::Pi).with_group_id("g");
        opts.max_line_bytes = Some(4);
        let state = create_stream(opts);
        let material = b"{\"a\":1}\n";
        let (_, update) = apply_snapshot(&state, material, "gen-0", None).unwrap();
        assert_eq!(update.kind, "error");
        assert_eq!(
            update.error.as_ref().map(|(c, _)| c.as_str()),
            Some("stream_buffer_limit")
        );
    }

    #[test]
    fn framing_holds_unterminated() {
        let (c, p) = split_complete_lines(b"{\"a\":1}\n{\"b\":");
        assert_eq!(c, b"{\"a\":1}\n");
        assert_eq!(p, b"{\"b\":");
    }

    #[test]
    fn reset_with_material_attaches_reset_envelope() {
        let long = read_case("file-truncate-reset", "step-long.jsonl");
        let short = read_case("file-truncate-reset", "step-truncated.jsonl");
        let opts = StreamOptions::new(TrajectorySource::Pi)
            .with_group_id("stream-file-truncate-reset");
        let state = create_stream(opts);
        let (state, _) = apply_snapshot(&state, &long, "gen-0", None).unwrap();
        let request = StreamResetRequest {
            reason: "source-truncated".into(),
            generation: Some(1),
            source_revision: Some("gen-1".into()),
            prior_cursor: None,
            material: Some(short),
        };
        let (state2, update) = reset_stream(&state, &request).unwrap();
        assert_eq!(update.kind, "updated");
        assert_eq!(state2.generation, 1);
        assert_eq!(state2.cursor.generation, 1);
        let reset = update.reset.as_ref().expect("reset envelope");
        assert_eq!(reset.reason, "source-truncated");
        assert!(!reset.requires_snapshot);
    }

    #[test]
    fn negative_max_line_bytes_is_invalid_input() {
        let mut opts = StreamOptions::new(TrajectorySource::Pi).with_group_id("g");
        opts.max_line_bytes = Some(-1);
        let state = create_stream(opts);
        let (_, update) = apply_snapshot(&state, b"{\"a\":1}\n", "gen-0", None).unwrap();
        assert_eq!(update.kind, "error");
        assert_eq!(
            update.error.as_ref().map(|(c, _)| c.as_str()),
            Some("invalid_input")
        );
    }

    #[test]
    fn finish_marks_complete() {
        let opts = StreamOptions::new(TrajectorySource::Pi).with_group_id("g");
        let state = create_stream(opts);
        let (state, _) = apply_snapshot(&state, b"", "gen-0", None).unwrap();
        let (state2, update) = finish_stream(&state).unwrap();
        assert_eq!(update.kind, "updated");
        assert!(state2.finished);
        assert!(update.revision.complete);
    }

    #[test]
    fn apply_append_pending_only_advances_cursor() {
        let incomplete = read_case("unterminated-line-held", "step-incomplete.txt");
        let opts =
            StreamOptions::new(TrajectorySource::Pi).with_group_id("stream-unterminated-line-held");
        let state = create_stream(opts);
        let (state, update) = apply_append(&state, &incomplete, None, Some("gen-0")).unwrap();
        assert_eq!(update.kind, "unchanged");
        assert_eq!(
            state.cursor.position.pending_byte_length,
            incomplete.len() as i64
        );
        assert_eq!(
            update.cursor.position.pending_byte_length,
            incomplete.len() as i64
        );
        assert_eq!(state.pending_bytes, incomplete);

        let partial = read_case("utf8-byte-boundary", "step-partial-utf8.bin");
        let tail = read_case("utf8-byte-boundary", "step-utf8-tail.bin");
        let opts =
            StreamOptions::new(TrajectorySource::Pi).with_group_id("stream-utf8-byte-boundary");
        let state = create_stream(opts);
        let (state, update) = apply_append(&state, &partial, None, Some("gen-0")).unwrap();
        assert_eq!(update.kind, "unchanged");
        assert_eq!(
            update.cursor.position.pending_byte_length,
            partial.len() as i64
        );
        let (state, update) = apply_append(&state, &tail, None, Some("gen-0")).unwrap();
        assert_eq!(update.kind, "updated");
        assert_eq!(update.cursor.position.pending_byte_length, 0);
        assert_eq!(state.cursor.position.pending_byte_length, 0);
    }

    #[test]
    fn apply_append_enforces_buffer_limits() {
        let mut opts = StreamOptions::new(TrajectorySource::Pi).with_group_id("g");
        opts.max_pending_bytes = Some(5);
        let state = create_stream(opts);
        let (_, update) = apply_append(&state, b"{\"a\":1", None, Some("gen-0")).unwrap();
        assert_eq!(update.kind, "error");
        assert_eq!(
            update.error.as_ref().map(|(c, _)| c.as_str()),
            Some("stream_buffer_limit")
        );
    }

    #[test]
    fn append_equals_prefix_oracle() {
        let c1 = read_case("append-equals-prefix-oracle", "step-chunk-1.jsonl");
        let c2 = read_case("append-equals-prefix-oracle", "step-chunk-2.jsonl");
        let opts = StreamOptions::new(TrajectorySource::Pi)
            .with_group_id("stream-append-equals-prefix-oracle");
        let state = create_stream(opts);
        let (state, a1) = apply_append(&state, &c1, None, Some("gen-0")).unwrap();
        assert_eq!(a1.kind, "updated");
        let (state, a2) = apply_append(&state, &c2, None, Some("gen-0")).unwrap();
        assert_eq!(a2.kind, "updated");
        let append_ids: Vec<_> = a2
            .snapshot
            .as_ref()
            .unwrap()
            .records
            .iter()
            .filter_map(|r| r.record.get("id").and_then(Value::as_str).map(str::to_string))
            .collect();

        let mut full = c1.clone();
        full.extend_from_slice(&c2);
        let opts = StreamOptions::new(TrajectorySource::Pi)
            .with_group_id("stream-append-equals-prefix-oracle");
        let oracle_state = create_stream(opts);
        let (_, snap) = apply_snapshot(&oracle_state, &full, "gen-0", None).unwrap();
        let snap_ids: Vec<_> = snap
            .snapshot
            .as_ref()
            .unwrap()
            .records
            .iter()
            .filter_map(|r| r.record.get("id").and_then(Value::as_str).map(str::to_string))
            .collect();
        assert_eq!(append_ids, snap_ids);
        assert_eq!(
            state.cursor.position.next_byte_offset,
            snap.cursor.position.next_byte_offset
        );
        assert_eq!(state.cursor.prefix_sha256, snap.cursor.prefix_sha256);
    }

    #[test]
    fn file_source_replaced_returns_source_replaced() {
        let original = read_case("file-source-replaced-reset", "step-original.jsonl");
        let replaced = read_case("file-source-replaced-reset", "step-replaced.jsonl");
        let opts = StreamOptions::new(TrajectorySource::Pi)
            .with_group_id("stream-file-source-replaced-reset");
        let state = create_stream(opts);
        let (state, u1) = apply_snapshot(&state, &original, "gen-0", None).unwrap();
        assert_eq!(u1.kind, "updated");
        let prior = state.cursor.position.next_byte_offset;
        let (state2, u2) = apply_snapshot(&state, &replaced, "gen-replaced", None).unwrap();
        assert_eq!(u2.kind, "reset-required");
        assert_eq!(
            u2.reset.as_ref().map(|r| r.reason.as_str()),
            Some("source-replaced")
        );
        assert_eq!(state2.cursor.position.next_byte_offset, prior);
    }

    #[test]
    fn duplicate_append_input_is_idempotent() {
        let line = read_case("duplicate-input-idempotent", "step-line.jsonl");
        let opts = StreamOptions::new(TrajectorySource::Pi)
            .with_group_id("stream-duplicate-input-idempotent");
        let state = create_stream(opts);
        let (state, u1) = apply_append(&state, &line, None, Some("gen-0")).unwrap();
        assert_eq!(u1.kind, "updated");
        let prior = state.cursor.position.next_byte_offset;
        let (state2, u2) = apply_append(&state, &line, None, Some("gen-0")).unwrap();
        assert_eq!(u2.kind, "unchanged");
        assert_eq!(state2.cursor.position.next_byte_offset, prior);
    }

    #[test]
    fn file_compaction_returns_source_compacted() {
        let original = read_case("file-compaction-reset", "step-original.jsonl");
        let compacted = read_case("file-compaction-reset", "step-compacted.jsonl");
        let opts = StreamOptions::new(TrajectorySource::GrokBuild)
            .with_group_id("stream-file-compaction-reset");
        let state = create_stream(opts);
        let (state, u1) = apply_snapshot(&state, &original, "gen-0", None).unwrap();
        assert_eq!(u1.kind, "updated");
        let prior = state.cursor.position.next_byte_offset;
        let (state2, u2) = apply_snapshot(&state, &compacted, "gen-compact", None).unwrap();
        assert_eq!(u2.kind, "reset-required");
        assert_eq!(
            u2.reset.as_ref().map(|r| r.reason.as_str()),
            Some("source-compacted")
        );
        assert_eq!(state2.cursor.position.next_byte_offset, prior);
    }

    #[test]
    fn per_source_append_oracle_parity() {
        let cases: &[(&str, TrajectorySource, &str, usize)] = &[
            ("pi-append-sequence", TrajectorySource::Pi, "stream-pi-append-sequence", 3),
            (
                "claude-code-append-sequence",
                TrajectorySource::ClaudeCode,
                "stream-claude-code-append-sequence",
                2,
            ),
            ("codex-append-sequence", TrajectorySource::Codex, "stream-codex-append", 3),
            (
                "openclaw-append-sequence",
                TrajectorySource::OpenClaw,
                "stream-openclaw-append",
                3,
            ),
            (
                "grok-build-append-sequence",
                TrajectorySource::GrokBuild,
                "stream-grok-build-append-sequence",
                3,
            ),
        ];
        for (case_id, source, group_id, steps) in cases {
            let mut chunks = Vec::new();
            for i in 1..=*steps {
                chunks.push(read_case(case_id, &format!("step-{i}.jsonl")));
            }
            let opts = StreamOptions::new(*source).with_group_id(*group_id);
            let mut state = create_stream(opts);
            for chunk in &chunks {
                let (next, update) = apply_append(&state, chunk, None, Some("gen-0")).unwrap();
                assert_eq!(update.kind, "updated", "{case_id} append failed");
                state = next;
            }
            let append_ids: Vec<_> = state
                .snapshot
                .as_ref()
                .unwrap()
                .records
                .iter()
                .filter_map(|r| r.record.get("id").and_then(Value::as_str).map(str::to_string))
                .collect();
            let mut full = Vec::new();
            for c in &chunks {
                full.extend_from_slice(c);
            }
            let opts = StreamOptions::new(*source).with_group_id(*group_id);
            let oracle_state = create_stream(opts);
            let (_, snap) = apply_snapshot(&oracle_state, &full, "gen-0", None).unwrap();
            let snap_ids: Vec<_> = snap
                .snapshot
                .as_ref()
                .unwrap()
                .records
                .iter()
                .filter_map(|r| r.record.get("id").and_then(Value::as_str).map(str::to_string))
                .collect();
            assert_eq!(append_ids, snap_ids, "{case_id} oracle mismatch");
        }
    }

    #[test]
    fn grok_backend_tool_provisional_then_stable() {
        let step1 = read_case("grok-build-backend-provisional", "step-1.jsonl");
        let step2 = read_case("grok-build-backend-provisional", "step-2.jsonl");
        let opts = StreamOptions::new(TrajectorySource::GrokBuild)
            .with_group_id("stream-grok-build-backend-provisional");
        let state = create_stream(opts);
        let (state, u1) = apply_append(&state, &step1, None, Some("gen-0")).unwrap();
        assert_eq!(u1.kind, "updated");
        let provisional: Vec<_> = u1
            .snapshot
            .as_ref()
            .unwrap()
            .records
            .iter()
            .filter(|r| r.status == "provisional")
            .collect();
        assert_eq!(provisional.len(), 1);
        let content = provisional[0]
            .record
            .get("content")
            .and_then(Value::as_str)
            .unwrap_or("");
        assert!(content.starts_with("[backend "));
        let (state, u2) = apply_append(&state, &step2, None, Some("gen-0")).unwrap();
        assert_eq!(u2.kind, "updated");
        assert!(u2
            .snapshot
            .as_ref()
            .unwrap()
            .records
            .iter()
            .all(|r| r.status == "stable"));
        let _ = state;
    }
}
