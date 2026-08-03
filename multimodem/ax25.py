"""Minimal AX.25 address field and UI-frame (unproto) encode/decode.

Only what's needed for unproto/UI traffic (APRS et al.) -- connected
mode (I/S frames, sequencing) is handled by the modem's own native
connect/data API elsewhere, not by us re-implementing AX.25 link layer.
"""
from __future__ import annotations

UI_CONTROL = 0x03
PID_NO_LAYER3 = 0xF0


def split_callsign(call: str) -> tuple[str, int]:
    if "-" in call:
        base, ssid_str = call.rsplit("-", 1)
        try:
            return base.upper(), int(ssid_str)
        except ValueError:
            pass
    return call.upper(), 0


def join_callsign(base: str, ssid: int) -> str:
    return f"{base}-{ssid}" if ssid else base


def encode_address(call: str, ssid: int, last: bool, c_bit: bool = False) -> bytes:
    base = call.upper().ljust(6)[:6]
    field = bytearray(b << 1 for b in base.encode("ascii", "ignore"))
    ssid_byte = 0x60 | ((ssid & 0x0F) << 1)  # reserved bits set per convention
    if c_bit:
        ssid_byte |= 0x80
    if last:
        ssid_byte |= 0x01
    field.append(ssid_byte)
    return bytes(field)


def decode_address(field: bytes) -> tuple[str, int, bool]:
    call = "".join(chr(b >> 1) for b in field[:6]).strip()
    ssid_byte = field[6]
    ssid = (ssid_byte >> 1) & 0x0F
    last = bool(ssid_byte & 0x01)
    return call, ssid, last


def encode_ui_frame(
    dest: str, source: str, payload: bytes, digipeaters: list[str] | None = None
) -> bytes:
    digipeaters = digipeaters or []
    dest_base, dest_ssid = split_callsign(dest)
    src_base, src_ssid = split_callsign(source)

    addrs = bytearray()
    addrs += encode_address(dest_base, dest_ssid, last=False, c_bit=True)
    addrs += encode_address(src_base, src_ssid, last=not digipeaters)
    for i, digi in enumerate(digipeaters):
        d_base, d_ssid = split_callsign(digi)
        addrs += encode_address(d_base, d_ssid, last=(i == len(digipeaters) - 1))

    return bytes(addrs) + bytes((UI_CONTROL, PID_NO_LAYER3)) + payload


def decode_ui_frame(frame: bytes) -> tuple[str, str, list[str], bytes] | None:
    """Returns (dest, source, digipeaters, payload), or None if not a UI frame."""
    pos = 0
    addrs = []
    while True:
        if pos + 7 > len(frame):
            return None
        call, ssid, last = decode_address(frame[pos : pos + 7])
        addrs.append(join_callsign(call, ssid))
        pos += 7
        if last:
            break
        if len(addrs) > 10:  # sanity cap, AX.25 allows at most 2 + 8 digis
            return None

    if pos + 2 > len(frame) or len(addrs) < 2:
        return None
    control, pid = frame[pos], frame[pos + 1]
    if control != UI_CONTROL:
        return None  # connected-mode I/S frames aren't our concern here

    dest, source, digipeaters = addrs[0], addrs[1], addrs[2:]
    return dest, source, digipeaters, frame[pos + 2 :]
