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

/// Fully normalized Pi trajectory.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Trajectory {
    /// Resolved source group.
    pub group_id: String,
    /// Producer version if present.
    pub producer_version: Option<String>,
    /// Synthetic meta followed by normalized body records.
    pub records: Vec<IrRecord>,
    /// Recoverable diagnostics.
    pub diagnostics: Vec<Diagnostic>,
    /// Applied configuration.
    pub config: AppliedConfig,
}
