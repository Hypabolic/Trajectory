#![forbid(unsafe_code)]
#![doc = "Optional OpenTelemetry `GenAI` integration boundary for Hypabolic Trajectory."]

use hypabolic_trajectory::{Trajectory, TrajectoryError, opentelemetry_value};
use serde_json::Value;

/// Deterministic OpenTelemetry `GenAI` schema version.
pub const OTEL_GENAI_SCHEMA_VERSION: &str = "1";

/// Application-owned bridge to an OpenTelemetry `SDK` or exporter.
pub trait SpanSetSink {
    /// Sink-specific error.
    type Error;

    /// Receives one complete deterministic span-set value.
    fn emit(&mut self, span_set: &Value) -> Result<(), Self::Error>;
}

/// Projects a trajectory without requiring an OpenTelemetry `SDK` dependency.
pub fn project(trajectory: &Trajectory) -> Result<Value, TrajectoryError> {
    opentelemetry_value(trajectory)
}

/// Projects once and passes the span set to an application-owned `SDK` bridge.
pub fn emit_to<S: SpanSetSink>(
    sink: &mut S,
    trajectory: &Trajectory,
) -> Result<(), EmitError<S::Error>> {
    let span_set = project(trajectory).map_err(EmitError::Projection)?;
    sink.emit(&span_set).map_err(EmitError::Sink)
}

/// Typed projection or sink failure.
#[derive(Debug)]
pub enum EmitError<E> {
    /// Deterministic projection failed.
    Projection(TrajectoryError),
    /// The application-owned sink rejected the span set.
    Sink(E),
}

#[cfg(test)]
mod tests {
    use hypabolic_trajectory::{NormalizeRequest, PiSourceAdapter, SourceAdapter};
    use serde_json::Value;

    use super::{SpanSetSink, emit_to};

    #[derive(Default)]
    struct CapturingSink {
        values: Vec<Value>,
    }

    impl SpanSetSink for CapturingSink {
        type Error = ();

        fn emit(&mut self, span_set: &Value) -> Result<(), Self::Error> {
            self.values.push(span_set.clone());
            Ok(())
        }
    }

    #[test]
    fn application_owned_sink_receives_one_deterministic_span_set() {
        let trajectory = PiSourceAdapter
            .normalize(NormalizeRequest {
                transcript: include_bytes!(
                    "../../../../conformance/cases/pi/tool-calls/input.jsonl"
                ),
                source_context: Default::default(),
                options: Default::default(),
            })
            .expect("shared Pi fixture normalizes");
        let mut sink = CapturingSink::default();
        emit_to(&mut sink, &trajectory).expect("sink accepts projection");
        assert_eq!(sink.values.len(), 1);
        assert_eq!(
            sink.values[0]["instrumentation_scope"],
            "Hypabolic.Trajectory.OpenTelemetry"
        );
    }
}
