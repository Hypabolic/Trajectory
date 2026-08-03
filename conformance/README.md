# Trajectory conformance

This directory contains the language-neutral behavioral evidence for
Trajectory. `contracts/` is normative; these cases make those rules executable.
No runtime owns or copies a shared input or golden.

## Case layout

Each `cases/<source>/<name>/case.json` declares the native input, source
context, normalization options, mode, required capabilities, expected outcome,
and one or more operations. Every operation names an immutable checked-in
expected file and comparison mode.

Comparison modes are:

- `json-exact`: parsed JSON values and array order must match;
- `byte-exact`: UTF-8 text must match exactly;
- `jsonl-exact`: JSONL bytes, record order, escaping, and final newline match.

Fatal cases compare the typed `{ "code", "message" }` object. Listing cases
reference a declarative fixture under `stores/`; the runner builds a temporary
explicit root and replaces its path with `$ROOT` before comparison.

## Adding a case

1. Choose the smallest sanitized native fixture that exposes one behavior.
2. Add `case.json` and the native input.
3. Run a trusted implementation and inspect the candidate result separately.
4. Check in the expected output only after review. Never run a command that
   generates and accepts a golden in the same workflow.
5. Validate the manifest and schemas, then run all implementations.
6. If the behavior changes identity, hashes, diagnostics, or existing bytes,
   version the affected contract before accepting the change.

Implementation-specific parser/unit fixtures belong under that runtime, not
here. Shared cases are copied directly into runtime test output only for test
execution; they remain authoritative at this path.

## Fixture sanitization and privacy

Shared fixtures are synthetic or rewritten so they never carry real user
secrets. Required rules:

- no live API keys, tokens, passwords, cookies, or private keys;
- no production home directories, org names, emails, or hostnames — use
  portable placeholders such as `/workspace/demo` and fixed session IDs;
- no pasting of production prompts, tool arguments, or tool results;
- multimodal or base64-looking payloads must be non-functional synthetic data;
- listing cases use declarative stores under `stores/`; comparison rewrites the
  temporary root to `$ROOT` so developer paths never appear in goldens;
- diagnostic and fatal messages obey content-safety rules in
  `contracts/spec/diagnostics.md` (no transcript prose, raw JSON, secrets, or
  paths).

When behaviour is discovered against a real transcript,
rewrite it into a minimal sanitized vector before check-in. See
[docs/release-readiness.md](../docs/release-readiness.md) for the ML13 privacy
review and product release policy.

## .NET runner

Build the solution, then send one protocol request:

```bash
printf '%s' '{"protocol_version":"1","case":"pi/tool-calls","operation":"normalize-canonical","repository_root":"'"$PWD"'"}' \
  | dotnet dotnet/tests/Trajectory.Conformance/bin/Release/net10.0/trajectory-conformance.dll
```

The executable writes exactly one structured response to stdout. Runtime logs
belong on stderr. It is private test infrastructure, not a supported RPC API.

Run the full declared suite and deterministic reruns with:

```bash
python3 conformance/verify.py --repository-root . -- \
  dotnet dotnet/tests/Trajectory.Conformance/bin/Release/net10.0/trajectory-conformance.dll
```

## TypeScript runner

Build the workspace, then run every advertised shared case:

```bash
cd typescript && npm ci && npm run build && cd ..
python3 conformance/verify.py --repository-root . -- \
  node typescript/packages/trajectory-testing/dist/cli.js
```

`--source` is repeatable and selects only manifests a runtime currently
advertises. `--operation` is also repeatable and selects specific operations.
Omitting either filter runs the full advertised set. The TypeScript runner is
private test infrastructure in `@hypabolic/trajectory-testing`; it is not a
public interchange API.

## Rust runner

Build the workspace and run every shared case for sources advertised in
`contracts/compatibility.json` on this tip (includes `ahp` Shape A on `main`;
published tag `v0.1.0` omitted `ahp`):

```bash
cargo +stable build --manifest-path rust/Cargo.toml \
  --release --bin trajectory-conformance
python3 conformance/verify.py --repository-root . -- \
  rust/target/release/trajectory-conformance
```

Focused filters remain available (`--source pi --source hermes`,
`--operation normalize-letta`, and so on). The executable is unpublished
private test infrastructure. All three runtimes share the same request and
response schemas; runners add operations and sources without a protocol
redesign.
