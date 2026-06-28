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

Future Rust and TypeScript implementations provide executables that consume the
same request schema and produce the same response schema. The verifier command
is then repeated with each runner; no case or protocol redesign is required.
