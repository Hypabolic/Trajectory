# AHP vendor pin

Trajectory pins the Agent Host Protocol family used for AHP source contracts
and synthetic conformance fixtures.

| File | Purpose |
| --- | --- |
| `PROTOCOL_VERSION` | SemVer string matching AHP `PROTOCOL_VERSION` at pin time |

## Current pin

- **Protocol:** `0.7.0` (see AHP `types/version/registry.ts` /
  `PROTOCOL_VERSION`)
- **Schema reference:** [AHP `schema/state.schema.json`](https://github.com/microsoft/agent-host-protocol/tree/main/schema)
- **Trajectory export envelope:** `contracts/schemas/ahp-export-v1.schema.json`
- **Normative decode rules:** `contracts/spec/sources/ahp.md`

## Bumping the pin

1. Review AHP changelog for ChatState / response-part / tool-call breaks.
2. Update `PROTOCOL_VERSION` and fixture `ahpProtocolVersion` fields.
3. Refresh mapping notes in `contracts/spec/sources/ahp.md` if field names or
   semantics change.
4. Re-review synthetic fixtures under `conformance/cases/ahp/`.
5. Expand the runtime version allow-list only after multi-runtime cases pass.

Do **not** vend real host exports, workspace paths, or secrets here. Fixtures
use synthetic `ahp-chat:/…` URIs only.

Action-log (Shape B) reducer fixtures and schema digests may be added when
Phase 2 lands; snapshot Shape A is the Phase 0–1 pin surface.
