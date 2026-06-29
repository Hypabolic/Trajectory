#![forbid(unsafe_code)]
#![doc = "Native Rust implementation of the language-neutral Trajectory contracts."]

mod canonical;
mod listing;
mod model;
mod normalize;
mod projection;

pub use canonical::{canonical_json, relaxed_json};
pub use listing::{
    ListingOptions, TrajectoryListing, TrajectoryListingPage, list_claude_code_trajectories,
    list_codex_trajectories, list_pi_trajectories,
};
pub use model::{
    AppliedConfig, Bounds, Diagnostic, Filters, IrRecord, ModelInvocation, ModelTokenUsage,
    NormalizeOptions, NormalizeRequest, Provenance, RecordHashes, RecordKind, Role, SourceContext,
    ToolCall, Trajectory, TrajectoryError, TrajectoryExecution, TrajectorySource,
    TruncationStrategy,
};
pub use normalize::{
    ClaudeCodeSourceAdapter, CodexSourceAdapter, PiSourceAdapter, SourceAdapter,
    normalize_claude_code, normalize_codex, normalize_pi,
};
pub use projection::{
    OutputAdapter, canonical_value, hypabolic_value, letta_value, openai_value,
    opentelemetry_value, project_canonical, project_hypabolic, project_letta,
    project_minimal_jsonl, project_openai, project_opentelemetry, project_schema,
    serialize_projection, write_minimal_jsonl, write_schema,
};

/// Language-neutral normalizer contract version.
pub const NORMALIZER_CONTRACT_VERSION: &str = "0.2.0";

/// Public output schema identifiers implemented by the Rust runtime.
pub mod schema_ids {
    /// Letta trajectory output.
    pub const LETTA_TRAJECTORY_V1: &str = "letta-trajectory-v1";
    /// Letta canonical output.
    pub const LETTA_CANONICAL_V1: &str = "letta-canonical-v1";
    /// Hypabolic trajectory output.
    pub const HYPOBOLIC_TRAJECTORY_V1: &str = "hypabolic-trajectory-v1";
    /// `OpenAI` chat message output.
    pub const OPENAI_CHAT_MESSAGES_V1: &str = "openai-chat-messages";
    /// Streaming minimal JSONL output.
    pub const MINIMAL_JSONL_V1: &str = "jsonl-minimal";
    /// Deterministic OpenTelemetry `GenAI` span-set output.
    pub const OTEL_GENAI_SPANS_V1: &str = "otel-genai-spans-v1";
}
