# Hypabolic.Trajectory.Hermes

Optional Hermes **SQLite/provider** streaming for Trajectory (LS-07h).

- **Query rows** in a read transaction — never byte-tail `state.db`
- **Change token** over ordered active message fingerprints
- **Feeds core only:** `ApplyHermesExport` on `Hypabolic.Trajectory`
- Soft-delete / DB generation change → `reset-required` (cursor unchanged until explicit reset)
- In-memory store for CI; `SqliteHermesProvider` for real `state.db` paths

Core packages stay SQLite-free. Capability `stream-hermes-provider` is not advertised until LS-12.
