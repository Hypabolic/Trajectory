//! Optional AHP live-host client (LS-10).
//!
//! Transport-only package for connecting to an Agent Host Protocol host,
//! authenticating via an injected callback, subscribing to a chat channel, and
//! feeding pure core `apply_ahp_snapshot` / `apply_ahp_actions`.
//!
//! Auth tokens never enter stream snapshots, deltas, or diagnostics.
//! Not imported by the core crate.

#![forbid(unsafe_code)]

use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::rc::Rc;

use hypabolic_trajectory::{
    StreamCursor, StreamOptions, StreamResetRequest, StreamState, StreamUpdate, TrajectorySource,
    apply_ahp_actions, apply_ahp_snapshot, create_stream, reset_stream,
};
use serde_json::{Map, Value};

/// Root channel URI for connection-level AHP commands.
pub const AHP_ROOT_CHANNEL: &str = "ahp-root://";
/// Pinned protocol minor for the optional client.
pub const PROTOCOL_VERSION: &str = "0.7.0";
/// Client name advertised on initialize.
pub const CLIENT_NAME: &str = "hypabolic-trajectory-ahp";

/// Fixed content-safe error codes (never include tokens/paths).
pub const ERR_AUTH_FAILED: &str = "ahp_auth_failed";
/// Auth required by host.
pub const ERR_AUTH_REQUIRED: &str = "ahp_auth_required";
/// Transport failure.
pub const ERR_TRANSPORT: &str = "ahp_transport_error";
/// Protocol framing error.
pub const ERR_PROTOCOL: &str = "ahp_protocol_error";
/// Client action buffer full.
pub const ERR_BACKPRESSURE: &str = "ahp_backpressure";
/// Client cancelled.
pub const ERR_CANCELLED: &str = "ahp_cancelled";
/// Sequence gap requires resync.
pub const ERR_RESYNC_REQUIRED: &str = "ahp_resync_required";

/// Fixed safe messages (no secrets).
#[must_use]
pub fn safe_error_message(code: &str) -> &'static str {
    match code {
        ERR_AUTH_FAILED => "AHP authentication failed.",
        ERR_AUTH_REQUIRED => "AHP authentication is required.",
        ERR_TRANSPORT => "AHP transport error.",
        ERR_PROTOCOL => "AHP protocol error.",
        ERR_BACKPRESSURE => "AHP client backpressure limit reached.",
        ERR_CANCELLED => "AHP client cancelled.",
        ERR_RESYNC_REQUIRED => "AHP sequence gap requires resync.",
        _ => "AHP client error.",
    }
}

/// Bidirectional text-frame transport for AHP JSON-RPC.
pub trait AhpTransport {
    /// Send one complete JSON-RPC text frame.
    ///
    /// # Errors
    /// Returns an error when the transport is closed.
    fn send(&self, message: &str) -> Result<(), String>;
    /// Register inbound frame handler.
    fn set_handler(&self, handler: Option<Box<dyn Fn(&str)>>);
    /// Close the duplex.
    fn close(&self);
}

#[derive(Default)]
struct MemoryInner {
    peer: Option<Rc<RefCell<MemoryInner>>>,
    handler: Option<Box<dyn Fn(&str)>>,
    closed: bool,
    sent: Vec<String>,
    /// Frames queued while a delivery is already in progress on this end.
    /// Without this, re-entrant peer responses (e.g. resync result while the
    /// client handler is still running the gap notification) are dropped.
    pending: Vec<String>,
    delivering: bool,
}

/// One side of an in-memory duplex.
#[derive(Default)]
pub struct MemoryAhpTransport {
    inner: Rc<RefCell<MemoryInner>>,
}

impl MemoryAhpTransport {
    /// Create an unbound transport end.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Bind to peer for loopback delivery.
    pub fn bind_peer(&self, peer: &MemoryAhpTransport) {
        self.inner.borrow_mut().peer = Some(Rc::clone(&peer.inner));
    }

    /// Frames sent from this end.
    #[must_use]
    pub fn sent(&self) -> Vec<String> {
        self.inner.borrow().sent.clone()
    }

    /// Whether closed.
    #[must_use]
    pub fn is_closed(&self) -> bool {
        self.inner.borrow().closed
    }
}

/// Deliver `message` to `peer`, queuing if a delivery is already in flight so
/// re-entrant reverse-path frames (resync RPC replies, nested notifications)
/// are not dropped while the peer handler is temporarily taken.
fn deliver_to_peer(peer: &Rc<RefCell<MemoryInner>>, message: &str) {
    {
        let mut p = peer.borrow_mut();
        if p.closed {
            return;
        }
        p.pending.push(message.to_owned());
        if p.delivering {
            return;
        }
        p.delivering = true;
    }
    loop {
        let next = {
            let mut p = peer.borrow_mut();
            if p.closed {
                p.pending.clear();
                p.delivering = false;
                None
            } else if p.pending.is_empty() {
                p.delivering = false;
                None
            } else {
                Some(p.pending.remove(0))
            }
        };
        let Some(raw) = next else {
            break;
        };
        let handler = {
            let mut p = peer.borrow_mut();
            if p.closed {
                None
            } else {
                p.handler.take()
            }
        };
        if let Some(h) = handler {
            h(&raw);
            let mut p = peer.borrow_mut();
            if !p.closed && p.handler.is_none() {
                p.handler = Some(h);
            }
        }
        // If handler was missing, frame is dropped (peer not yet wired).
    }
}

impl AhpTransport for MemoryAhpTransport {
    fn send(&self, message: &str) -> Result<(), String> {
        // Deliver without holding RefCell borrows across the peer callback so
        // the peer may respond re-entrantly on the reverse path.
        let peer = {
            let mut inner = self.inner.borrow_mut();
            if inner.closed {
                return Err("transport_closed".into());
            }
            inner.sent.push(message.to_owned());
            inner.peer.clone()
        };
        if let Some(peer) = peer {
            deliver_to_peer(&peer, message);
        }
        Ok(())
    }

    fn set_handler(&self, handler: Option<Box<dyn Fn(&str)>>) {
        self.inner.borrow_mut().handler = handler;
    }

    fn close(&self) {
        let mut inner = self.inner.borrow_mut();
        inner.closed = true;
        inner.handler = None;
        inner.pending.clear();
    }
}

/// Linked client/host transports for fake-host CI tests.
pub struct InMemoryAhpTransportPair {
    /// Client side.
    pub client: MemoryAhpTransport,
    /// Host side.
    pub host: MemoryAhpTransport,
}

impl Default for InMemoryAhpTransportPair {
    fn default() -> Self {
        Self::new()
    }
}

impl InMemoryAhpTransportPair {
    /// Create a linked pair.
    #[must_use]
    pub fn new() -> Self {
        let client = MemoryAhpTransport::new();
        let host = MemoryAhpTransport::new();
        client.bind_peer(&host);
        host.bind_peer(&client);
        Self { client, host }
    }
}

/// Auth credentials from the injected callback.
#[derive(Debug, Clone)]
pub struct AhpAuthCredentials {
    /// Bearer/token string (transport only).
    pub token: String,
}

/// Auth callback type.
pub type AhpAuthCallback = Box<dyn Fn(Option<&Map<String, Value>>) -> Option<AhpAuthCredentials>>;

/// Client-level event kinds.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AhpClientEventKind {
    /// Core stream update.
    StreamUpdate,
    /// Host requires authentication.
    AuthRequired,
    /// Authentication failed.
    AuthFailed,
    /// Sequence gap; resync needed.
    ResyncRequired,
    /// Action buffer full.
    Backpressure,
    /// Client disconnected/cancelled.
    Disconnected,
    /// Transport/protocol error.
    Error,
    /// Subscribe handshake complete.
    Ready,
}

/// Host-client event. Auth tokens never appear in `update`.
#[derive(Debug, Clone)]
pub struct AhpClientEvent {
    /// Event kind.
    pub kind: AhpClientEventKind,
    /// Stream update when kind is StreamUpdate / ResyncRequired.
    pub update: Option<StreamUpdate>,
    /// Fixed error code.
    pub code: Option<String>,
    /// Fixed safe message.
    pub message: Option<String>,
    /// Buffered action count on backpressure.
    pub buffered: Option<usize>,
}

/// Client options.
pub struct AhpClientOptions {
    /// Target chat channel URI.
    pub chat_channel: String,
    /// Optional auth callback.
    pub auth: Option<AhpAuthCallback>,
    /// Stream options (source forced to AHP).
    pub stream_options: Option<StreamOptions>,
    /// Auto-request resync on sequence gap (default true).
    pub auto_resync: bool,
    /// Max buffered actions before backpressure (default 256).
    pub max_buffered_actions: usize,
    /// Optional fromSeq on subscribe.
    pub from_server_seq: Option<i64>,
    /// Protocol version pin.
    pub protocol_version: String,
}

impl AhpClientOptions {
    /// Create options for a chat channel.
    #[must_use]
    pub fn new(chat_channel: impl Into<String>) -> Self {
        Self {
            chat_channel: chat_channel.into(),
            auth: None,
            stream_options: None,
            auto_resync: true,
            max_buffered_actions: 256,
            from_server_seq: None,
            protocol_version: PROTOCOL_VERSION.to_owned(),
        }
    }
}

struct ClientCore {
    options: AhpClientOptions,
    on_event: Box<dyn Fn(AhpClientEvent)>,
    state: StreamState,
    next_id: i64,
    pending: HashMap<i64, String>,
    action_buffer: Vec<Value>,
    paused: bool,
    cancelled: bool,
    resync_inflight: bool,
    /// Outbound JSON-RPC frames waiting to be sent (avoids re-entrant processing).
    outbox: Vec<String>,
}

/// Connect → auth (callback) → subscribe → feed core AHP stream apply.
///
/// Uses an inbox/outbox pump so synchronous in-memory duplexes never re-enter
/// the client while a frame is being handled. Host-initiated frames (push after
/// `start()`) auto-process via the same pump when no pump is already active.
pub struct AhpStreamClient {
    transport: Rc<dyn AhpTransport>,
    core: Rc<RefCell<ClientCore>>,
    pumping: Rc<Cell<bool>>,
    inbox: Rc<RefCell<Vec<String>>>,
}

impl AhpStreamClient {
    /// Build a client on the given transport.
    pub fn new(
        transport: Box<dyn AhpTransport>,
        options: AhpClientOptions,
        on_event: impl Fn(AhpClientEvent) + 'static,
    ) -> Self {
        let mut stream_opts = options.stream_options.clone().unwrap_or_else(|| {
            StreamOptions::new(TrajectorySource::Ahp).with_group_id(options.chat_channel.clone())
        });
        stream_opts.source = TrajectorySource::Ahp;
        if stream_opts.group_id.is_none() {
            stream_opts.group_id = Some(options.chat_channel.clone());
        }
        if stream_opts.ahp_protocol_version.is_none() {
            stream_opts.ahp_protocol_version = Some(options.protocol_version.clone());
        }
        let state = create_stream(stream_opts);
        let core = Rc::new(RefCell::new(ClientCore {
            options,
            on_event: Box::new(on_event),
            state,
            next_id: 1,
            pending: HashMap::new(),
            action_buffer: Vec::new(),
            paused: false,
            cancelled: false,
            resync_inflight: false,
            outbox: Vec::new(),
        }));
        let pumping = Rc::new(Cell::new(false));
        let inbox = Rc::new(RefCell::new(Vec::new()));
        let transport: Rc<dyn AhpTransport> = Rc::from(transport);

        let inbox_h = Rc::clone(&inbox);
        let pumping_h = Rc::clone(&pumping);
        let core_h = Rc::clone(&core);
        let transport_h = Rc::clone(&transport);

        // Enqueue inbound frames; when idle, auto-run the same pump loop as
        // start()/pump() so host push/action/snapshot is handled without a
        // manual caller pump (parity with other runtimes).
        transport.set_handler(Some(Box::new(move |raw| {
            inbox_h.borrow_mut().push(raw.to_owned());
            if !pumping_h.get() {
                pump_loop(&transport_h, &pumping_h, &inbox_h, &core_h);
            }
        })));

        Self {
            transport,
            core,
            pumping,
            inbox,
        }
    }

    /// Start initialize/subscribe handshake.
    pub fn start(&self) {
        {
            let mut core = self.core.borrow_mut();
            if core.cancelled {
                core.emit_error(ERR_CANCELLED);
                return;
            }
            let params = json_obj(&[
                ("channel", Value::String(AHP_ROOT_CHANNEL.into())),
                (
                    "protocolVersion",
                    Value::String(core.options.protocol_version.clone()),
                ),
                (
                    "clientInfo",
                    Value::Object(Map::from_iter([
                        ("name".into(), Value::String(CLIENT_NAME.into())),
                        ("version".into(), Value::String("0.1.2".into())),
                    ])),
                ),
            ]);
            core.enqueue_request("initialize", params);
        }
        self.pump();
    }

    /// Cancel; last committed cursor remains valid.
    pub fn cancel(&self) {
        {
            let mut core = self.core.borrow_mut();
            core.cancelled = true;
            core.action_buffer.clear();
            (core.on_event)(AhpClientEvent {
                kind: AhpClientEventKind::Disconnected,
                update: None,
                code: Some(ERR_CANCELLED.into()),
                message: Some(safe_error_message(ERR_CANCELLED).into()),
                buffered: None,
            });
        }
        self.transport.close();
    }

    /// Current stream cursor.
    #[must_use]
    pub fn cursor(&self) -> StreamCursor {
        self.core.borrow().state.cursor.clone()
    }

    /// Whether cancelled.
    #[must_use]
    pub fn is_cancelled(&self) -> bool {
        self.core.borrow().cancelled
    }

    /// Force pause for backpressure tests.
    pub fn set_paused_for_test(&self, paused: bool) {
        self.core.borrow_mut().paused = paused;
    }

    /// Clear backpressure pause and flush any buffered actions (parity with peers).
    pub fn resume(&self) {
        let mut core = self.core.borrow_mut();
        core.paused = false;
        core.flush_actions();
    }

    /// Drain inbox/outbox until idle. Safe for synchronous duplex hosts.
    ///
    /// Usually unnecessary: inbound frames auto-pump when the client is idle.
    /// Safe to call re-entrantly (no-op while a pump is already active).
    pub fn pump(&self) {
        pump_loop(&self.transport, &self.pumping, &self.inbox, &self.core);
    }
}

/// Shared inbox/outbox drain used by both explicit `pump()` and the transport
/// handler auto-process path.
fn pump_loop(
    transport: &Rc<dyn AhpTransport>,
    pumping: &Rc<Cell<bool>>,
    inbox: &Rc<RefCell<Vec<String>>>,
    core: &Rc<RefCell<ClientCore>>,
) {
    if pumping.get() {
        return;
    }
    pumping.set(true);
    loop {
        let frames: Vec<String> = inbox.borrow_mut().drain(..).collect();
        for raw in frames {
            core.borrow_mut().on_frame(&raw);
        }
        let outbound: Vec<String> = core.borrow_mut().outbox.drain(..).collect();
        if outbound.is_empty() && inbox.borrow().is_empty() {
            break;
        }
        for msg in outbound {
            if transport.send(&msg).is_err() {
                core.borrow_mut().emit_error(ERR_TRANSPORT);
            }
        }
    }
    pumping.set(false);
}

impl ClientCore {
    fn enqueue_request(&mut self, method: &str, params: Value) {
        let id = self.next_id;
        self.next_id += 1;
        self.pending.insert(id, method.to_owned());
        let msg = Value::Object(Map::from_iter([
            ("jsonrpc".into(), Value::String("2.0".into())),
            ("id".into(), Value::Number(id.into())),
            ("method".into(), Value::String(method.into())),
            ("params".into(), params),
        ]));
        self.outbox.push(msg.to_string());
    }

    fn on_frame(&mut self, raw: &str) {
        if self.cancelled {
            return;
        }
        let msg: Value = match serde_json::from_str(raw) {
            Ok(v) => v,
            Err(_) => {
                self.emit_error(ERR_PROTOCOL);
                return;
            }
        };
        let Some(obj) = msg.as_object() else {
            self.emit_error(ERR_PROTOCOL);
            return;
        };
        if obj.contains_key("method") && !obj.contains_key("id") {
            self.handle_notification(obj);
            return;
        }
        if obj.contains_key("id") {
            self.handle_response(obj);
            return;
        }
        self.emit_error(ERR_PROTOCOL);
    }

    fn handle_response(&mut self, msg: &Map<String, Value>) {
        let raw_id = match msg.get("id") {
            Some(Value::Number(n)) => n.as_i64().unwrap_or(-1),
            Some(Value::String(s)) => s.parse().unwrap_or(-1),
            _ => {
                self.emit_error(ERR_PROTOCOL);
                return;
            }
        };
        let Some(method) = self.pending.remove(&raw_id) else {
            return;
        };
        if msg.contains_key("error") {
            let err_msg = msg
                .get("error")
                .and_then(|e| e.get("message"))
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_ascii_lowercase();
            if method == "authenticate" || err_msg.contains("auth") {
                (self.on_event)(AhpClientEvent {
                    kind: AhpClientEventKind::AuthFailed,
                    update: None,
                    code: Some(ERR_AUTH_FAILED.into()),
                    message: Some(safe_error_message(ERR_AUTH_FAILED).into()),
                    buffered: None,
                });
                return;
            }
            self.emit_error(ERR_PROTOCOL);
            return;
        }
        let result = msg.get("result");
        match method.as_str() {
            "initialize" => {
                if result
                    .and_then(Value::as_object)
                    .and_then(|o| o.get("authRequired"))
                    .and_then(Value::as_bool)
                    == Some(true)
                {
                    let challenge = result
                        .and_then(Value::as_object)
                        .and_then(|o| o.get("authChallenge"))
                        .and_then(Value::as_object);
                    self.begin_auth(challenge);
                    return;
                }
                self.send_subscribe();
            }
            "authenticate" => self.send_subscribe(),
            "subscribe" => {
                (self.on_event)(AhpClientEvent {
                    kind: AhpClientEventKind::Ready,
                    update: None,
                    code: None,
                    message: None,
                    buffered: None,
                });
                if let Some(Value::Object(r)) = result {
                    self.ingest_subscribe_result(r);
                }
            }
            "resync" => {
                // Keep resync_inflight true until reset + snapshot apply finish
                // so re-entrant action notifications drop mid-resync.
                if let Some(Value::Object(r)) = result {
                    self.apply_resync_snapshot(r);
                } else {
                    self.resync_inflight = false;
                }
            }
            _ => {}
        }
    }

    fn handle_notification(&mut self, msg: &Map<String, Value>) {
        let method = msg.get("method").and_then(Value::as_str).unwrap_or("");
        let params = msg
            .get("params")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();
        match method {
            "auth/required" | "authRequired" => {
                self.begin_auth(if params.is_empty() {
                    None
                } else {
                    Some(&params)
                });
            }
            "action" | "channel/action" => {
                if !self.notification_channel_ok(&params) {
                    return;
                }
                let envelope = params
                    .get("envelope")
                    .cloned()
                    .unwrap_or(Value::Object(params.clone()));
                if envelope.get("action").is_some() {
                    self.buffer_action(envelope);
                }
            }
            "snapshot" | "channel/snapshot" => {
                if !self.notification_channel_ok(&params) {
                    return;
                }
                self.apply_host_snapshot(&params);
            }
            _ => {}
        }
    }

    /// Drop action/snapshot noise whose params.channel is not the subscribed chat.
    fn notification_channel_ok(&self, params: &Map<String, Value>) -> bool {
        match params.get("channel").and_then(Value::as_str) {
            Some(ch) => ch == self.options.chat_channel,
            // Protocol requires channel on notifications; treat missing as foreign noise.
            None => false,
        }
    }

    fn begin_auth(&mut self, challenge: Option<&Map<String, Value>>) {
        (self.on_event)(AhpClientEvent {
            kind: AhpClientEventKind::AuthRequired,
            update: None,
            code: Some(ERR_AUTH_REQUIRED.into()),
            message: Some(safe_error_message(ERR_AUTH_REQUIRED).into()),
            buffered: None,
        });
        let Some(ref auth) = self.options.auth else {
            (self.on_event)(AhpClientEvent {
                kind: AhpClientEventKind::AuthFailed,
                update: None,
                code: Some(ERR_AUTH_FAILED.into()),
                message: Some(safe_error_message(ERR_AUTH_FAILED).into()),
                buffered: None,
            });
            return;
        };
        let creds = auth(challenge);
        let Some(creds) = creds.filter(|c| !c.token.is_empty()) else {
            (self.on_event)(AhpClientEvent {
                kind: AhpClientEventKind::AuthFailed,
                update: None,
                code: Some(ERR_AUTH_FAILED.into()),
                message: Some(safe_error_message(ERR_AUTH_FAILED).into()),
                buffered: None,
            });
            return;
        };
        let params = json_obj(&[
            ("channel", Value::String(AHP_ROOT_CHANNEL.into())),
            ("token", Value::String(creds.token)),
        ]);
        self.enqueue_request("authenticate", params);
    }

    fn send_subscribe(&mut self) {
        let mut params = Map::new();
        params.insert(
            "channel".into(),
            Value::String(self.options.chat_channel.clone()),
        );
        if let Some(seq) = self.options.from_server_seq {
            params.insert("fromSeq".into(), Value::Number(seq.into()));
        }
        self.enqueue_request("subscribe", Value::Object(params));
    }

    fn ingest_subscribe_result(&mut self, result: &Map<String, Value>) {
        if result.contains_key("snapshot") {
            self.apply_host_snapshot(result);
        }
        if let Some(Value::Array(actions)) = result.get("actions") {
            for item in actions {
                if item.is_object() {
                    self.buffer_action(item.clone());
                }
            }
            self.flush_actions();
        }
    }

    fn buffer_action(&mut self, envelope: Value) {
        if self.resync_inflight {
            return;
        }
        if self.action_buffer.len() >= self.options.max_buffered_actions {
            self.paused = true;
            (self.on_event)(AhpClientEvent {
                kind: AhpClientEventKind::Backpressure,
                update: None,
                code: Some(ERR_BACKPRESSURE.into()),
                message: Some(safe_error_message(ERR_BACKPRESSURE).into()),
                buffered: Some(self.action_buffer.len()),
            });
            return;
        }
        self.action_buffer.push(envelope);
        if !self.paused {
            self.flush_actions();
        }
    }

    fn flush_actions(&mut self) {
        if self.cancelled || self.resync_inflight || self.action_buffer.is_empty() {
            return;
        }
        let batch = std::mem::take(&mut self.action_buffer);
        let mut lines = String::new();
        for env in &batch {
            lines.push_str(&env.to_string());
            lines.push('\n');
        }
        match apply_ahp_actions(&self.state, lines.as_bytes(), None) {
            Ok((state, update)) => {
                self.state = state;
                let is_gap = update.kind == "reset-required"
                    && update
                        .reset
                        .as_ref()
                        .is_some_and(|r| r.reason == "sequence-gap");
                self.emit_update(update.clone());
                if is_gap {
                    self.handle_sequence_gap(update);
                }
            }
            Err(_) => self.emit_error(ERR_PROTOCOL),
        }
    }

    fn apply_host_snapshot(&mut self, params: &Map<String, Value>) {
        let mut material_obj = if let Some(s) = params.get("snapshot").and_then(Value::as_object) {
            s.clone()
        } else if let Some(c) = params.get("chat").and_then(Value::as_object) {
            Map::from_iter([
                (
                    "ahpProtocolVersion".into(),
                    Value::String(self.options.protocol_version.clone()),
                ),
                ("chat".into(), Value::Object(c.clone())),
            ])
        } else if params.contains_key("ahpProtocolVersion") {
            params.clone()
        } else {
            return;
        };
        if !material_obj.contains_key("chat") && material_obj.contains_key("turns") {
            material_obj = Map::from_iter([
                (
                    "ahpProtocolVersion".into(),
                    Value::String(self.options.protocol_version.clone()),
                ),
                ("chat".into(), Value::Object(material_obj)),
            ]);
        } else if !material_obj.contains_key("ahpProtocolVersion") {
            material_obj.insert(
                "ahpProtocolVersion".into(),
                Value::String(self.options.protocol_version.clone()),
            );
        }
        let revision = params
            .get("revision")
            .or_else(|| params.get("sourceRevision"))
            .and_then(Value::as_str)
            .unwrap_or("host-snapshot")
            .to_owned();
        let material = Value::Object(material_obj).to_string();
        match apply_ahp_snapshot(&self.state, material.as_bytes(), &revision, None) {
            Ok((state, update)) => {
                self.state = state;
                self.emit_update(update);
            }
            Err(_) => self.emit_error(ERR_PROTOCOL),
        }
    }

    fn handle_sequence_gap(&mut self, update: StreamUpdate) {
        (self.on_event)(AhpClientEvent {
            kind: AhpClientEventKind::ResyncRequired,
            update: Some(update),
            code: Some(ERR_RESYNC_REQUIRED.into()),
            message: Some(safe_error_message(ERR_RESYNC_REQUIRED).into()),
            buffered: None,
        });
        if !self.options.auto_resync {
            return;
        }
        self.resync_inflight = true;
        self.action_buffer.clear();
        let params = json_obj(&[(
            "channel",
            Value::String(self.options.chat_channel.clone()),
        )]);
        self.enqueue_request("resync", params);
    }

    fn apply_resync_snapshot(&mut self, result: &Map<String, Value>) {
        let prior = self.state.cursor.clone();
        let request = StreamResetRequest {
            reason: "sequence-gap".into(),
            generation: None,
            source_revision: result
                .get("revision")
                .and_then(Value::as_str)
                .map(str::to_owned),
            prior_cursor: Some(prior),
            material: None,
        };
        if let Ok((state, _)) = reset_stream(&self.state, &request) {
            self.state = state;
            self.apply_host_snapshot(result);
        }
        self.resync_inflight = false;
    }

    fn emit_update(&self, update: StreamUpdate) {
        (self.on_event)(AhpClientEvent {
            kind: AhpClientEventKind::StreamUpdate,
            update: Some(update),
            code: None,
            message: None,
            buffered: None,
        });
    }

    fn emit_error(&self, code: &str) {
        (self.on_event)(AhpClientEvent {
            kind: AhpClientEventKind::Error,
            update: None,
            code: Some(code.into()),
            message: Some(safe_error_message(code).into()),
            buffered: None,
        });
    }
}

fn json_obj(entries: &[(&str, Value)]) -> Value {
    Value::Object(Map::from_iter(
        entries.iter().map(|(k, v)| ((*k).to_owned(), v.clone())),
    ))
}

/// Declarative host behaviour for a single chat channel.
#[derive(Debug, Clone, Default)]
pub struct FakeAhpHostScript {
    /// Require auth after initialize.
    pub require_auth: bool,
    /// Accepted token (`None` rejects all).
    pub accept_token: Option<String>,
    /// Optional initial Shape A snapshot on subscribe.
    pub initial_snapshot: Option<Value>,
    /// Revision for initial snapshot.
    pub initial_revision: String,
    /// Optional initial action envelopes on subscribe.
    pub initial_actions: Vec<Value>,
}

impl FakeAhpHostScript {
    /// Default script accepting `test-token`.
    #[must_use]
    pub fn new() -> Self {
        Self {
            require_auth: false,
            accept_token: Some("test-token".into()),
            initial_snapshot: None,
            initial_revision: "rev-1".into(),
            initial_actions: Vec::new(),
        }
    }
}

/// Programmable fake AHP host for CI (no real network).
pub struct FakeAhpHost {
    transport: MemoryAhpTransport,
    chat_channel: String,
    closed: Rc<Cell<bool>>,
    auth_attempts: Rc<Cell<i32>>,
    resync_count: Rc<Cell<i32>>,
}

impl FakeAhpHost {
    /// Attach host logic to a memory transport.
    pub fn new(
        transport: MemoryAhpTransport,
        script: FakeAhpHostScript,
        chat_channel: &str,
    ) -> Self {
        let closed = Rc::new(Cell::new(false));
        let auth_attempts = Rc::new(Cell::new(0));
        let resync_count = Rc::new(Cell::new(0));
        let send_transport = MemoryAhpTransport {
            inner: Rc::clone(&transport.inner),
        };
        let chat = chat_channel.to_owned();
        let closed_h = Rc::clone(&closed);
        let auth_h = Rc::clone(&auth_attempts);
        let resync_h = Rc::clone(&resync_count);

        transport.set_handler(Some(Box::new(move |raw| {
            if closed_h.get() {
                return;
            }
            let msg: Value = match serde_json::from_str(raw) {
                Ok(v) => v,
                Err(_) => return,
            };
            let Some(obj) = msg.as_object() else {
                return;
            };
            let method = obj.get("method").and_then(Value::as_str).unwrap_or("");
            let Some(req_id) = obj.get("id").cloned() else {
                return;
            };
            let params = obj
                .get("params")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();

            match method {
                "initialize" => {
                    let mut result = Map::new();
                    result.insert("channel".into(), Value::String(AHP_ROOT_CHANNEL.into()));
                    result.insert(
                        "protocolVersion".into(),
                        Value::String(PROTOCOL_VERSION.into()),
                    );
                    if script.require_auth {
                        result.insert("authRequired".into(), Value::Bool(true));
                    }
                    let _ = send_transport.send(&encode_result(req_id, Value::Object(result)));
                }
                "authenticate" => {
                    auth_h.set(auth_h.get() + 1);
                    let token = params.get("token").and_then(Value::as_str).unwrap_or("");
                    if script.accept_token.as_deref() == Some(token) {
                        let _ = send_transport.send(&encode_result(
                            req_id,
                            json_obj(&[("ok", Value::Bool(true))]),
                        ));
                    } else {
                        let _ = send_transport.send(&encode_error(
                            req_id,
                            -32001,
                            "authentication failed",
                        ));
                    }
                }
                "subscribe" => {
                    let mut result = Map::new();
                    result.insert(
                        "channel".into(),
                        Value::String(
                            params
                                .get("channel")
                                .and_then(Value::as_str)
                                .unwrap_or(&chat)
                                .to_owned(),
                        ),
                    );
                    if let Some(ref snap) = script.initial_snapshot {
                        result.insert(
                            "revision".into(),
                            Value::String(script.initial_revision.clone()),
                        );
                        result.insert("snapshot".into(), snap.clone());
                    }
                    if !script.initial_actions.is_empty() {
                        result.insert(
                            "actions".into(),
                            Value::Array(script.initial_actions.clone()),
                        );
                    }
                    let _ = send_transport.send(&encode_result(req_id, Value::Object(result)));
                }
                "resync" => {
                    resync_h.set(resync_h.get() + 1);
                    let n = resync_h.get();
                    let snap = script.initial_snapshot.clone().unwrap_or_else(|| {
                        json_obj(&[
                            (
                                "ahpProtocolVersion",
                                Value::String(PROTOCOL_VERSION.into()),
                            ),
                            (
                                "chat",
                                json_obj(&[
                                    ("id", Value::String(chat.clone())),
                                    ("turns", Value::Array(vec![])),
                                    ("activeTurn", Value::Null),
                                ]),
                            ),
                        ])
                    });
                    let result = json_obj(&[
                        ("channel", Value::String(chat.clone())),
                        ("revision", Value::String(format!("resync-{n}"))),
                        ("snapshot", snap),
                    ]);
                    let _ = send_transport.send(&encode_result(req_id, result));
                }
                _ => {
                    let _ = send_transport.send(&encode_error(req_id, -32601, "method not found"));
                }
            }
        })));

        Self {
            transport,
            chat_channel: chat_channel.to_owned(),
            closed,
            auth_attempts,
            resync_count,
        }
    }

    /// Push a single action envelope notification.
    pub fn push_action(&self, envelope: Value) {
        let params = json_obj(&[
            ("channel", Value::String(self.chat_channel.clone())),
            ("envelope", envelope),
        ]);
        let msg = Value::Object(Map::from_iter([
            ("jsonrpc".into(), Value::String("2.0".into())),
            ("method".into(), Value::String("action".into())),
            ("params".into(), params),
        ]));
        let _ = self.transport.send(&msg.to_string());
    }

    /// Push many actions.
    pub fn push_actions(&self, envelopes: &[Value]) {
        for env in envelopes {
            self.push_action(env.clone());
        }
    }

    /// Push a raw JSON-RPC frame (CI only; used for foreign-channel filter tests).
    pub fn push_raw(&self, raw: &str) {
        let _ = self.transport.send(raw);
    }

    /// Close host.
    pub fn close(&self) {
        self.closed.set(true);
        self.transport.close();
    }

    /// Auth attempts so far.
    #[must_use]
    pub fn auth_attempts(&self) -> i32 {
        self.auth_attempts.get()
    }

    /// Resync count.
    #[must_use]
    pub fn resync_count(&self) -> i32 {
        self.resync_count.get()
    }
}

fn encode_result(id: Value, result: Value) -> String {
    Value::Object(Map::from_iter([
        ("jsonrpc".into(), Value::String("2.0".into())),
        ("id".into(), id),
        ("result".into(), result),
    ]))
    .to_string()
}

fn encode_error(id: Value, code: i64, message: &str) -> String {
    Value::Object(Map::from_iter([
        ("jsonrpc".into(), Value::String("2.0".into())),
        ("id".into(), id),
        (
            "error".into(),
            json_obj(&[
                ("code", Value::Number(code.into())),
                ("message", Value::String(message.into())),
            ]),
        ),
    ]))
    .to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use hypabolic_trajectory::{StreamPosition, update_to_value};
    use std::path::PathBuf;

    const CHAT: &str = "ahp-chat:/00000000-0000-4000-8000-0000000000c1";

    fn cases_root() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../..")
            .join("conformance/cases/streaming")
    }

    fn load_actions(case: &str, name: &str) -> Vec<Value> {
        let text = std::fs::read_to_string(cases_root().join(case).join(name)).unwrap();
        text.lines()
            .filter(|l| !l.trim().is_empty())
            .map(|l| serde_json::from_str(l).unwrap())
            .collect()
    }

    fn empty_snapshot() -> Value {
        json_obj(&[
            (
                "ahpProtocolVersion",
                Value::String(PROTOCOL_VERSION.into()),
            ),
            (
                "chat",
                json_obj(&[
                    ("id", Value::String(CHAT.into())),
                    ("turns", Value::Array(vec![])),
                    ("activeTurn", Value::Null),
                ]),
            ),
        ])
    }

    fn collect_client(
        transport: MemoryAhpTransport,
        options: AhpClientOptions,
    ) -> (AhpStreamClient, Rc<RefCell<Vec<AhpClientEvent>>>) {
        let events = Rc::new(RefCell::new(Vec::new()));
        let ev = Rc::clone(&events);
        let client = AhpStreamClient::new(Box::new(transport), options, move |e| {
            ev.borrow_mut().push(e);
        });
        (client, events)
    }

    #[test]
    fn subscribe_actions_feed_core() {
        let pair = InMemoryAhpTransportPair::new();
        let actions = load_actions("ahp-action-turn-flow", "step-actions.jsonl");
        let mut script = FakeAhpHostScript::new();
        script.initial_actions = actions;
        let host = FakeAhpHost::new(pair.host, script, CHAT);
        let (client, events) = collect_client(pair.client, AhpClientOptions::new(CHAT));
        client.start();
        {
            let events = events.borrow();
            assert!(events.iter().any(|e| e.kind == AhpClientEventKind::Ready));
            let updates: Vec<_> = events
                .iter()
                .filter(|e| e.kind == AhpClientEventKind::StreamUpdate)
                .collect();
            assert!(!updates.is_empty());
            assert_eq!(
                updates.last().unwrap().update.as_ref().unwrap().kind,
                "updated"
            );
        }
        match client.cursor().position {
            StreamPosition::AhpServerSeq(ref p) => assert_eq!(p.last_server_seq, 5),
            _ => panic!("expected ahp-server-seq"),
        }
        host.close();
        client.cancel();
    }

    #[test]
    fn auth_failure() {
        let pair = InMemoryAhpTransportPair::new();
        let mut script = FakeAhpHostScript::new();
        script.require_auth = true;
        script.accept_token = Some("good".into());
        let host = FakeAhpHost::new(pair.host, script, CHAT);
        let mut opts = AhpClientOptions::new(CHAT);
        opts.auth = Some(Box::new(|_| {
            Some(AhpAuthCredentials {
                token: "bad".into(),
            })
        }));
        let (client, events) = collect_client(pair.client, opts);
        client.start();
        {
            let events = events.borrow();
            assert!(
                events
                    .iter()
                    .any(|e| e.kind == AhpClientEventKind::AuthRequired)
            );
            assert!(
                events
                    .iter()
                    .any(|e| e.kind == AhpClientEventKind::AuthFailed)
            );
            assert!(!events.iter().any(|e| e.kind == AhpClientEventKind::Ready));
        }
        assert_eq!(host.auth_attempts(), 1);
        client.cancel();
    }

    #[test]
    fn auth_success_token_not_in_stream_update() {
        let pair = InMemoryAhpTransportPair::new();
        let mut script = FakeAhpHostScript::new();
        script.require_auth = true;
        script.accept_token = Some("secret-token-xyz".into());
        script.initial_snapshot = Some(empty_snapshot());
        let host = FakeAhpHost::new(pair.host, script, CHAT);
        let mut opts = AhpClientOptions::new(CHAT);
        opts.auth = Some(Box::new(|_| {
            Some(AhpAuthCredentials {
                token: "secret-token-xyz".into(),
            })
        }));
        let (client, events) = collect_client(pair.client, opts);
        client.start();
        {
            let events = events.borrow();
            assert!(events.iter().any(|e| e.kind == AhpClientEventKind::Ready));
            for e in events.iter().filter(|e| e.kind == AhpClientEventKind::StreamUpdate) {
                if let Some(ref update) = e.update {
                    let blob = update_to_value(update).to_string();
                    assert!(
                        !blob.contains("secret-token-xyz"),
                        "auth token must not appear in stream update JSON"
                    );
                }
            }
        }
        client.cancel();
        let _ = host;
    }

    #[test]
    fn sequence_gap_triggers_resync() {
        let pair = InMemoryAhpTransportPair::new();
        let actions = load_actions("ahp-action-turn-flow", "step-actions.jsonl");
        let mut script = FakeAhpHostScript::new();
        script.initial_actions = actions;
        script.initial_snapshot = Some(empty_snapshot());
        let host = FakeAhpHost::new(pair.host, script, CHAT);
        let (client, events) = collect_client(pair.client, AhpClientOptions::new(CHAT));
        client.start();
        let gen_before = client.cursor().generation;
        let updates_before = events
            .borrow()
            .iter()
            .filter(|e| e.kind == AhpClientEventKind::StreamUpdate)
            .count();
        let gap = load_actions("ahp-action-sequence-gap", "step-gap.jsonl");
        host.push_actions(&gap);
        // Host push enqueues client inbox; pump to process.
        client.pump();
        {
            let events = events.borrow();
            assert!(
                events
                    .iter()
                    .any(|e| e.kind == AhpClientEventKind::ResyncRequired)
            );
        }
        assert!(host.resync_count() >= 1);
        assert!(client.cursor().generation > gen_before);
        let updates_after: Vec<_> = events
            .borrow()
            .iter()
            .filter(|e| e.kind == AhpClientEventKind::StreamUpdate)
            .cloned()
            .collect();
        assert!(updates_after.len() > updates_before);
        let last_kind = updates_after
            .last()
            .and_then(|e| e.update.as_ref())
            .map(|u| u.kind.as_str())
            .unwrap_or("");
        assert!(
            last_kind == "updated" || last_kind == "unchanged",
            "expected post-resync stream-update, got {last_kind}"
        );
        client.cancel();
        assert!(client.is_cancelled());
    }

    #[test]
    fn backpressure() {
        let pair = InMemoryAhpTransportPair::new();
        let mut script = FakeAhpHostScript::new();
        script.initial_snapshot = Some(empty_snapshot());
        let host = FakeAhpHost::new(pair.host, script, CHAT);
        let mut opts = AhpClientOptions::new(CHAT);
        opts.max_buffered_actions = 2;
        let (client, events) = collect_client(pair.client, opts);
        client.start();
        client.set_paused_for_test(true);
        for i in 0..5 {
            host.push_action(json_obj(&[
                ("channel", Value::String(CHAT.into())),
                ("serverSeq", Value::Number((100 + i).into())),
                (
                    "origin",
                    json_obj(&[("kind", Value::String("server".into()))]),
                ),
                (
                    "action",
                    json_obj(&[
                        ("type", Value::String("chat/activityChanged".into())),
                        ("activity", Value::String("thinking".into())),
                    ]),
                ),
            ]));
        }
        client.pump();
        {
            let events = events.borrow();
            assert!(
                events
                    .iter()
                    .any(|e| e.kind == AhpClientEventKind::Backpressure)
            );
        }
        client.cancel();
    }

    #[test]
    fn resume_after_backpressure_flushes_buffer() {
        let pair = InMemoryAhpTransportPair::new();
        let mut script = FakeAhpHostScript::new();
        script.initial_snapshot = Some(empty_snapshot());
        let host = FakeAhpHost::new(pair.host, script, CHAT);
        let mut opts = AhpClientOptions::new(CHAT);
        opts.max_buffered_actions = 8;
        let (client, events) = collect_client(pair.client, opts);
        client.start();
        client.set_paused_for_test(true);
        for i in 0..3 {
            host.push_action(json_obj(&[
                ("channel", Value::String(CHAT.into())),
                ("serverSeq", Value::Number((1 + i).into())),
                (
                    "origin",
                    json_obj(&[("kind", Value::String("server".into()))]),
                ),
                (
                    "action",
                    json_obj(&[
                        ("type", Value::String("chat/activityChanged".into())),
                        ("activity", Value::String("thinking".into())),
                    ]),
                ),
            ]));
        }
        client.pump();
        let updates_before = events
            .borrow()
            .iter()
            .filter(|e| e.kind == AhpClientEventKind::StreamUpdate)
            .count();
        // Production recovery path: resume clears pause and flushes buffered actions.
        client.resume();
        client.pump();
        let updates_after = events
            .borrow()
            .iter()
            .filter(|e| e.kind == AhpClientEventKind::StreamUpdate)
            .count();
        assert!(
            updates_after > updates_before,
            "resume must flush buffered actions into core"
        );
        // Further pushes after resume must apply (paused must stay false).
        host.push_action(json_obj(&[
            ("channel", Value::String(CHAT.into())),
            ("serverSeq", Value::Number(4.into())),
            (
                "origin",
                json_obj(&[("kind", Value::String("server".into()))]),
            ),
            (
                "action",
                json_obj(&[
                    ("type", Value::String("chat/activityChanged".into())),
                    ("activity", Value::String("idle".into())),
                ]),
            ),
        ]));
        client.pump();
        match client.cursor().position {
            StreamPosition::AhpServerSeq(ref p) => assert!(p.last_server_seq >= 3),
            _ => panic!("expected ahp-server-seq after resume"),
        }
        client.cancel();
    }

    #[test]
    fn duplicate_action_replay_does_not_crash() {
        let pair = InMemoryAhpTransportPair::new();
        let actions = load_actions("ahp-action-turn-flow", "step-actions.jsonl");
        let mut script = FakeAhpHostScript::new();
        script.initial_actions = actions.clone();
        let host = FakeAhpHost::new(pair.host, script, CHAT);
        let (client, events) = collect_client(pair.client, AhpClientOptions::new(CHAT));
        client.start();
        host.push_actions(&actions);
        client.pump();
        {
            let events = events.borrow();
            let updates: Vec<_> = events
                .iter()
                .filter(|e| e.kind == AhpClientEventKind::StreamUpdate)
                .collect();
            assert!(!updates.is_empty());
            for e in &updates {
                let kind = e.update.as_ref().map(|u| u.kind.as_str()).unwrap_or("");
                assert!(
                    kind == "updated"
                        || kind == "unchanged"
                        || kind == "reset-required"
                        || kind == "error",
                    "unexpected update kind {kind}"
                );
            }
            assert!(!events.iter().any(|e| e.kind == AhpClientEventKind::Error));
        }
        client.cancel();
    }

    #[test]
    fn foreign_channel_action_notification_ignored() {
        let pair = InMemoryAhpTransportPair::new();
        let mut script = FakeAhpHostScript::new();
        script.initial_snapshot = Some(empty_snapshot());
        let host = FakeAhpHost::new(pair.host, script, CHAT);
        let (client, events) = collect_client(pair.client, AhpClientOptions::new(CHAT));
        client.start();
        let updates_before = events
            .borrow()
            .iter()
            .filter(|e| e.kind == AhpClientEventKind::StreamUpdate)
            .count();
        let cur_before = client.cursor();
        // Raw foreign-channel notification (not via host.push_action which pins chat).
        let foreign = Value::Object(Map::from_iter([
            ("jsonrpc".into(), Value::String("2.0".into())),
            ("method".into(), Value::String("action".into())),
            (
                "params".into(),
                json_obj(&[
                    (
                        "channel",
                        Value::String("ahp-chat:/ffffffff-ffff-4fff-8fff-ffffffffffff".into()),
                    ),
                    (
                        "envelope",
                        json_obj(&[
                            (
                                "channel",
                                Value::String(
                                    "ahp-chat:/ffffffff-ffff-4fff-8fff-ffffffffffff".into(),
                                ),
                            ),
                            ("serverSeq", Value::Number(99.into())),
                            (
                                "origin",
                                json_obj(&[("kind", Value::String("server".into()))]),
                            ),
                            (
                                "action",
                                json_obj(&[
                                    ("type", Value::String("chat/activityChanged".into())),
                                    ("activity", Value::String("foreign".into())),
                                ]),
                            ),
                        ]),
                    ),
                ]),
            ),
        ]));
        // Deliver through the same duplex as host pushes.
        host.push_raw(&foreign.to_string());
        client.pump();
        let updates_after = events
            .borrow()
            .iter()
            .filter(|e| e.kind == AhpClientEventKind::StreamUpdate)
            .count();
        assert_eq!(
            updates_after, updates_before,
            "foreign-channel action must not produce stream updates"
        );
        assert_eq!(client.cursor().generation, cur_before.generation);
        match (&client.cursor().position, &cur_before.position) {
            (StreamPosition::AhpServerSeq(a), StreamPosition::AhpServerSeq(b)) => {
                assert_eq!(a.last_server_seq, b.last_server_seq);
            }
            _ => {}
        }
        client.cancel();
    }

    #[test]
    fn cancel_keeps_cursor() {
        let pair = InMemoryAhpTransportPair::new();
        let actions = load_actions("ahp-action-turn-flow", "step-actions.jsonl");
        let mut script = FakeAhpHostScript::new();
        script.initial_actions = actions;
        let host = FakeAhpHost::new(pair.host, script, CHAT);
        let (client, _events) = collect_client(pair.client, AhpClientOptions::new(CHAT));
        client.start();
        let cur = client.cursor();
        let last = match &cur.position {
            StreamPosition::AhpServerSeq(p) => p.last_server_seq,
            _ => panic!("expected seq"),
        };
        client.cancel();
        assert!(client.is_cancelled());
        let after = client.cursor();
        assert_eq!(after.generation, cur.generation);
        match after.position {
            StreamPosition::AhpServerSeq(p) => assert_eq!(p.last_server_seq, last),
            _ => panic!("expected seq"),
        }
        host.close();
    }
}

