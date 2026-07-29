# Trajectory TypeScript

Independent TypeScript implementation of Trajectory for Node.js 22+.

| Package | Role |
| --- | --- |
| `@hypabolic/trajectory` | Core normalize + project (byte-oriented) |
| `@hypabolic/trajectory-node` | Explicit-root local store listing |
| `@hypabolic/trajectory-otel` | Optional OpenTelemetry GenAI projection |
| `@hypabolic/trajectory-testing` | Private conformance runner (unpublished) |
| `@hypabolic/trajectory-cli` | Private sample TUI (unpublished) |

## Install

```bash
npm install @hypabolic/trajectory
npm install @hypabolic/trajectory-node   # listing helpers
npm install @hypabolic/trajectory-otel   # optional spans
```

## Build and verify (from this workspace)

```bash
npm ci
npm run typecheck
npm test
```

Shared conformance from the repo root:

```bash
python3 conformance/verify.py --repository-root . -- \
  node typescript/packages/trajectory-testing/dist/cli.js
```

## Core API

Pass exact bytes when identity or byte anchors matter:

```ts
import {
  normalizeToIR,
  normalizeToCanonical,
  normalizeToHypabolic,
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
const canonical = normalizeToCanonical(request);
const hypabolic = normalizeToHypabolic(request);
```

String transcript input is a UTF-8 convenience. Anchors are always computed
from encoded bytes, never from UTF-16 string indices.

## Sample CLI

```bash
npm run build
node packages/trajectory-cli/dist/cli.js list --source pi
```

See [packages/trajectory-cli/README.md](packages/trajectory-cli/README.md).

## Further reading

- [Root README](../README.md)
- [Architecture](../docs/architecture.md)
- [Publishing](../docs/publishing.md)
