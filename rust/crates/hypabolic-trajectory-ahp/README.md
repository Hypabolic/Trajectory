# hypabolic-trajectory-ahp

Optional Agent Host Protocol (AHP) **live-host client** for Trajectory.

- **Transport only:** JSON-RPC over an injected `AhpTransport` (in-memory fake host for CI).
- **Auth via callback:** tokens never enter stream snapshots, deltas, or diagnostics.
- **Feeds core only:** `apply_ahp_snapshot` / `apply_ahp_actions`.
- **Sequence gaps** emit `resync-required` and optionally request a host resync.

Not a dependency of the core crate. See `docs/ahp-client.md`.
