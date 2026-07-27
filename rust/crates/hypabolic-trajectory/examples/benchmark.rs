//! Dependency-free representative normalization and output-size benchmark.

use std::collections::BTreeMap;
use std::hint::black_box;
use std::time::Instant;

use hypabolic_trajectory::{
    NormalizeRequest, PiSourceAdapter, SourceAdapter, project_canonical, project_hypabolic,
    project_letta, project_minimal_jsonl, project_openai, project_opentelemetry,
};
use serde_json::json;

fn main() {
    const ITERATIONS: u32 = 250;
    let transcript =
        include_bytes!("../../../../conformance/cases/pi/unicode-boundaries/input.jsonl");
    let request = NormalizeRequest {
        transcript,
        source_context: Default::default(),
        options: Default::default(),
    };
    for _ in 0..10 {
        black_box(PiSourceAdapter.normalize(request).expect("warmup"));
    }
    let started = Instant::now();
    let mut trajectory = None;
    for _ in 0..ITERATIONS {
        trajectory = Some(
            PiSourceAdapter
                .normalize(request)
                .expect("benchmark normalization"),
        );
    }
    let elapsed = started.elapsed();
    let trajectory = trajectory.expect("benchmark has iterations");
    let mut outputs = BTreeMap::new();
    outputs.insert(
        "hypabolic-trajectory-v1",
        project_hypabolic(&trajectory)
            .expect("Hypabolic output")
            .len(),
    );
    outputs.insert(
        "jsonl-minimal",
        project_minimal_jsonl(&trajectory)
            .expect("minimal output")
            .len(),
    );
    outputs.insert(
        "letta-canonical-v1",
        project_canonical(&trajectory)
            .expect("canonical output")
            .len(),
    );
    outputs.insert(
        "letta-trajectory-v1",
        project_letta(&trajectory).expect("Letta output").len(),
    );
    outputs.insert(
        "openai-chat-messages",
        project_openai(&trajectory).expect("OpenAI output").len(),
    );
    outputs.insert(
        "otel-genai-spans-v1",
        project_opentelemetry(&trajectory)
            .expect("OpenTelemetry output")
            .len(),
    );
    println!(
        "{}",
        serde_json::to_string(&json!({
            "runtime": "rust",
            "iterations": ITERATIONS,
            "elapsed_ms": elapsed.as_secs_f64() * 1_000.0,
            "operations_per_second": f64::from(ITERATIONS) / elapsed.as_secs_f64(),
            "input_bytes": transcript.len(),
            "records": trajectory.records.len(),
            "output_bytes": outputs,
            "allocation_note": "stable Rust exposes no safe process-local allocation counter",
        }))
        .expect("benchmark result serializes")
    );
}
