"""Icom CI-V PTT keying over a USB-serial CAT connection (e.g. IC-705).

Sends the CI-V "transceiver PTT" command (0x1C 0x00) to key/unkey PTT
directly over CAT, so no separate PTT wiring (GPIO/RTS) is needed --
just the radio's USB CAT/CDC serial port. Frame format and default
addresses are from Icom's public CI-V reference:
https://www.icomeurope.com/wp-content/uploads/2020/08/IC-705_ENG_CI-V_1_20200721.pdf

NOTE: not bench-tested against physical hardware in this change --
confirm radio_address/baud against your actual radio before relying on
it (both are shown on the radio's own CI-V settings menu).
"""
from __future__ import annotations

import logging

import serial
from serial.tools import list_ports

from ..config import PttControlConfig
from . import PttDriver, PttError

log = logging.getLogger(__name__)

ICOM_VENDOR_ID = 0x0C26
DEFAULT_RADIO_ADDRESS = 0xA4  # IC-705 default CI-V address
CONTROLLER_ADDRESS = 0xE0
FRAME_START = b"\xfe\xfe"
FRAME_END = b"\xfd"
CMD_TRANSCEIVER_PTT = b"\x1c\x00"
OK_SUFFIX = b"\xfb\xfd"
NG_SUFFIX = b"\xfa\xfd"

READ_TIMEOUT_S = 1.0


def select_civ_port(ports: list) -> str:
    """Pick the one serial port that's an Icom CI-V interface.

    `ports` is whatever serial.tools.list_ports.comports() returns (or,
    in tests, anything with matching .device/.vid/.location/.hwid
    attributes). Icom radios often enumerate more than one USB-serial
    interface (e.g. the IC-705's CI-V port plus a second serial port),
    sharing one vendor/product id -- when that happens this narrows to
    interface 0 (the CI-V port) if it can tell interfaces apart, and
    otherwise refuses rather than guess.
    """
    icom_ports = [p for p in ports if getattr(p, "vid", None) == ICOM_VENDOR_ID]
    if not icom_ports:
        raise PttError(
            "no Icom CI-V USB device found; set ptt_control.serial_port explicitly"
        )
    if len(icom_ports) == 1:
        return icom_ports[0].device

    first_interface = [p for p in icom_ports if _is_first_interface(p)]
    if len(first_interface) == 1:
        return first_interface[0].device

    candidates = ", ".join(p.device for p in icom_ports)
    raise PttError(
        f"multiple Icom USB serial devices found ({candidates}); "
        "set ptt_control.serial_port explicitly to select the CI-V interface"
    )


def _is_first_interface(port) -> bool:
    # macOS/Linux (pyserial list_ports_posix): location looks like
    # "1-1.4:1.0", where the part after ':' is bConfigurationValue.
    # bInterfaceNumber -- ".0" is interface 0.
    location = getattr(port, "location", None)
    if location and ":" in location:
        iface = location.rsplit(".", 1)[-1]
        if iface.isdigit():
            return int(iface) == 0
    # Windows (pyserial list_ports_windows): composite devices carry
    # "MI_00"/"MI_02" etc in the hardware id.
    hwid = getattr(port, "hwid", "") or ""
    if "MI_" in hwid:
        return "MI_00" in hwid
    return False


class CivPttDriver(PttDriver):
    def __init__(self, config: PttControlConfig) -> None:
        self.serial_port = config.serial_port
        self.baud = config.baud
        self.radio_address = (
            config.radio_address if config.radio_address is not None else DEFAULT_RADIO_ADDRESS
        )
        self._serial: serial.Serial | None = None

    def start(self) -> None:
        port = self.serial_port or select_civ_port(list_ports.comports())
        try:
            self._serial = serial.Serial(port, self.baud, timeout=READ_TIMEOUT_S)
        except serial.SerialException as exc:
            raise PttError(f"cannot open civ serial port {port}: {exc}") from exc
        log.info(
            "civ PTT driver open on %s @ %d baud (radio addr %#04x)",
            port, self.baud, self.radio_address,
        )

    def stop(self) -> None:
        if not self._serial:
            return
        try:
            self.set_ptt(False)
        except PttError:
            log.exception("civ: failed to unkey on shutdown")
        self._serial.close()
        self._serial = None

    def set_ptt(self, on: bool) -> None:
        if not self._serial:
            raise PttError("civ serial port not open")
        frame = (
            FRAME_START
            + bytes([self.radio_address, CONTROLLER_ADDRESS])
            + CMD_TRANSCEIVER_PTT
            + bytes([0x01 if on else 0x00])
            + FRAME_END
        )
        try:
            self._serial.reset_input_buffer()
            self._serial.write(frame)
            self._serial.flush()
            reply = self._serial.read(64)
        except serial.SerialException as exc:
            raise PttError(f"civ serial I/O failed: {exc}") from exc
        if OK_SUFFIX in reply:
            return
        if NG_SUFFIX in reply:
            raise PttError("radio replied NG to CI-V PTT command")
        raise PttError(f"no CI-V ack from radio (got {reply!r})")
