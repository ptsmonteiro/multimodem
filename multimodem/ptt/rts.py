"""Serial RTS-line PTT keying -- the same scheme Direwolf's "PTT <port> RTS"
config line and most rigctld serial-PTT backends use.

Some Digirig/USB-serial radio interfaces key PTT off the RTS handshake line
of their onboard USB-serial adapter instead of (or in addition to) the CM108
GPIO pin -- if a working Direwolf/rigctld config for the same interface uses
"RTS" rather than a CM108 GPIO, use this driver instead of cm108.
"""
from __future__ import annotations

import logging

import serial

from ..config import PttControlConfig
from . import PttDriver, PttError

log = logging.getLogger(__name__)

DEFAULT_BAUD = 9600


class RtsPttDriver(PttDriver):
    def __init__(self, config: PttControlConfig) -> None:
        self.serial_port = config.serial_port
        self.baud = config.baud if config.baud is not None else DEFAULT_BAUD
        self.active_high = config.active_high if config.active_high is not None else True
        self._serial: serial.Serial | None = None

    def start(self) -> None:
        try:
            # idle (unkeyed) level set before open(), so PTT never glitches
            # keyed for the instant the port opens.
            ser = serial.Serial()
            ser.port = self.serial_port
            ser.baudrate = self.baud
            ser.rts = not self.active_high
            ser.dtr = False
            ser.open()
            self._serial = ser
        except serial.SerialException as exc:
            raise PttError(f"cannot open rts serial port {self.serial_port}: {exc}") from exc
        log.info(
            "rts PTT driver open on %s @ %d baud (active_high=%s)",
            self.serial_port, self.baud, self.active_high,
        )

    def stop(self) -> None:
        if not self._serial:
            return
        try:
            self.set_ptt(False)
        except PttError:
            log.exception("rts: failed to unkey on shutdown")
        self._serial.close()
        self._serial = None

    def set_ptt(self, on: bool) -> None:
        if not self._serial:
            raise PttError("rts serial port not open")
        try:
            self._serial.rts = on == self.active_high
        except (serial.SerialException, OSError) as exc:
            raise PttError(f"rts serial I/O failed: {exc}") from exc
