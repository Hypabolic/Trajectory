"""JSONL complete-line framing for stream apply (contracts/spec/streaming.md §9)."""

from __future__ import annotations

# LF (0x0A). CRLF is normalized by stripping a trailing CR from each complete line.
_LF = 0x0A
_CR = 0x0D


def split_complete_lines(data: bytes) -> tuple[bytes, bytes]:
    """Split *data* into committed complete-line prefix and pending incomplete tail.

    Ordinary apply commits only LF-terminated lines. The committed prefix retains
    original line terminators (CRLF becomes LF-only after CR strip per line body
    is decoder responsibility; framing keeps raw bytes up through each LF).

    Returns ``(committed_prefix, pending_tail)`` where ``committed + pending ==
    data`` only when no CR stripping is needed on the wire buffer — pending is
    always a suffix of *data*, and committed is the prefix of *data* ending at
    the last LF (inclusive). CR stripping for decode is applied by the source
    adapters on complete lines.
    """
    if not data:
        return b"", b""
    last_lf = data.rfind(bytes((_LF,)))
    if last_lf < 0:
        return b"", data
    return data[: last_lf + 1], data[last_lf + 1 :]


def append_framed(
    pending: bytes,
    segment: bytes,
    *,
    max_pending_bytes: int | None = None,
    max_line_bytes: int | None = None,
) -> tuple[bytes, bytes]:
    """Append *segment* to *pending* and emit newly completed lines.

    Returns ``(complete_segment, new_pending)``.
    Raises ``ValueError`` with code-like message keys on buffer limits
    (caller maps to typed stream errors).

    ``max_pending_bytes`` is enforced on the *post-frame* pending tail only
    (parity with .NET/TS/Rust). A complete-line segment larger than the limit
    is accepted when the resulting pending tail is within the limit.
    ``max_line_bytes`` still applies to each complete line and the pending tail.
    """
    buf = pending + segment
    complete, new_pending = split_complete_lines(buf)
    if max_line_bytes is not None:
        start = 0
        for i, b in enumerate(complete):
            if b == _LF:
                line_len = i - start + 1
                if line_len > max_line_bytes:
                    raise ValueError("stream_buffer_limit:line")
                start = i + 1
        if len(new_pending) > max_line_bytes:
            raise ValueError("stream_buffer_limit:line")
    if max_pending_bytes is not None and len(new_pending) > max_pending_bytes:
        raise ValueError("stream_buffer_limit:pending")
    return complete, new_pending
