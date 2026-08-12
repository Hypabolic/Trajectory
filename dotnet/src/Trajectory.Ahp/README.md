# Hypabolic.Trajectory.Ahp

Optional Agent Host Protocol (AHP) **live-host client** for Trajectory.

- **Transport only:** JSON-RPC over an injected `IAhpTransport` (in-memory fake host for CI; WebSocket adapters are consumer-owned).
- **Auth via callback:** tokens never enter stream snapshots, deltas, or diagnostics.
- **Feeds core only:** `ApplyAhpSnapshot` / `ApplyAhpActions` on `Hypabolic.Trajectory`.
- **Sequence gaps** emit `resync-required` and optionally request a host resync.

Not referenced by the core package. See `docs/ahp-client.md`.
