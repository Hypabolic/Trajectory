"""Optional Hermes SQLite/provider streaming (LS-07h).

Queries session rows (never byte-tails ``state.db``) and feeds pure core
``apply_hermes_export``. Core packages stay SQLite-free.

**Not imported by the core package root.** Consumers opt in explicitly::

    from hypabolic_trajectory.hermes_provider import (
        HermesProviderStream,
        MemoryHermesStore,
        SqliteHermesProvider,
    )

Capability ``stream-hermes-provider`` is advertised only on this optional
module (``package-capabilities.json``), never on core runtime-capabilities.
"""

from __future__ import annotations

from hypabolic_trajectory.hermes_provider.provider import (
    HOST_DB_ERROR,
    HOST_SESSION_NOT_FOUND,
    HOST_STORE_REQUIRED,
    HermesHostError,
    HermesProviderOptions,
    HermesProviderStream,
    HermesSessionInfo,
    HermesStore,
    MemoryHermesStore,
    SqliteHermesProvider,
    compute_change_token,
    export_session_json,
)

__all__ = [
    "HOST_DB_ERROR",
    "HOST_SESSION_NOT_FOUND",
    "HOST_STORE_REQUIRED",
    "HermesHostError",
    "HermesProviderOptions",
    "HermesProviderStream",
    "HermesSessionInfo",
    "HermesStore",
    "MemoryHermesStore",
    "SqliteHermesProvider",
    "compute_change_token",
    "export_session_json",
]
