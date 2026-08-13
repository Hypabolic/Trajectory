# Hypabolic.Trajectory.Hermes

Optional Hermes **SQLite/provider** streaming for Trajectory (LS-07h).

- **Query rows** in a read transaction — never byte-tail `state.db`
- **Change token** over ordered active message fingerprints
- **Feeds core only:** `ApplyHermesExport` on `Hypabolic.Trajectory`
- Soft-delete / DB generation change → `reset-required` (cursor unchanged until explicit reset)
- In-memory store for CI; `SqliteHermesProvider` for real `state.db` paths

**Not Native AOT / trim compatible.** `IsAotCompatible` and `IsTrimmable` are
**false**. The provider uses `Microsoft.Data.Sqlite` plus a native SQLite build
(`SQLitePCLRaw.lib.e_sqlite3` 3.50.3+, pinned to clear NU1903 / CVE-2025-6965).
AOT consumers should keep SQLite out of the native publish graph and feed core
`ApplyHermesExport` themselves (or use `MemoryHermesStore` only in JIT/dev).

Core packages stay SQLite-free. Capability `stream-hermes-provider` is advertised
only on this optional package (`package-capabilities.json`), never on core.
