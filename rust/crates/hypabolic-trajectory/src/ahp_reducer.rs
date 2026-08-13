//! AHP Shape B action-log reducer (LS-07).
//! Pure function: reduce ActionEnvelope batches into ChatState-like JSON,
//! then decode via existing Shape A path. No network. Protocol pin: 0.7.x.

use serde_json::{Map, Value, json};

/// Content-safe fixed messages (no action bodies, channels, or payloads).
pub const MSG_UNKNOWN_ACTION: &str = "Ignored an unknown AHP action type.";
pub const MSG_FOREIGN_CHANNEL: &str = "Ignored an AHP action for a non-target channel.";
pub const MSG_INVALID_ACTIONS: &str = "AHP action batch must be JSONL envelopes or a JSON array.";
/// Non-monotonic or duplicate serverSeq in original batch order.
pub const MSG_BATCH_REORDER: &str = "AHP action batch serverSeq order must be strictly increasing.";
/// Sequenced and unsequenced envelopes mixed in one batch.
pub const MSG_BATCH_MIXED_SEQ: &str =
    "AHP action batch must not mix sequenced and unsequenced envelopes.";

const KNOWN_CHAT: &[&str] = &[
    "chat/turnStarted",
    "chat/responsePart",
    "chat/delta",
    "chat/reasoning",
    "chat/toolCallStart",
    "chat/toolCallDelta",
    "chat/toolCallReady",
    "chat/toolCallConfirmed",
    "chat/toolCallComplete",
    "chat/toolCallResultConfirmed",
    "chat/toolCallContentChanged",
    "chat/toolCallAuthRequired",
    "chat/toolCallAuthResolved",
    "chat/usage",
    "chat/turnComplete",
    "chat/turnCancelled",
    "chat/error",
    "chat/truncated",
    "chat/activityChanged",
    "chat/workingDirectorySet",
    "chat/workingDirectoryRemoved",
    "chat/inputRequested",
    "chat/inputAnswerChanged",
    "chat/inputCompleted",
];

fn is_known_chat(action_type: &str) -> bool {
    KNOWN_CHAT.contains(&action_type)
}

/// Create a minimal empty ChatState-like object.
#[must_use]
pub fn empty_chat_state(resource: Option<&str>) -> Value {
    json!({
        "resource": resource,
        "title": null,
        "status": 1,
        "activity": "",
        "modifiedAt": null,
        "origin": { "kind": "user" },
        "workingDirectories": [],
        "turns": [],
        "activeTurn": null,
    })
}

/// Parse Shape B bytes: JSONL envelopes, JSON array, or single envelope object.
pub fn parse_action_batch(data: &[u8]) -> Result<Vec<Map<String, Value>>, String> {
    let text = std::str::from_utf8(data).map_err(|_| MSG_INVALID_ACTIONS.to_string())?;
    let stripped = text.trim();
    if stripped.is_empty() {
        return Ok(Vec::new());
    }

    let non_empty_lines: Vec<&str> = text.lines().filter(|ln| !ln.trim().is_empty()).collect();
    if non_empty_lines.len() > 1 {
        let mut envelopes = Vec::with_capacity(non_empty_lines.len());
        for line in non_empty_lines {
            let obj: Value =
                serde_json::from_str(line).map_err(|_| MSG_INVALID_ACTIONS.to_string())?;
            let map = obj
                .as_object()
                .ok_or_else(|| MSG_INVALID_ACTIONS.to_string())?
                .clone();
            envelopes.push(map);
        }
        return Ok(envelopes);
    }

    let parsed: Value =
        serde_json::from_str(stripped).map_err(|_| MSG_INVALID_ACTIONS.to_string())?;
    if let Some(arr) = parsed.as_array() {
        let mut out = Vec::new();
        for item in arr {
            if let Some(map) = item.as_object() {
                out.push(map.clone());
            }
        }
        return Ok(out);
    }
    if let Some(map) = parsed.as_object() {
        return Ok(vec![map.clone()]);
    }
    Err(MSG_INVALID_ACTIONS.to_string())
}

struct NormalizedEnvelope {
    channel: Option<String>,
    server_seq: Option<i64>,
    action: Map<String, Value>,
}

fn normalize_envelope(raw: &Map<String, Value>) -> Option<NormalizedEnvelope> {
    if let Some(action_val) = raw.get("action") {
        if let Some(action) = action_val.as_object() {
            let channel = raw
                .get("channel")
                .and_then(Value::as_str)
                .map(str::to_string);
            let server_seq = parse_seq(raw.get("serverSeq"));
            return Some(NormalizedEnvelope {
                channel,
                server_seq,
                action: action.clone(),
            });
        }
    }
    if raw.get("type").and_then(Value::as_str).is_some() {
        let channel = raw
            .get("channel")
            .and_then(Value::as_str)
            .map(str::to_string);
        let server_seq = parse_seq(raw.get("serverSeq"));
        let mut action = Map::new();
        for (k, v) in raw {
            if k == "channel" || k == "serverSeq" || k == "origin" {
                continue;
            }
            action.insert(k.clone(), v.clone());
        }
        return Some(NormalizedEnvelope {
            channel,
            server_seq,
            action,
        });
    }
    None
}

fn parse_seq(v: Option<&Value>) -> Option<i64> {
    match v {
        Some(Value::Number(n)) => n
            .as_i64()
            .or_else(|| n.as_u64().and_then(|u| i64::try_from(u).ok()))
            .or_else(|| n.as_f64().map(|f| f as i64)),
        _ => None,
    }
}

/// Next expected serverSeq (hosts typically start at 1).
#[must_use]
pub fn expected_next_seq(last_server_seq: Option<i64>) -> i64 {
    match last_server_seq {
        None => 1,
        Some(n) => n + 1,
    }
}

/// Return the first gap serverSeq, or None if contiguous / empty / all replay.
#[must_use]
pub fn detect_sequence_gap(
    envelopes: &[Map<String, Value>],
    last_server_seq: Option<i64>,
    target_channel: Option<&str>,
) -> Option<i64> {
    let expected = expected_next_seq(last_server_seq);
    let mut seqs: Vec<i64> = Vec::new();
    for raw in envelopes {
        let Some(env) = normalize_envelope(raw) else {
            continue;
        };
        let Some(seq) = env.server_seq else {
            continue;
        };
        if let Some(ch) = env.channel.as_deref() {
            if let Some(target) = target_channel {
                if ch != target {
                    continue;
                }
            }
            if !ch.starts_with("ahp-chat:") {
                continue;
            }
        }
        if let Some(last) = last_server_seq {
            if seq <= last {
                continue;
            }
        }
        seqs.push(seq);
    }
    if seqs.is_empty() {
        return None;
    }
    seqs.sort_unstable();
    if last_server_seq.is_some() && seqs[0] > expected {
        return Some(seqs[0]);
    }
    let mut prev = seqs[0];
    for &s in &seqs[1..] {
        if s > prev + 1 {
            return Some(prev + 1);
        }
        prev = s;
    }
    None
}

/// Validate original batch order under reorder=reject.
/// Returns a fixed content-safe error message when invalid, else None.
/// Does not sort. Rejects non-monotonic/duplicate seqs and mixed sequencing.
#[must_use]
pub fn validate_ahp_batch_order(
    envelopes: &[Map<String, Value>],
    target_channel: Option<&str>,
) -> Option<&'static str> {
    let mut has_seq = false;
    let mut has_unseq = false;
    let mut last_seq: Option<i64> = None;
    for raw in envelopes {
        let Some(env) = normalize_envelope(raw) else {
            continue;
        };
        if let Some(ch) = env.channel.as_deref() {
            if let Some(target) = target_channel {
                if ch != target {
                    continue;
                }
            }
            if !ch.starts_with("ahp-chat:") {
                continue;
            }
        }
        match env.server_seq {
            None => {
                has_unseq = true;
                if has_seq {
                    return Some(MSG_BATCH_MIXED_SEQ);
                }
            }
            Some(seq) => {
                has_seq = true;
                if has_unseq {
                    return Some(MSG_BATCH_MIXED_SEQ);
                }
                if let Some(prev) = last_seq {
                    if seq <= prev {
                        return Some(MSG_BATCH_REORDER);
                    }
                }
                last_seq = Some(seq);
            }
        }
    }
    None
}

/// Result of reducing an action batch.
pub struct ReduceResult {
    /// Reduced chat state.
    pub chat: Value,
    /// New last server seq (None if never advanced).
    pub last_server_seq: Option<i64>,
    /// Content-safe diagnostics.
    pub diagnostics: Vec<(String, String)>,
    /// Applied serverSeq values (retained for host inspection / future slices).
    #[allow(dead_code)]
    pub applied: Vec<i64>,
}

/// Reduce ordered envelopes into chat state.
///
/// Consumes original batch order and does not sort. Callers must reject
/// non-monotonic / mixed batches via [`validate_ahp_batch_order`] first.
#[must_use]
pub fn reduce_ahp_actions(
    chat: Option<&Value>,
    envelopes: &[Map<String, Value>],
    target_channel: Option<&str>,
    last_server_seq: Option<i64>,
) -> ReduceResult {
    let mut state = chat
        .cloned()
        .unwrap_or_else(|| empty_chat_state(target_channel));
    let mut diagnostics: Vec<(String, String)> = Vec::new();
    let mut applied: Vec<i64> = Vec::new();
    let mut last = last_server_seq;
    let mut channel = target_channel.map(str::to_string).or_else(|| {
        state
            .get("resource")
            .and_then(Value::as_str)
            .map(str::to_string)
    });
    if channel.is_some() && state.get("resource").map_or(true, Value::is_null) {
        if let Some(ch) = &channel {
            if let Some(obj) = state.as_object_mut() {
                obj.insert("resource".into(), Value::String(ch.clone()));
            }
        }
    }

    // Preserve original batch order (reorder=reject). Do not sort-then-apply.
    let mut normalized: Vec<NormalizedEnvelope> = Vec::new();
    for raw in envelopes {
        match normalize_envelope(raw) {
            Some(env) => normalized.push(env),
            None => {
                diagnostics.push(("ahp_unknown_action".into(), MSG_UNKNOWN_ACTION.into()));
            }
        }
    }

    for env in normalized {
        let action_type = env
            .action
            .get("type")
            .and_then(Value::as_str)
            .map(str::to_string);
        let Some(action_type) = action_type else {
            diagnostics.push(("ahp_unknown_action".into(), MSG_UNKNOWN_ACTION.into()));
            continue;
        };

        let env_channel = env.channel.as_deref();
        if channel.is_none() {
            if let Some(ch) = env_channel {
                if ch.starts_with("ahp-chat:") {
                    channel = Some(ch.to_string());
                    if let Some(obj) = state.as_object_mut() {
                        obj.insert("resource".into(), Value::String(ch.to_string()));
                    }
                }
            }
        }

        if let Some(ch) = env_channel {
            if let Some(target) = channel.as_deref() {
                if ch != target {
                    diagnostics.push(("ahp_foreign_channel".into(), MSG_FOREIGN_CHANNEL.into()));
                    continue;
                }
            }
            if !ch.starts_with("ahp-chat:") {
                diagnostics.push(("ahp_foreign_channel".into(), MSG_FOREIGN_CHANNEL.into()));
                continue;
            }
        }

        if env.server_seq.is_none() {
            if !is_known_chat(&action_type) {
                diagnostics.push(("ahp_unknown_action".into(), MSG_UNKNOWN_ACTION.into()));
                continue;
            }
            state = apply_chat_action(&state, &env.action);
            continue;
        }

        let seq = env.server_seq.unwrap();
        if let Some(prev) = last {
            if seq <= prev {
                continue;
            }
        }

        if !is_known_chat(&action_type) {
            diagnostics.push(("ahp_unknown_action".into(), MSG_UNKNOWN_ACTION.into()));
            last = Some(seq);
            applied.push(seq);
            continue;
        }

        state = apply_chat_action(&state, &env.action);
        last = Some(seq);
        applied.push(seq);
    }

    if let Some(ch) = &channel {
        if let Some(obj) = state.as_object_mut() {
            obj.insert("resource".into(), Value::String(ch.clone()));
        }
    }

    ReduceResult {
        chat: state,
        last_server_seq: last,
        diagnostics,
        applied,
    }
}

/// Serialize reduced ChatState as Shape A export bytes.
#[must_use]
pub fn shape_a_bytes(chat: &Value, protocol_version: &str, session: Option<&Value>) -> Vec<u8> {
    let mut envelope = Map::new();
    envelope.insert(
        "ahpProtocolVersion".into(),
        Value::String(protocol_version.into()),
    );
    envelope.insert("chat".into(), chat.clone());
    if let Some(s) = session {
        envelope.insert("session".into(), s.clone());
    }
    // Compact JSON like Python separators=(",", ":")
    serde_json::to_vec(&Value::Object(envelope)).unwrap_or_default()
}

fn clone_json(v: &Value) -> Value {
    v.clone()
}

fn active_turn(state: &Value) -> Option<&Map<String, Value>> {
    state.get("activeTurn").and_then(Value::as_object)
}

fn apply_chat_action(state: &Value, action: &Map<String, Value>) -> Value {
    let t = action.get("type").and_then(Value::as_str).unwrap_or("");
    match t {
        "chat/turnStarted" => turn_started(state, action),
        "chat/responsePart" => response_part(state, action),
        "chat/delta" => delta(state, action, &["markdown"]),
        "chat/reasoning" => delta(state, action, &["reasoning"]),
        "chat/toolCallStart" => tool_call_start(state, action),
        "chat/toolCallDelta" => update_tool(state, action, |tc| {
            if tc.get("status").and_then(Value::as_str) != Some("streaming") {
                return;
            }
            if let Some(content) = action.get("content").and_then(Value::as_str) {
                let prev = tc.get("partialInput").and_then(Value::as_str).unwrap_or("");
                tc.insert(
                    "partialInput".into(),
                    Value::String(format!("{prev}{content}")),
                );
            }
            if let Some(inv) = action.get("invocationMessage") {
                tc.insert("invocationMessage".into(), inv.clone());
            }
        }),
        "chat/toolCallReady" => update_tool(state, action, |tc| {
            let status = tc.get("status").and_then(Value::as_str).unwrap_or("");
            if !matches!(status, "streaming" | "running" | "pending-confirmation") {
                return;
            }
            if let Some(v) = action.get("intention") {
                tc.insert("intention".into(), v.clone());
            }
            if let Some(v) = action.get("invocationMessage") {
                tc.insert("invocationMessage".into(), v.clone());
            }
            if let Some(v) = action.get("toolInput") {
                tc.insert("toolInput".into(), v.clone());
            }
            if let Some(v) = action.get("contributor") {
                tc.insert("contributor".into(), v.clone());
            }
            if action.get("confirmed").is_some_and(is_truthy) {
                tc.insert("status".into(), Value::String("running".into()));
                tc.insert("confirmed".into(), action["confirmed"].clone());
            } else {
                tc.insert(
                    "status".into(),
                    Value::String("pending-confirmation".into()),
                );
            }
        }),
        "chat/toolCallConfirmed" => update_tool(state, action, |tc| {
            if tc.get("status").and_then(Value::as_str) != Some("pending-confirmation") {
                return;
            }
            if action.get("approved").is_some_and(is_truthy) {
                tc.insert("status".into(), Value::String("running".into()));
                tc.insert(
                    "confirmed".into(),
                    action
                        .get("confirmed")
                        .cloned()
                        .unwrap_or_else(|| Value::String("user-action".into())),
                );
                if let (Some(edited), Some(Value::String(_))) = (
                    action.get("editedToolInput").and_then(Value::as_str),
                    tc.get("toolInput"),
                ) {
                    tc.insert("toolInput".into(), Value::String(edited.into()));
                }
            } else {
                tc.insert("status".into(), Value::String("cancelled".into()));
                tc.insert("success".into(), Value::Bool(false));
                tc.insert(
                    "reason".into(),
                    action
                        .get("reason")
                        .cloned()
                        .unwrap_or_else(|| Value::String("denied".into())),
                );
                if let Some(rm) = action.get("reasonMessage") {
                    tc.insert("reasonMessage".into(), rm.clone());
                }
            }
        }),
        "chat/toolCallComplete" => update_tool(state, action, |tc| {
            let status = tc
                .get("status")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            if !matches!(
                status.as_str(),
                "running" | "pending-confirmation" | "auth-required"
            ) {
                return;
            }
            let result = action
                .get("result")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();
            if status == "auth-required" && result.get("success") == Some(&Value::Bool(true)) {
                return;
            }
            for key in [
                "success",
                "pastTenseMessage",
                "content",
                "structuredContent",
                "error",
                "reasonMessage",
            ] {
                if let Some(v) = result.get(key) {
                    tc.insert(key.into(), v.clone());
                }
            }
            let needs_confirm = action
                .get("requiresResultConfirmation")
                .is_some_and(is_truthy)
                && status != "auth-required";
            tc.insert(
                "status".into(),
                Value::String(if needs_confirm {
                    "pending-result-confirmation".into()
                } else {
                    "completed".into()
                }),
            );
            if tc.get("confirmed").map_or(true, Value::is_null) && status == "pending-confirmation"
            {
                tc.insert("confirmed".into(), Value::String("not-needed".into()));
            }
        }),
        "chat/toolCallResultConfirmed" => update_tool(state, action, |tc| {
            if tc.get("status").and_then(Value::as_str) != Some("pending-result-confirmation") {
                return;
            }
            if action.get("approved").is_some_and(is_truthy) {
                tc.insert("status".into(), Value::String("completed".into()));
            } else {
                tc.insert("status".into(), Value::String("cancelled".into()));
                tc.insert("success".into(), Value::Bool(false));
                tc.insert("reason".into(), Value::String("result-denied".into()));
            }
        }),
        "chat/toolCallContentChanged" => update_tool(state, action, |tc| {
            if tc.get("status").and_then(Value::as_str) != Some("running") {
                return;
            }
            if let Some(c) = action.get("content") {
                tc.insert("content".into(), c.clone());
            }
        }),
        "chat/toolCallAuthRequired" => update_tool(state, action, |tc| {
            if tc.get("status").and_then(Value::as_str) != Some("running") {
                return;
            }
            let is_mcp = tc
                .get("contributor")
                .and_then(Value::as_object)
                .and_then(|c| c.get("kind"))
                .and_then(Value::as_str)
                == Some("mcp");
            if !is_mcp {
                return;
            }
            tc.insert("status".into(), Value::String("auth-required".into()));
            if let Some(auth) = action.get("auth") {
                tc.insert("auth".into(), auth.clone());
            }
        }),
        "chat/toolCallAuthResolved" => update_tool(state, action, |tc| {
            if tc.get("status").and_then(Value::as_str) != Some("auth-required") {
                return;
            }
            tc.insert("status".into(), Value::String("running".into()));
            tc.remove("auth");
        }),
        "chat/usage" => {
            let mut next = clone_json(state);
            let turn_id = action.get("turnId");
            let usage = action.get("usage");
            let ok = {
                let active = next.get("activeTurn").and_then(Value::as_object);
                matches!(
                    (active, turn_id, usage),
                    (Some(a), Some(tid), Some(u))
                        if a.get("id") == Some(tid) && u.is_object()
                )
            };
            if !ok {
                return state.clone();
            }
            if let Some(active) = next.get_mut("activeTurn").and_then(Value::as_object_mut) {
                active.insert("usage".into(), usage.cloned().unwrap_or(Value::Null));
            }
            next
        }
        "chat/turnComplete" => end_turn(state, action, "complete"),
        "chat/turnCancelled" => end_turn(state, action, "cancelled"),
        "chat/error" => end_turn(state, action, "error"),
        "chat/truncated" => truncated(state, action),
        "chat/activityChanged" => {
            let mut next = clone_json(state);
            if let Some(obj) = next.as_object_mut() {
                let activity = action.get("activity").and_then(Value::as_str).unwrap_or("");
                obj.insert("activity".into(), Value::String(activity.into()));
            }
            next
        }
        "chat/workingDirectorySet" => {
            let directory = match action.get("directory").and_then(Value::as_str) {
                Some(d) => d,
                None => return state.clone(),
            };
            let mut next = clone_json(state);
            if let Some(obj) = next.as_object_mut() {
                let mut dirs: Vec<Value> = obj
                    .get("workingDirectories")
                    .and_then(Value::as_array)
                    .cloned()
                    .unwrap_or_default();
                if !dirs.iter().any(|d| d.as_str() == Some(directory)) {
                    dirs.push(Value::String(directory.into()));
                }
                obj.insert("workingDirectories".into(), Value::Array(dirs));
            }
            next
        }
        "chat/workingDirectoryRemoved" => {
            let directory = match action.get("directory").and_then(Value::as_str) {
                Some(d) => d,
                None => return state.clone(),
            };
            let mut next = clone_json(state);
            if let Some(obj) = next.as_object_mut() {
                let dirs: Vec<Value> = obj
                    .get("workingDirectories")
                    .and_then(Value::as_array)
                    .cloned()
                    .unwrap_or_default()
                    .into_iter()
                    .filter(|d| d.as_str() != Some(directory))
                    .collect();
                obj.insert("workingDirectories".into(), Value::Array(dirs));
            }
            next
        }
        "chat/inputRequested" | "chat/inputAnswerChanged" | "chat/inputCompleted" => state.clone(),
        _ => state.clone(),
    }
}

fn is_truthy(v: &Value) -> bool {
    match v {
        Value::Bool(b) => *b,
        Value::Null => false,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(_) => true,
    }
}

fn turn_started(state: &Value, action: &Map<String, Value>) -> Value {
    let turn_id = match action.get("turnId").and_then(Value::as_str) {
        Some(id) => id,
        None => return state.clone(),
    };
    let mut next = clone_json(state);
    let message = action
        .get("message")
        .filter(|m| m.is_object())
        .cloned()
        .unwrap_or_else(|| json!({"text": "", "origin": {"kind": "user"}}));
    let started = action
        .get("startedAt")
        .and_then(Value::as_str)
        .map(str::to_string);
    if let Some(obj) = next.as_object_mut() {
        obj.insert(
            "activeTurn".into(),
            json!({
                "id": turn_id,
                "startedAt": started,
                "duration": null,
                "message": message,
                "responseParts": [],
                "usage": null,
                "state": "in-progress",
                "error": null,
            }),
        );
        let activity = obj
            .get("activity")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .map(str::to_string)
            .unwrap_or_else(|| "generating".into());
        obj.insert("activity".into(), Value::String(activity));
    }
    next
}

fn response_part(state: &Value, action: &Map<String, Value>) -> Value {
    let turn_id = action.get("turnId");
    let part = match action.get("part").filter(|p| p.is_object()) {
        Some(p) => p,
        None => return state.clone(),
    };
    let active = match active_turn(state) {
        Some(a) if a.get("id") == turn_id => a,
        _ => return state.clone(),
    };
    let mut next = clone_json(state);
    if let Some(active_mut) = next.get_mut("activeTurn").and_then(Value::as_object_mut) {
        let mut parts = active
            .get("responseParts")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        parts.push(part.clone());
        active_mut.insert("responseParts".into(), Value::Array(parts));
    }
    next
}

fn delta(state: &Value, action: &Map<String, Value>, part_kinds: &[&str]) -> Value {
    let turn_id = action.get("turnId");
    let part_id = match action.get("partId").and_then(Value::as_str) {
        Some(id) => id,
        None => return state.clone(),
    };
    let chunk = match action.get("content").and_then(Value::as_str) {
        Some(c) => c,
        None => return state.clone(),
    };
    let active = match active_turn(state) {
        Some(a) if a.get("id") == turn_id => a,
        _ => return state.clone(),
    };
    let parts = match active.get("responseParts").and_then(Value::as_array) {
        Some(p) => p,
        None => return state.clone(),
    };
    let mut updated = false;
    let mut new_parts = Vec::new();
    for part in parts {
        if !updated {
            if let Some(obj) = part.as_object() {
                let kind = obj.get("kind").and_then(Value::as_str).unwrap_or("");
                let id = obj.get("id").and_then(Value::as_str);
                if part_kinds.contains(&kind) && id == Some(part_id) {
                    let mut p = obj.clone();
                    let prev = p.get("content").and_then(Value::as_str).unwrap_or("");
                    p.insert("content".into(), Value::String(format!("{prev}{chunk}")));
                    new_parts.push(Value::Object(p));
                    updated = true;
                    continue;
                }
            }
        }
        new_parts.push(part.clone());
    }
    if !updated {
        return state.clone();
    }
    let mut next = clone_json(state);
    if let Some(active_mut) = next.get_mut("activeTurn").and_then(Value::as_object_mut) {
        active_mut.insert("responseParts".into(), Value::Array(new_parts));
    }
    next
}

fn tool_call_start(state: &Value, action: &Map<String, Value>) -> Value {
    let turn_id = action.get("turnId");
    let tool_call_id = match action.get("toolCallId").and_then(Value::as_str) {
        Some(id) => id,
        None => return state.clone(),
    };
    match active_turn(state) {
        Some(a) if a.get("id") == turn_id => {}
        _ => return state.clone(),
    }
    let mut next = clone_json(state);
    if let Some(active_mut) = next.get_mut("activeTurn").and_then(Value::as_object_mut) {
        let mut parts = active_mut
            .get("responseParts")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        parts.push(json!({
            "kind": "toolCall",
            "toolCall": {
                "toolCallId": tool_call_id,
                "toolName": action.get("toolName").and_then(Value::as_str).unwrap_or("unknown"),
                "displayName": action.get("displayName").cloned().unwrap_or(Value::Null),
                "intention": action.get("intention").cloned().unwrap_or(Value::Null),
                "contributor": action.get("contributor").cloned().unwrap_or(Value::Null),
                "status": "streaming",
                "success": null,
                "confirmed": null,
                "content": null,
                "toolInput": null,
                "invocationMessage": null,
                "pastTenseMessage": null,
            }
        }));
        active_mut.insert("responseParts".into(), Value::Array(parts));
    }
    next
}

fn update_tool(
    state: &Value,
    action: &Map<String, Value>,
    mut updater: impl FnMut(&mut Map<String, Value>),
) -> Value {
    let turn_id = action.get("turnId");
    let tool_call_id = match action.get("toolCallId").and_then(Value::as_str) {
        Some(id) => id,
        None => return state.clone(),
    };
    match active_turn(state) {
        Some(a) if a.get("id") == turn_id => {}
        _ => return state.clone(),
    }
    let mut next = clone_json(state);
    let Some(active_mut) = next.get_mut("activeTurn").and_then(Value::as_object_mut) else {
        return state.clone();
    };
    let parts = match active_mut
        .get("responseParts")
        .and_then(Value::as_array)
        .cloned()
    {
        Some(p) => p,
        None => return state.clone(),
    };
    let mut found = false;
    let mut new_parts = Vec::new();
    for part in parts {
        if !found {
            if let Some(obj) = part.as_object() {
                if obj.get("kind").and_then(Value::as_str) == Some("toolCall") {
                    if let Some(tc) = obj.get("toolCall").and_then(Value::as_object) {
                        if tc.get("toolCallId").and_then(Value::as_str) == Some(tool_call_id) {
                            let mut tc_mut = tc.clone();
                            updater(&mut tc_mut);
                            new_parts.push(json!({
                                "kind": "toolCall",
                                "toolCall": Value::Object(tc_mut),
                            }));
                            found = true;
                            continue;
                        }
                    }
                }
            }
        }
        new_parts.push(part);
    }
    if !found {
        return state.clone();
    }
    active_mut.insert("responseParts".into(), Value::Array(new_parts));
    next
}

fn end_turn(state: &Value, action: &Map<String, Value>, turn_state: &str) -> Value {
    let turn_id = action.get("turnId");
    let active = match active_turn(state) {
        Some(a) if a.get("id") == turn_id => a,
        _ => return state.clone(),
    };
    let duration = match action.get("duration") {
        Some(Value::Number(n)) => {
            let v = n.as_f64().map(|f| f.max(0.0)).unwrap_or(0.0);
            if v.fract() == 0.0 {
                Value::from(v as i64)
            } else {
                json!(v)
            }
        }
        _ => Value::from(0),
    };
    let parts = active
        .get("responseParts")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let new_parts: Vec<Value> = parts
        .into_iter()
        .map(|part| {
            if let Some(obj) = part.as_object() {
                if obj.get("kind").and_then(Value::as_str) == Some("toolCall") {
                    if let Some(tc) = obj.get("toolCall").and_then(Value::as_object) {
                        let mut tc_mut = tc.clone();
                        let st = tc_mut.get("status").and_then(Value::as_str).unwrap_or("");
                        if st != "completed" && st != "cancelled" {
                            tc_mut.insert("status".into(), Value::String("cancelled".into()));
                            tc_mut.insert("success".into(), Value::Bool(false));
                            tc_mut.insert("reason".into(), Value::String("skipped".into()));
                        }
                        return json!({
                            "kind": "toolCall",
                            "toolCall": Value::Object(tc_mut),
                        });
                    }
                }
            }
            part
        })
        .collect();
    let turn = json!({
        "id": active.get("id").cloned().unwrap_or(Value::Null),
        "startedAt": active.get("startedAt").cloned().unwrap_or(Value::Null),
        "duration": duration,
        "message": active.get("message").cloned().unwrap_or(Value::Null),
        "responseParts": new_parts,
        "usage": active.get("usage").cloned().unwrap_or(Value::Null),
        "state": turn_state,
        "error": if turn_state == "error" {
            action.get("error").cloned().unwrap_or(Value::Null)
        } else {
            Value::Null
        },
    });
    let mut next = clone_json(state);
    if let Some(obj) = next.as_object_mut() {
        let mut turns = obj
            .get("turns")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        turns.push(turn);
        obj.insert("turns".into(), Value::Array(turns));
        obj.insert("activeTurn".into(), Value::Null);
        obj.insert("activity".into(), Value::String(String::new()));
    }
    next
}

fn truncated(state: &Value, action: &Map<String, Value>) -> Value {
    let mut next = clone_json(state);
    let turns = next
        .get("turns")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let new_turns = match action.get("turnId") {
        None | Some(Value::Null) => Vec::new(),
        Some(Value::String(tid)) => {
            let idx = turns.iter().position(|t| {
                t.as_object()
                    .and_then(|o| o.get("id"))
                    .and_then(Value::as_str)
                    == Some(tid.as_str())
            });
            match idx {
                Some(i) => turns[..=i].to_vec(),
                None => return state.clone(),
            }
        }
        Some(_) => return state.clone(),
    };
    if let Some(obj) = next.as_object_mut() {
        obj.insert("turns".into(), Value::Array(new_turns));
        obj.insert("activeTurn".into(), Value::Null);
        obj.insert("activity".into(), Value::String(String::new()));
    }
    next
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_jsonl_and_array() {
        let jsonl = br#"{"serverSeq":1,"action":{"type":"chat/activityChanged","activity":"x"}}
{"serverSeq":2,"action":{"type":"chat/activityChanged","activity":"y"}}
"#;
        let envs = parse_action_batch(jsonl).unwrap();
        assert_eq!(envs.len(), 2);

        let arr = br#"[{"serverSeq":1,"action":{"type":"chat/activityChanged","activity":"x"}}]"#;
        let envs = parse_action_batch(arr).unwrap();
        assert_eq!(envs.len(), 1);
    }

    #[test]
    fn gap_detection() {
        let e1: Map<String, Value> = serde_json::from_str(
            r#"{"channel":"ahp-chat:/x","serverSeq":1,"action":{"type":"chat/activityChanged","activity":"a"}}"#,
        )
        .unwrap();
        let e3: Map<String, Value> = serde_json::from_str(
            r#"{"channel":"ahp-chat:/x","serverSeq":3,"action":{"type":"chat/activityChanged","activity":"b"}}"#,
        )
        .unwrap();
        let gap = detect_sequence_gap(&[e1.clone(), e3.clone()], Some(0), Some("ahp-chat:/x"));
        // last=0 expected=1; first is 1 so no initial gap; internal hole at 2
        assert_eq!(gap, Some(2));

        let gap2 = detect_sequence_gap(&[e3], Some(1), Some("ahp-chat:/x"));
        // last=1 expected=2; first=3 > expected → gap 3
        assert_eq!(gap2, Some(3));
    }

    #[test]
    fn turn_flow_reduce() {
        let channel = "ahp-chat:/test";
        let lines = [
            r#"{"channel":"ahp-chat:/test","serverSeq":1,"action":{"type":"chat/turnStarted","turnId":"t1","startedAt":"2026-01-01T00:00:00Z","message":{"text":"hi","origin":{"kind":"user"}}}}"#,
            r#"{"channel":"ahp-chat:/test","serverSeq":2,"action":{"type":"chat/responsePart","turnId":"t1","part":{"kind":"markdown","id":"p1","content":""}}}"#,
            r#"{"channel":"ahp-chat:/test","serverSeq":3,"action":{"type":"chat/delta","turnId":"t1","partId":"p1","content":"hello"}}"#,
            r#"{"channel":"ahp-chat:/test","serverSeq":4,"action":{"type":"chat/turnComplete","turnId":"t1","duration":10}}"#,
        ];
        let envs: Vec<Map<String, Value>> = lines
            .iter()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect();
        let result = reduce_ahp_actions(None, &envs, Some(channel), None);
        assert_eq!(result.last_server_seq, Some(4));
        assert!(result.chat.get("activeTurn").unwrap().is_null());
        let turns = result.chat.get("turns").and_then(Value::as_array).unwrap();
        assert_eq!(turns.len(), 1);
        let parts = turns[0]
            .get("responseParts")
            .and_then(Value::as_array)
            .unwrap();
        assert_eq!(
            parts[0].get("content").and_then(Value::as_str),
            Some("hello")
        );
    }
}
