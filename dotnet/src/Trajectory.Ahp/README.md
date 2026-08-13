# Hypabolic.Trajectory.Ahp

Optional Agent Host Protocol (AHP) **live-host client** for Trajectory.

- **Transport only:** JSON-RPC over an injected `IAhpTransport` (in-memory fake host for CI; WebSocket adapters are consumer-owned).
- **Auth via callback:** tokens never enter stream snapshots, deltas, or diagnostics.
- **Feeds core only:** `ApplyAhpSnapshot` / `ApplyAhpActions` on `Hypabolic.Trajectory`.
- **Sequence gaps** emit `resync-required` and optionally request a host resync.

**Native AOT / trim:** this package is `IsAotCompatible` / `IsTrimmable`. JSON-RPC
framing uses `JsonNode.WriteTo` (no reflection `JsonSerializer.Serialize`).
Release builds treat `IL2026` / `IL3050` as errors.

Not referenced by the core package. See `docs/ahp-client.md`.
