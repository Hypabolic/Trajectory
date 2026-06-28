# Trajectory TypeScript

This workspace is the independent TypeScript implementation of Trajectory. It
targets Node.js 22 and newer and is authored only from the Hypabolic contracts
and shared conformance cases.

The pinned `letta-ai/trajectory` release is a black-box compatibility oracle.
Its implementation source must not be copied, translated, vendored, imported,
or used as this workspace's module structure.

ML2 starts with four package boundaries:

- `@hypabolic/trajectory`: byte-oriented core, Pi normalization, identity, and
  projections;
- `@hypabolic/trajectory-node`: explicit-root local-store listing;
- `@hypabolic/trajectory-otel`: optional OpenTelemetry projection and emission;
- `@hypabolic/trajectory-testing`: conformance and adapter-authoring helpers.

Node filesystem APIs, SQLite, and OpenTelemetry dependencies stay outside the
core package.
