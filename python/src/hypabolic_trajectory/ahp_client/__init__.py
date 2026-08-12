"""Optional AHP live-host client (LS-10).

Transport-only package surface for connecting to an Agent Host Protocol host,
authenticating via an injected callback, subscribing to a chat channel, and
feeding pure core ``apply_ahp_snapshot`` / ``apply_ahp_actions``.

**Not imported by the core package root.** Consumers opt in explicitly::

    from hypabolic_trajectory.ahp_client import AhpStreamClient, FakeAhpHost

Auth tokens never enter stream snapshots, deltas, or diagnostics.
"""

from __future__ import annotations

from hypabolic_trajectory.ahp_client.client import (
    AhpAuthCallback,
    AhpAuthCredentials,
    AhpClientEvent,
    AhpClientOptions,
    AhpStreamClient,
)
from hypabolic_trajectory.ahp_client.fake_host import FakeAhpHost, FakeAhpHostScript
from hypabolic_trajectory.ahp_client.transport import (
    AhpTransport,
    InMemoryAhpTransportPair,
    MemoryAhpTransport,
)

__all__ = [
    "AhpAuthCallback",
    "AhpAuthCredentials",
    "AhpClientEvent",
    "AhpClientOptions",
    "AhpStreamClient",
    "AhpTransport",
    "FakeAhpHost",
    "FakeAhpHostScript",
    "InMemoryAhpTransportPair",
    "MemoryAhpTransport",
]
