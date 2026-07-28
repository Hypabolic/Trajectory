use std::error::Error;
use std::fmt::{self, Display, Formatter};

use serde::{Deserialize, Serialize};

/// A typed fatal normalization or listing error.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TrajectoryError {
    /// Stable diagnostic contract code.
    pub code: String,
    /// Content-safe human-readable message.
    pub message: String,
}

impl TrajectoryError {
    /// Creates a typed fatal error.
    #[must_use]
    pub fn new(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
        }
    }
}

impl Display for TrajectoryError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        write!(formatter, "{}: {}", self.code, self.message)
    }
}

impl Error for TrajectoryError {}

/// Exact byte-oriented normalization request.
#[derive(Debug, Clone, Copy)]
pub struct NormalizeRequest<'a> {
    /// Original UTF-8 transcript bytes.
    pub transcript: &'a [u8],
    /// Caller-supplied source context.
    pub source_context: SourceContext<'a>,
    /// Resolved normalization inputs.
    pub options: NormalizeOptions,
}

/// Caller-supplied source identity and segmentation context.
#[derive(Debug, Clone, Copy, Default)]
pub struct SourceContext<'a> {
    /// Optional source group identifier.
    pub group_id: Option<&'a str>,
    /// Absolute byte offset of this transcript segment.
    pub base_byte_offset: i64,
    /// Whether whole-transcript validation is relaxed.
    pub partial: bool,
}

/// User-configurable normalization policy.
#[derive(Debug, Clone, Copy, Default)]
pub struct NormalizeOptions {
    /// Optional argument maximum; outer `None` uses the default and inner `None` disables it.
    pub tool_arguments_max_characters: Option<Option<usize>>,
    /// Optional result maximum; outer `None` uses the default and inner `None` disables it.
    pub tool_results_max_characters: Option<Option<usize>>,
    /// Result truncation strategy.
    pub tool_results_strategy: Option<TruncationStrategy>,
    /// Whether linked tool results are emitted.
    pub include_tool_results: Option<bool>,
}

/// Marker-inclusive result truncation strategy.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum TruncationStrategy {
    /// Retain the maximum prefix.
    Head,
    /// Retain a balanced prefix and suffix.
    HeadTail,
}

impl TruncationStrategy {
    pub(crate) const fn wire_name(self) -> &'static str {
        match self {
            Self::Head => "head",
            Self::HeadTail => "head-tail",
        }
    }
}

/// Fully resolved normalization configuration.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AppliedConfig {
    /// Resolved bounds.
    pub bounds: Bounds,
    /// Resolved filters.
    pub filters: Filters,
    /// Owned source group supplied by the caller.
    pub source_group_id: Option<String>,
    /// Source segment base byte offset.
    pub base_byte_offset: i64,
    /// Explicit caller partial flag.
    pub partial: bool,
}

/// Resolved bounds.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Bounds {
    /// Tool argument scalar maximum.
    pub tool_arguments_max_characters: Option<usize>,
    /// Tool result scalar maximum.
    pub tool_results_max_characters: Option<usize>,
    /// Result truncation strategy.
    pub tool_results_strategy: TruncationStrategy,
}

/// Resolved output filters.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Filters {
    /// Whether linked tool results are emitted.
    pub include_tool_results: bool,
}

/// Recoverable normalization diagnostic.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Diagnostic {
    /// Stable code.
    pub code: String,
    /// Content-safe message.
    pub message: String,
    /// One-based source line when applicable.
    #[serde(rename = "inputLine", skip_serializing_if = "Option::is_none")]
    pub input_line: Option<usize>,
    /// One-based normalized record position when applicable.
    #[serde(rename = "recordIndex", skip_serializing_if = "Option::is_none")]
    pub record_index: Option<usize>,
    /// Aggregate count when applicable.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub count: Option<usize>,
}

/// Built-in transcript source.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TrajectorySource {
    /// Pi session JSONL.
    Pi,
    /// Claude Code project JSONL.
    ClaudeCode,
    /// Codex rollout JSONL.
    Codex,
    /// OpenClaw session JSONL (Pi-family).
    OpenClaw,
}

impl TrajectorySource {
    pub(crate) const fn wire_name(self) -> &'static str {
        match self {
            Self::Pi => "pi",
            Self::ClaudeCode => "claude-code",
            Self::Codex => "codex",
            Self::OpenClaw => "openclaw",
        }
    }
}

/// Normalized role.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Role {
    /// Synthetic metadata.
    Meta,
    /// User message.
    User,
    /// Model reasoning.
    Reasoning,
    /// Assistant message or tool call.
    Assistant,
    /// Tool result.
    Tool,
}

impl Role {
    pub(crate) const fn wire_name(self) -> &'static str {
        match self {
            Self::Meta => "meta",
            Self::User => "user",
            Self::Reasoning => "reasoning",
            Self::Assistant => "assistant",
            Self::Tool => "tool",
        }
    }
}

/// Private IR record family exposed for typed Rust projection.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RecordKind {
    /// Synthetic metadata.
    Meta,
    /// Text or reasoning message.
    Message,
    /// Assistant tool call record.
    AssistantToolCalls,
    /// Tool result record.
    ToolResult,
}

impl RecordKind {
    pub(crate) const fn wire_name(self) -> &'static str {
        match self {
            Self::Meta => "meta",
            Self::Message => "message",
            Self::AssistantToolCalls => "assistant_tool_calls",
            Self::ToolResult => "tool_result",
        }
    }
}

/// Normalized tool call.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ToolCall {
    /// Final linked ID.
    pub id: String,
    /// Tool name.
    pub name: String,
    /// Valid JSON object string.
    pub arguments_json: String,
}

/// Durable source provenance.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Provenance {
    /// Durable source record identity.
    pub stable_source_record_id: String,
    /// `native`, `location`, or `synthetic`.
    pub source_identity_kind: &'static str,
    /// Source ordering identity.
    pub source_order_id: String,
    /// Semantic component identity.
    pub component_key: String,
    /// Component position in the source occurrence.
    pub component_index: usize,
    /// Same-type ordinal in the source occurrence.
    pub component_type_ordinal: usize,
    /// Native record ID if present.
    pub native_record_id: Option<String>,
    /// Producer version attached to the native occurrence.
    pub producer_version: Option<String>,
    /// Source record sequence.
    pub source_sequence: Option<i64>,
    /// Zero-based byte location within the supplied segment.
    pub source_offset: Option<i64>,
    /// `byte` for Pi JSONL anchors.
    pub source_anchor_kind: Option<&'static str>,
}

/// Identity-bearing hashes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RecordHashes {
    /// Canonical semantic content hash.
    pub content_sha256: String,
    /// Canonical Letta record hash.
    pub record_sha256: String,
}

/// Token usage retained for deterministic telemetry projection.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct ModelTokenUsage {
    /// Input tokens.
    pub input_tokens: Option<i64>,
    /// Output tokens.
    pub output_tokens: Option<i64>,
    /// Cache-read input tokens.
    pub cache_read_tokens: Option<i64>,
    /// Cache-write input tokens.
    pub cache_write_tokens: Option<i64>,
    /// Provider-reported total tokens.
    pub total_tokens: Option<i64>,
}

/// One source-native model invocation retained in the private IR.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ModelInvocation {
    /// Deterministic invocation ID.
    pub id: String,
    /// Native source record ID.
    pub native_record_id: Option<String>,
    /// Absolute source byte offset.
    pub source_offset: Option<i64>,
    /// Provider name.
    pub provider: Option<String>,
    /// Source API family.
    pub api_family: Option<String>,
    /// Caller-requested model.
    pub requested_model: Option<String>,
    /// Response model.
    pub response_model: Option<String>,
    /// Provider response ID.
    pub response_id: Option<String>,
    /// Provider stop reason.
    pub stop_reason: Option<String>,
    /// Producer version on this invocation.
    pub producer_version: Option<String>,
    /// Token usage when present.
    pub usage: Option<ModelTokenUsage>,
    /// Request start time in Unix milliseconds.
    pub started_at_ms: Option<i64>,
    /// Source-native seven-digit request start representation.
    pub started_at_precise: Option<String>,
    /// Completion time in Unix milliseconds.
    pub completed_at_ms: Option<i64>,
    /// Source-native seven-digit UTC representation.
    pub completed_at_precise: Option<String>,
}

/// Execution metadata retained separately from normalized records.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct TrajectoryExecution {
    /// Model invocations decoded from the source.
    pub model_invocations: Vec<ModelInvocation>,
}

/// One normalized private-IR record.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IrRecord {
    /// Public deterministic record ID.
    pub id: String,
    /// Record family.
    pub kind: RecordKind,
    /// Semantic role.
    pub role: Role,
    /// Segment-local order.
    pub order: i64,
    /// Source-native Unix time in milliseconds.
    pub source_timestamp_ms: Option<i64>,
    /// Source-native seven-digit UTC representation, when present.
    pub source_timestamp_precise: Option<String>,
    /// Filled Unix time in milliseconds.
    pub timestamp_ms: Option<i64>,
    /// Text content.
    pub content: Option<String>,
    /// Source name for metadata.
    pub source_name: Option<String>,
    /// Working directory for metadata.
    pub cwd: Option<String>,
    /// Git branch for metadata.
    pub git_branch: Option<String>,
    /// Selected model for metadata.
    pub model: Option<String>,
    /// Producer version for metadata.
    pub producer_version: Option<String>,
    /// Tool calls.
    pub tool_calls: Vec<ToolCall>,
    /// Linked tool call ID.
    pub tool_call_id: Option<String>,
    /// Tool name retained on result records.
    pub tool_name: Option<String>,
    /// Source error flag.
    pub is_error: Option<bool>,
    /// Durable provenance.
    pub provenance: Provenance,
    /// Canonical hashes.
    pub hashes: RecordHashes,
}

/// Fully normalized trajectory.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Trajectory {
    /// Native transcript source.
    pub source: TrajectorySource,
    /// Language-neutral source name.
    pub source_name: String,
    /// Resolved source group.
    pub group_id: String,
    /// Whether the source group came from native or caller-supplied context.
    pub source_group_resolved: bool,
    /// Producer version if present.
    pub producer_version: Option<String>,
    /// Synthetic meta followed by normalized body records.
    pub records: Vec<IrRecord>,
    /// Recoverable diagnostics.
    pub diagnostics: Vec<Diagnostic>,
    /// Source-native execution metadata for later projections.
    pub execution: TrajectoryExecution,
    /// Applied configuration.
    pub config: AppliedConfig,
}
