# hypabolic-trajectory

Normalize coding-agent session transcripts into deterministic, versioned
Trajectory contracts. Independent native **Python 3.11+** runtime of the
[Hypabolic Trajectory](https://github.com/Hypabolic/Trajectory) product — same
wire contracts and shared conformance suite as .NET, TypeScript, and Rust.
**No FFI** to other language runtimes.

| | |
| --- | --- |
| **PyPI name** | `hypabolic-trajectory` (org [Hypabolic](https://pypi.org/org/Hypabolic/)) |
| **Import root** | `hypabolic_trajectory` |
| **Requires-Python** | `>=3.11` |
| **Normalizer contract** | `0.2.0` (identity-stable; embedded in canonical output) |
| **Wire package version** | `WIRE_PACKAGE_VERSION` — tip pin `"0.1.0"` (must match peer runtimes + goldens on the same tag; **not** auto-bound to package SemVer) |
| **License** | MIT |

> **Published vs tree:** NuGet / npm / crates **`0.1.0`** already ship without
> AHP. The first public PyPI cut of this package ships on the **next**
> synchronized multi-registry tag (with tip sources including AHP Shape A).
> Until that tag, install from this monorepo (editable) or wait for the tag.

---

## Package map

| Dist / extra | Import | Role |
| --- | --- | --- |
| **`hypabolic-trajectory`** (core wheel) | `hypabolic_trajectory` | Normalize, project (including **pure** `project_otel_genai`), listing, bundled contracts + `runtime-capabilities.json`, core **streaming** apply APIs, and **`hypabolic_trajectory.otel`** (`SpanSetSink` / `emit_to`, no SDK) |
| **`hypabolic-trajectory[otel]`** | same `hypabolic_trajectory.otel` | Optional OpenTelemetry **SDK sink helpers** + `opentelemetry-*` deps only. Does **not** gate pure projection or `emit_to` with a pure sink. |
| **`hypabolic-trajectory[io]`** | `hypabolic_trajectory.io` | Optional file poll/follow stream I/O (`stream-file-io`; stdlib only). Modules ship in the core wheel; extra marks install intent. |
| **`hypabolic-trajectory[ahp]`** | `hypabolic_trajectory.ahp_client` | Optional AHP live-host client (`stream-ahp-client`; stdlib only; auth via callback). |
| **`hypabolic-trajectory[hermes]`** | `hypabolic_trajectory.hermes_provider` | Optional Hermes SQLite/provider stream (`stream-hermes-provider`; stdlib `sqlite3`). Shared `hermes-provider-*` cases cover core `apply_hermes_export`; SQLite I/O is package-test-gated. |
| Conformance runner | **not published** | `python/tools/trajectory_conformance` — stdin/stdout protocol v1 for `conformance/verify.py` |
| Sample CLI | **not published** | `python/samples/trajectory_cli` — browse/list/show/**stream**/**ahp-stream** (no console script; monorepo sample only) |

Cross-ecosystem map (same product):

| Ecosystem | Core | Optional |
| --- | --- | --- |
| .NET | `Hypabolic.Trajectory` | `.OpenTelemetry`, `.Testing`, `.IO`, `.Ahp`, `.Hermes` |
| TypeScript | `@hypabolic/trajectory` | `@hypabolic/trajectory-node`, `@hypabolic/trajectory-otel`, `@hypabolic/trajectory-ahp`, `@hypabolic/trajectory-hermes` |
| Rust | `hypabolic-trajectory` | `hypabolic-trajectory-opentelemetry`, `-io`, `-ahp`, `-hermes` |
| **Python** | **`hypabolic-trajectory`** | **`[otel]`**, **`[io]`**, **`[ahp]`**, **`[hermes]`** |

---

## Install

```bash
# After first public tag (replace with the tag SemVer):
pip install hypabolic-trajectory==<tag-semver>
pip install 'hypabolic-trajectory[otel]==<tag-semver>'     # optional SDK sinks
pip install 'hypabolic-trajectory[io]==<tag-semver>'       # stream file I/O intent
pip install 'hypabolic-trajectory[ahp]==<tag-semver>'      # AHP client intent
pip install 'hypabolic-trajectory[hermes]==<tag-semver>'   # Hermes provider intent

# From this monorepo (development):
python -m pip install -e './python[dev]'
# Stream extras (optional install intent; modules already in the src tree):
python -m pip install -e './python[io,ahp,hermes,dev]'
```

The published wheel has **no** console scripts. The conformance runner and sample
CLI (`stream` / `ahp-stream` included) are monorepo-only unpublished samples.

### Sample CLI (unpublished)

```bash
# After editable install of the core package:
PYTHONPATH=python/samples python -m trajectory_cli list --source pi
PYTHONPATH=python/samples python -m trajectory_cli show \
  --source pi \
  --path conformance/cases/pi/tool-calls/input.jsonl
PYTHONPATH=python/samples python -m trajectory_cli browse
PYTHONPATH=python/samples python -m trajectory_cli stream \
  --source pi \
  --path conformance/cases/pi/tool-calls/input.jsonl \
  --max-updates 1
```

See [`samples/trajectory_cli/README.md`](samples/trajectory_cli/README.md).

---

## Supported public imports (semver-stable)

**Only** these paths are supported:

1. **Package root** `hypabolic_trajectory` — names in root `__all__`
2. **`hypabolic_trajectory.ir`** — multi-project IR surface (`ir.__all__`)
3. **`hypabolic_trajectory.otel`** — always importable from the core wheel
4. **`hypabolic_trajectory.io`** — optional file stream I/O (`[io]` extra intent)
5. **`hypabolic_trajectory.ahp_client`** — optional AHP client (`[ahp]` extra intent)
6. **`hypabolic_trajectory.hermes_provider`** — optional Hermes provider (`[hermes]` extra intent)

Any other module path (`api`, `engine`, `normalize`, `sources`, …) is
**unsupported** and may break without notice.

```python
from hypabolic_trajectory import (
    # free functions
    normalize_to_ir,
    normalize_to_letta,
    normalize_to_canonical,
    normalize_to_hypabolic,
    project_letta,
    project_canonical,
    project_hypabolic,
    project_openai,
    project_minimal_jsonl,
    project_otel_genai,
    list_trajectories,
    serialize_projection,
    canonical_json,
    # engine
    TrajectoryEngine,
    # request / errors
    NormalizeRequest,
    SourceContext,
    NormalizeOptions,
    Bounds,
    Filters,
    TrajectoryError,
    Diagnostic,
    TrajectorySource,
    # versions
    NORMALIZER_CONTRACT_VERSION,  # "0.2.0"
    WIRE_PACKAGE_VERSION,         # tip "0.1.0" until coordinated bump
    PACKAGE_VERSION,
    __version__,
)
from hypabolic_trajectory.ir import TrajectoryIR  # also re-exported at root
from hypabolic_trajectory.otel import SpanSetSink, emit_to
```

### OTEL import matrix

| Import / call | Without `opentelemetry-*` | With `[otel]` extra |
| --- | --- | --- |
| `from hypabolic_trajectory import project_otel_genai` | **Succeeds** | Succeeds |
| `from hypabolic_trajectory.otel import SpanSetSink, emit_to` | **Succeeds** | Succeeds |
| `emit_to(pure_sink, ir)` | pure project + `sink.emit`; **no** SDK | Same |
| Concrete SDK helper symbols (if shipped) | **`ImportError`** with install hint | Succeeds when deps present |
| Whole `hypabolic_trajectory.otel` package | **Always** in the core wheel | Same |

---

## Quick usage

```python
from pathlib import Path
from hypabolic_trajectory import (
    NormalizeRequest,
    TrajectorySource,
    list_trajectories,
    normalize_to_ir,
    project_hypabolic,
    project_letta,
    project_otel_genai,
    serialize_projection,
    TrajectoryEngine,
)

# Listing always takes an explicit root (no home default in library APIs).
page = list_trajectories(
    source=TrajectorySource.CLAUDE_CODE,
    root=Path.home() / ".claude" / "projects",
    limit=20,
)
session = page.items[0]

transcript = Path(session.path).read_bytes()
request = NormalizeRequest(
    source=TrajectorySource.CLAUDE_CODE,
    transcript=transcript,
)

ir = normalize_to_ir(request)
hypabolic = project_hypabolic(ir)
messages = project_letta(ir)
spans = project_otel_genai(ir)  # pure; no OTEL SDK required

# Product JSON emit (shared Trajectory string-escape; never stdlib json.dumps
# for identity/product schemas):
wire = serialize_projection(hypabolic)

# Engine (tip matrix including pure otel); free functions ignore engine mutations.
engine = TrajectoryEngine.create_default()
out = engine.project(ir, "hypabolic-trajectory-v1")
```

### Filters (normalize options)

`NormalizeOptions.filters` controls content inclusion during normalization:

| Field | Values | Default | Effect |
| --- | --- | --- | --- |
| `filters.tool_results` | `"include"` \| `"omit"` | `"include"` | When `"omit"`, tool-result record bodies are dropped while structure/diagnostics remain policy-correct |

Bounds (`options.bounds.tool_arguments` / `tool_results`) cap argument/result
character lengths with documented truncation strategies. Domain validation of
options runs at free-function / engine entry (DTO construction does not raise
`TrajectoryError`).

```python
from hypabolic_trajectory import (
    Filters,
    NormalizeOptions,
    NormalizeRequest,
    TrajectorySource,
    normalize_to_ir,
)

request = NormalizeRequest(
    source=TrajectorySource.PI,
    transcript=b"...",
    options=NormalizeOptions(filters=Filters(tool_results="omit")),
)
ir = normalize_to_ir(request)
```

---

## Dual timestamps

Source adapters and the normalizer preserve **dual timing** when present (never
fabricated):

| Clock | Field | Role |
| --- | --- | --- |
| Millisecond | `timestamp_ms` (int) | Filled / synthesized body clock used for **public** message, canonical, Hypabolic, and jsonl-minimal timestamps |
| Precise | `timestamp_precise` (str, optional) | Source-native high-resolution string when present; copied onto IR records and model invocations |

**Public timestamp formats (filled `timestamp_ms` only — never `source_timestamp_ms`):**

| Surface | Format |
| --- | --- |
| Message / canonical / Hypabolic | `yyyy-MM-ddTHH:mm:ss.fffZ` |
| Listing `updated_at` | `Z` form |
| `jsonl-minimal` | `yyyy-MM-ddTHH:mm:ss.fff+00:00` (three fractional digits; omit key when filled ms is absent) |
| OTEL span bounds | precise as-is; else ms → seven-digit pad (`Z` → `0000+00:00`) |

---

## Identity formulas and string escape

Authority: `contracts/spec/identity.md`, `contracts/spec/canonical-json.md`, and
peer goldens. Digests are **64 lowercase hex**.

| Product field | Formula |
| --- | --- |
| Hypabolic `trajectory_id` | `sha256(utf8(compact_json([source_wire_name, group_id])))` |
| Model-invocation `id` | `sha256(utf8(compact_json([group_id, identity, "model-invocation"])))` |
| Model-invocation absolute offset | checked `decoded.source_offset + base_byte_offset` (signed int64; overflow → `invalid_input`) |
| `segment.partial` | `config.partial or base_byte_offset != 0` |

**Trajectory string-escape** (shared by `canonical_json`, `serialize_projection`,
and each `project_minimal_jsonl` line):

1. Walk the string as **UTF-16 code units**.
2. Short escapes for `"`, `\\`, `\b`, `\t`, `\n`, `\f`, `\r`.
3. `\uXXXX` **four uppercase** hex for `U+0000–001F`, `U+E000–F8FF`, `U+2028`,
   `U+2029`, and surrogates; else UTF-8.
4. **Do not** escape solidus `/`; **no** Unicode normalization; no BOM.

Only **identity hashing** sorts object keys by UTF-16 code unit order via
`canonical_json`. Product `serialize_projection` preserves insertion order.

---

## Capabilities and sources

Advertised surface is the tip matrix in
[`python/runtime-capabilities.json`](runtime-capabilities.json) (must equal
`contracts/compatibility.json` and peer TS/Rust manifests at ship):

- **Sources:** `pi`, `claude-code`, `codex`, `openclaw`, `hermes`, `ahp` (Shape A offline ChatState snapshot; listing Phase 3 stub), `grok-build`, `cursor`
- **Outputs:** `letta-trajectory-v1`, `letta-canonical-v1`, `hypabolic-trajectory-v1`, `openai-chat-messages`, `jsonl-minimal`, `otel-genai-spans-v1`
- **Capabilities:** `normalize`, `normalize-partial`, `list-explicit-root`, `typed-diagnostics`, `typed-fatal-errors`, `deterministic-rerun`
- **Slice:** `ML13` (historical id; AHP is a post-`0.1.0` source addition on tip)

Do **not** treat `IMPLEMENTED_SOURCES` as a registry claim — the capabilities
file is authoritative for CI and advertising.

---

## Conformance (filtered runner argv)

Protocol v1 runner (unpublished): `python/tools/trajectory_conformance`.

```bash
# Editable install + unit tests
python -m pip install -e './python[dev]'
python -m pytest python/tests -q

# Tip suite (unfiltered verify defaults to compatibility tip set):
python conformance/verify.py --repository-root . -- \
  env PYTHONPATH=python/src:python/tools python -m trajectory_conformance

# Progressive / filtered argv from claimed capabilities (fail-closed when ⊂ tip):
python conformance/verify.py --repository-root . \
  $(python tools/conformance_argv_from_capabilities.py --repository-root .) \
  -- \
  env PYTHONPATH=python/src:python/tools python -m trajectory_conformance

# Manual filters while iterating:
python conformance/verify.py --repository-root . \
  --source pi --operation normalize-letta --operation normalize-canonical -- \
  env PYTHONPATH=python/src:python/tools python -m trajectory_conformance
```

When claimed sources/outputs are a **proper subset** of tip, CI **must** pass
explicit `--source` / `--operation` filters (generator: `tools/conformance_argv_from_capabilities.py`).
Never run unfiltered verify while claimed ⊂ tip.

Identity baseline:

```bash
sha256sum --check conformance/identity-baseline.sha256
```

---

## Version pins

| Symbol / field | Value / rule |
| --- | --- |
| `NORMALIZER_CONTRACT_VERSION` | `"0.2.0"` — wire identity contract |
| `WIRE_PACKAGE_VERSION` | `"0.1.0"` tip pin until all runtimes + goldens move together |
| `PACKAGE_VERSION` / `__version__` | From installed dist metadata (stamp-synced with root `VERSION`) |
| Canonical `normalizer_version` | Contract version `0.2.0` (never package SemVer) |
| Hypabolic `normalizer.version` | `WIRE_PACKAGE_VERSION` |
| OTEL `instrumentation_version` | `WIRE_PACKAGE_VERSION` |

---

## Development

From the monorepo root:

```bash
python -m pip install -e './python[dev]'
python -m pytest python/tests -q
```

Wire authority: `contracts/` and `conformance/`. Product docs:
[`docs/architecture.md`](../docs/architecture.md),
[`docs/live-session-streaming.md`](../docs/live-session-streaming.md).

## License

MIT — Copyright 2026 Hypabolic
