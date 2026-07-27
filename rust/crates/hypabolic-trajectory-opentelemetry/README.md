# Hypabolic Trajectory OpenTelemetry

Optional deterministic GenAI span projection for
[`hypabolic-trajectory`](../hypabolic-trajectory/README.md).

The crate is deliberately separate from the core runtime. It provides the
contract projection and a small sink boundary without selecting or pulling an
OpenTelemetry SDK. Applications can bridge `emit_to` into the SDK and exporter
versions they already own.
