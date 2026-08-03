"""AGWPE (AGW Packet Engine) frame encoding/decoding.

Frame layout (little-endian), 36-byte header + variable payload:
    port      : 1 byte
    reserved1 : 3 bytes
    data_kind : 1 byte (ascii char, e.g. b'X', b'C', b'K')
    reserved2 : 1 byte
    pid       : 1 byte
    reserved3 : 1 byte
    call_from : 10 bytes (null-padded ascii)
    call_to   : 10 bytes (null-padded ascii)
    data_len  : 4 bytes (uint32)
    user_reserved : 4 bytes
    data      : data_len bytes
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

HEADER_STRUCT = struct.Struct("<B3xc1xc1x10s10sLL")
HEADER_SIZE = HEADER_STRUCT.size  # 36 bytes


@dataclass
class AgwFrame:
    port: int
    data_kind: bytes  # single byte, e.g. b'X'
    pid: bytes = b"\x00"
    call_from: str = ""
    call_to: str = ""
    data: bytes = b""

    def encode(self) -> bytes:
        header = HEADER_STRUCT.pack(
            self.port,
            self.data_kind,
            self.pid,
            self.call_from.encode("ascii", "ignore")[:10].ljust(10, b"\x00"),
            self.call_to.encode("ascii", "ignore")[:10].ljust(10, b"\x00"),
            len(self.data),
            0,
        )
        return header + self.data

    @classmethod
    def decode_header(cls, header: bytes) -> tuple["AgwFrame", int]:
        """Decode header, return (partial frame without data, data_len)."""
        if len(header) != HEADER_SIZE:
            raise ValueError(f"expected {HEADER_SIZE} byte header, got {len(header)}")
        (
            port,
            data_kind,
            pid,
            call_from,
            call_to,
            data_len,
            _user_reserved,
        ) = HEADER_STRUCT.unpack(header)
        frame = cls(
            port=port,
            data_kind=data_kind,
            pid=pid,
            call_from=call_from.rstrip(b"\x00").decode("ascii", "ignore"),
            call_to=call_to.rstrip(b"\x00").decode("ascii", "ignore"),
        )
        return frame, data_len


async def read_frame(reader) -> AgwFrame | None:
    """Read one AGWPE frame from an asyncio StreamReader. None on EOF."""
    header = await reader.readexactly(HEADER_SIZE)
    frame, data_len = AgwFrame.decode_header(header)
    if data_len:
        frame.data = await reader.readexactly(data_len)
    return frame
