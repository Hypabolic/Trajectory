# hypabolic-trajectory

Normalize coding-agent transcripts into deterministic Trajectory contracts.

**Supported public import surface (semver-stable):**

- Package root `hypabolic_trajectory` — names re-exported via explicit `__all__`
- `hypabolic_trajectory.ir` — multi-project IR surface (lands with later issues)
- `hypabolic_trajectory.otel` — OTEL sink Protocol + `emit_to` (pure project in core)

Only names listed in root `__all__`, `hypabolic_trajectory.ir.__all__`, and
`hypabolic_trajectory.otel.__all__` are semver-stable. Other import paths are
unsupported and may break without notice.

## Install

```bash
pip install hypabolic-trajectory
# optional OpenTelemetry SDK sink adapters:
pip install 'hypabolic-trajectory[otel]'
```

## Development

From the monorepo `python/` directory:

```bash
pip install -e '.[dev]'
pytest
```

## License

MIT — Copyright 2026 Hypabolic
