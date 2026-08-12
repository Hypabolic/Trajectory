# hypabolic-trajectory-hermes

Optional Hermes **SQLite/provider** streaming for Trajectory (LS-07h).

- Query session rows in a read transaction — never byte-tail `state.db`
- Change token over ordered active message fingerprints
- Feeds core only: `apply_hermes_export`
- Soft-delete / DB generation change → `reset-required`
- `MemoryHermesStore` for CI; `SqliteHermesProvider` behind the default `sqlite` feature

Core crate stays SQLite-free. Capability `stream-hermes-provider` is advertised
only on this optional package (`package-capabilities.json`), never on core.
