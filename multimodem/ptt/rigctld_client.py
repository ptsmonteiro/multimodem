"""Forwards granted PTT requests to an externally-managed hamlib rigctld.

Lets multimodem act purely as the shared-channel arbiter while a real
hamlib rigctld -- any rig model hamlib supports (CAT, CM108, etc.) --
does the actual keying. Start that rigctld separately, e.g.:

    rigctld -m <hamlib model number> -r /dev/ttyUSB0 -t 4533

and point ptt_control.port at its port. That's a different endpoint
from multimodem's own [rigctld] section (the server multimodem exposes
to clients) -- don't reuse the same port for both on one host.
"""
from __future__ import annotations

import logging
import socket

from ..config import PttControlConfig
from . import PttDriver, PttError

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 3.0
COMMAND_TIMEOUT_S = 2.0


class RigctldPttDriver(PttDriver):
    def __init__(self, config: PttControlConfig) -> None:
        self.host = config.host
        self.port = config.port
        self._sock: socket.socket | None = None

    def start(self) -> None:
        try:
            sock = socket.create_connection((self.host, self.port), timeout=CONNECT_TIMEOUT_S)
        except OSError as exc:
            raise PttError(f"cannot connect to rigctld at {self.host}:{self.port}: {exc}") from exc
        sock.settimeout(COMMAND_TIMEOUT_S)
        self._sock = sock
        log.info("rigctld PTT driver connected to %s:%s", self.host, self.port)

    def stop(self) -> None:
        if not self._sock:
            return
        try:
            self.set_ptt(False)
        except PttError:
            log.exception("rigctld: failed to unkey on shutdown")
        self._sock.close()
        self._sock = None

    def set_ptt(self, on: bool) -> None:
        if not self._sock:
            raise PttError("rigctld connection not open")
        cmd = f"\\set_ptt {1 if on else 0}\n"
        try:
            self._sock.sendall(cmd.encode("ascii"))
            reply = self._sock.recv(256).decode("ascii", "ignore")
        except OSError as exc:
            raise PttError(f"rigctld I/O failed: {exc}") from exc
        if "RPRT 0" not in reply:
            raise PttError(f"rigctld refused set_ptt: {reply.strip()!r}")
