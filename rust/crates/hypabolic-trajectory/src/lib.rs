#![forbid(unsafe_code)]
#![doc = "Native Rust implementation of the language-neutral Trajectory contracts."]

mod ahp_reducer;
mod canonical;
mod listing;
mod model;
mod normalize;
mod projection;
mod streaming;

pub use canonical::{canonical_json, relaxed_json};
pub use listing::{
    ListingOptions, TrajectoryListing, TrajectoryListingPage, list_ahp_trajectories,
    list_claude_code_trajectories, list_codex_trajectories, list_grok_build_trajectories,
    list_hermes_trajectories, list_openclaw_trajectories, list_pi_trajectories,
};
pub use model::{
    AppliedConfig, Bounds, Diagnostic, Filters, IrRecord, ModelInvocation, ModelTokenUsage,
    NormalizeOptions, NormalizeRequest, Provenance, RecordHashes, RecordKind, Role, SourceContext,
    ToolCall, Trajectory, TrajectoryError, TrajectoryExecution, TrajectorySource,
    TruncationStrategy,
};
pub use normalize::{
    AhpSourceAdapter, ClaudeCodeSourceAdapter, CodexSourceAdapter, GrokBuildSourceAdapter,
    HermesSourceAdapter, OpenClawSourceAdapter, PiSourceAdapter, SourceAdapter, normalize_ahp,
    normalize_claude_code, normalize_codex, normalize_grok_build, normalize_hermes,
    normalize_openclaw, normalize_pi,
};
pub use projection::{
    OutputAdapter, canonical_value, hypabolic_value, letta_value, openai_value,
    opentelemetry_value, project_canonical, project_hypabolic, project_letta,
    project_minimal_jsonl, project_openai, project_opentelemetry, project_schema,
    serialize_projection, write_minimal_jsonl, write_schema,
};
pub use streaming::{
    AhpServerSeqPosition, BytePosition, HermesRowPosition, STREAM_SCHEMA_ID,
    SnapshotRevisionPosition, StreamConsumed, StreamCursor, StreamDelivery, StreamDelta,
    StreamInput, StreamInputKind, StreamOptions, StreamPosition, StreamProvisionalInfo,
    StreamRecord, StreamReset, StreamResetPolicy, StreamResetRequest, StreamRevision,
    StreamSnapshot, StreamState, JSON_SAFE_INTEGER_MAX, JSON_SAFE_INTEGER_MIN,
    StreamUpdate, TrajectoryStream, apply_ahp_actions, apply_ahp_snapshot, apply_append,
    apply_delta_to_snapshot, apply_hermes_export, apply_snapshot, apply_stream, create_stream,
    delta_to_value, diagnostic_key, diff_snapshots, finish_stream, match_key_value,
    project_stream_diagnostic, reset_stream, snapshot_to_value, split_complete_lines,
    stream_diagnostic_message, stream_error_message, update_to_value,
};

/// Language-neutral normalizer contract version.
pub const NORMALIZER_CONTRACT_VERSION: &str = "0.2.0";

/// Public output schema identifiers implemented by the Rust runtime.
pub mod schema_ids {
    /// Compact message-trajectory record array.
    pub const LETTA_TRAJECTORY_V1: &str = "letta-trajectory-v1";
    /// Canonical identity records (stable ids, hashes, ordering).
    pub const LETTA_CANONICAL_V1: &str = "letta-canonical-v1";
    /// Provenance-rich Hypabolic trajectory document.
    pub const HYPOBOLIC_TRAJECTORY_V1: &str = "hypabolic-trajectory-v1";
    /// OpenAI-style chat message projection.
    pub const OPENAI_CHAT_MESSAGES_V1: &str = "openai-chat-messages";
    /// Streaming minimal JSONL projection.
    pub const MINIMAL_JSONL_V1: &str = "jsonl-minimal";
    /// Deterministic OpenTelemetry `GenAI` span-set projection.
    pub const OTEL_GENAI_SPANS_V1: &str = "otel-genai-spans-v1";
}
