# Upstream compatibility reference

The .NET implementation of Trajectory is behaviorally pinned to:

- repository: `letta-ai/trajectory`;
- commit: `f165ecf0af35da40512a288c4380a36b3102403c`;
- package version: `0.2.0`;
- reference state: merge of upstream Pi adapter PR #24;
- reviewed: 2026-07-24.

This commit is the authoritative reference for fixtures, defaults, diagnostics, source adapters, listing behaviour, schemas, and canonical identity until an intentional compatibility update changes the pin.

## Updating the pin

A pull request that updates the upstream pin must:

1. compare public APIs, schemas, version constants, defaults, diagnostics, and source adapters;
2. import or regenerate sanitized fixtures for changed behaviour;
3. run the complete differential parity suite against the old and new pins;
4. document intentional output changes and migration implications;
5. decide whether the Hypabolic schema or its hashing rules are affected;
6. update this file, the parity baseline, and any affected implementation slices.

An upstream package-version change alone is not sufficient reason to move the pin. The repository commit is the compatibility identity.

Known intentional differences between Trajectory and this pin (implementation
independence, v1 source set, OpenClaw delivery-mirror masking, Hermes
SQLite-free core listing, canonical JSON algorithm, Hypabolic output, and
optional OTEL packages) are recorded in
[release-readiness.md](release-readiness.md).
