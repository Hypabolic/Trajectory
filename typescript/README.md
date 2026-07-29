# Trajectory TypeScript

This workspace is the independent TypeScript implementation of Trajectory. It
targets Node.js 22 and newer and is authored only from the Hypabolic contracts
and shared conformance cases.

The pinned `letta-ai/trajectory` release is a black-box compatibility oracle.
Its implementation source must not be copied, translated, vendored, imported,
or used as this workspace's module structure.

The implementation retains these package boundaries:

- `@hypabolic/trajectory`: byte-oriented core, Pi, Claude Code, Codex, OpenClaw,
  and Hermes normalization, lossless source-native invocation metadata,
  identity, and projections;
- `@hypabolic/trajectory-node`: explicit-root local-store listing for implemented
  sources;
- `@hypabolic/trajectory-otel`: optional OpenTelemetry projection and emission;
- `@hypabolic/trajectory-testing`: conformance and adapter-authoring helpers;
- `@hypabolic/trajectory-cli`: unpublished local sample TUI for browsing agent
  sessions (private workspace package).

Node filesystem APIs, SQLite, and OpenTelemetry dependencies stay outside the
core package.

## Build and verify

```bash
npm ci
npm run typecheck
npm test
```

The test command builds all four packages, exercises typed failures and partial
segments, validates `runtime-capabilities.json`, and runs every advertised
shared operation through the private runner twice.
From the repository root, the runner can also be invoked directly:

```bash
python3 conformance/verify.py --repository-root . -- \
  node typescript/packages/trajectory-testing/dist/cli.js
```

ML13 advertises Pi, Claude Code, Codex, OpenClaw, and Hermes with all six
deterministic outputs. `minimalJsonlChunks` and `writeMinimalJsonl` provide
incremental output without materializing the complete JSONL document. The same
private protocol and language-neutral cases are used by every runtime; source
filters remain useful for focused development. Packages remain unpublished;
see [docs/release-readiness.md](../docs/release-readiness.md).

## Core API

Pass exact bytes when identity or byte anchors matter:

```ts
import {
  normalizeToIR,
  normalizeToCanonical,
  normalizeToHypabolic,
  normalizeToLetta,
} from "@hypabolic/trajectory";

const request = {
  source: "codex" as const,
  transcriptBytes,
  sourceContext: {
    groupId: "session-id",
    baseByteOffset: 0n,
    partial: false,
  },
};

const ir = normalizeToIR(request);
const letta = normalizeToLetta(request);
const canonical = normalizeToCanonical(request);
const hypabolic = normalizeToHypabolic(request);
```

String transcript input is a UTF-8 convenience. The decoder always computes
anchors from the encoded bytes, never from UTF-16 string indices.

## Sample CLI

The private `@hypabolic/trajectory-cli` package lists local agent stores and
prints privacy-safe trajectory summaries. See
[packages/trajectory-cli/README.md](packages/trajectory-cli/README.md).

```bash
npm run build
node packages/trajectory-cli/dist/cli.js list --source pi
node packages/trajectory-cli/dist/cli.js show \
  --source pi \
  --path ../conformance/cases/pi/tool-calls/input.jsonl
```
