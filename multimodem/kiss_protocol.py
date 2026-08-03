"""KISS framing (RFC-ish de facto standard used by TNCs and VARA's KISS port)."""
from __future__ import annotations

FEND = 0xC0
FESC = 0xDB
TFEND = 0xDC
TFESC = 0xDD

CMD_DATA = 0x00


def kiss_escape(data: bytes) -> bytes:
    out = bytearray()
    for b in data:
        if b == FEND:
            out += bytes((FESC, TFEND))
        elif b == FESC:
            out += bytes((FESC, TFESC))
        else:
            out.append(b)
    return bytes(out)


def kiss_unescape(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == FESC and i + 1 < len(data) and data[i + 1] in (TFEND, TFESC):
            out.append(FEND if data[i + 1] == TFEND else FESC)
            i += 2
        else:
            out.append(b)
            i += 1
    return bytes(out)


def encode_kiss_frame(payload: bytes, port: int = 0) -> bytes:
    cmd = (port & 0x0F) << 4 | CMD_DATA
    return bytes((FEND, cmd)) + kiss_escape(payload) + bytes((FEND,))


class KissDecoder:
    """Streaming KISS decoder: feed raw bytes, get back data-frame payloads.

    FEND delimits frames; a data frame's first (unescaped) byte is a
    port/command nibble pair which is stripped here, since callers just
    want the AX.25 frame contents.
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        frames = []
        for b in data:
            if b == FEND:
                if self._buf:
                    unescaped = kiss_unescape(bytes(self._buf))
                    if unescaped and (unescaped[0] & 0x0F) == CMD_DATA:
                        frames.append(unescaped[1:])
                    self._buf = bytearray()
            else:
                self._buf.append(b)
        return frames
