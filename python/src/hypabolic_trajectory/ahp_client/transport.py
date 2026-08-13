"""Injectable AHP message duplex (JSON-RPC text frames).

Core stream packages never import this module. Real WebSocket adapters wrap
:class:`AhpTransport`; tests use :class:`InMemoryAhpTransportPair`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

MessageHandler = Callable[[str], None]


@runtime_checkable
class AhpTransport(Protocol):
    """Bidirectional text-frame transport for AHP JSON-RPC."""

    def send(self, message: str) -> None:
        """Send one complete JSON-RPC text frame to the peer."""

    def set_handler(self, handler: MessageHandler | None) -> None:
        """Register the inbound frame handler (replaces any previous handler)."""

    def close(self) -> None:
        """Close the duplex; further sends may raise."""


@dataclass
class MemoryAhpTransport:
    """One side of an in-memory duplex used by :class:`InMemoryAhpTransportPair`."""

    _peer: MemoryAhpTransport | None = field(default=None, repr=False)
    _handler: MessageHandler | None = field(default=None, repr=False)
    _closed: bool = False
    sent: list[str] = field(default_factory=list)

    def bind_peer(self, peer: MemoryAhpTransport) -> None:
        self._peer = peer

    def send(self, message: str) -> None:
        if self._closed:
            raise RuntimeError("transport_closed")
        self.sent.append(message)
        peer = self._peer
        if peer is None or peer._closed:
            return
        handler = peer._handler
        if handler is not None:
            handler(message)

    def set_handler(self, handler: MessageHandler | None) -> None:
        self._handler = handler

    def close(self) -> None:
        self._closed = True
        self._handler = None

    @property
    def closed(self) -> bool:
        return self._closed


@dataclass
class InMemoryAhpTransportPair:
    """Linked client/host transports for fake-host CI tests."""

    client: MemoryAhpTransport = field(default_factory=MemoryAhpTransport)
    host: MemoryAhpTransport = field(default_factory=MemoryAhpTransport)

    def __post_init__(self) -> None:
        self.client.bind_peer(self.host)
        self.host.bind_peer(self.client)
