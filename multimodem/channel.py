"""Shared-RF-channel arbitration.

There is exactly one radio behind this multiplexer. Only one backend
modem may key PTT or hold an active connection at a time, and no other
modem may start a new (non-connected/unproto) transmission while one is
in progress. This module is the single source of truth for that state.
"""
from __future__ import annotations

import enum
import logging
import threading
from typing import Callable

log = logging.getLogger(__name__)


class ChannelState(enum.Enum):
    IDLE = "idle"
    BUSY = "busy"          # carrier/connection activity, not ours
    CONNECTED = "connected"  # a modem holds an active link-layer connection
    TRANSMITTING = "transmitting"  # PTT keyed


class ChannelBusyError(Exception):
    pass


class ChannelArbiter:
    """Thread-safe gatekeeper for the single shared RF channel."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state = ChannelState.IDLE
        self._owner: str | None = None  # modem name holding the channel
        # Optional hardware sink: called synchronously as on_ptt(True)/
        # on_ptt(False) whenever request_ptt/release_ptt actually key or
        # unkey PTT for their caller. None (the default) means there is
        # no radio attached -- arbitration-only, as before this existed.
        # Assigned directly by main.py after construction, e.g.
        # `channel.on_ptt = driver.set_ptt`.
        self.on_ptt: Callable[[bool], None] | None = None

    @property
    def state(self) -> ChannelState:
        with self._lock:
            return self._state

    @property
    def owner(self) -> str | None:
        with self._lock:
            return self._owner

    def mark_busy(self, owner: str) -> None:
        with self._lock:
            if self._owner not in (None, owner):
                return  # someone else already reported activity; ignore
            self._state = ChannelState.BUSY
            self._owner = owner

    def mark_idle(self, owner: str) -> None:
        with self._lock:
            if self._owner == owner:
                self._state = ChannelState.IDLE
                self._owner = None

    def acquire_connection(self, owner: str) -> None:
        with self._lock:
            if self._owner not in (None, owner):
                raise ChannelBusyError(
                    f"channel held by {self._owner}, cannot connect for {owner}"
                )
            self._state = ChannelState.CONNECTED
            self._owner = owner

    def release_connection(self, owner: str) -> None:
        with self._lock:
            if self._owner == owner:
                self._state = ChannelState.IDLE
                self._owner = None

    def request_ptt(self, owner: str) -> bool:
        """Return True if PTT may be keyed for `owner` right now.

        If `owner` already holds the channel via an active session
        (CONNECTED), this doesn't touch state/ownership -- it's treated
        as that session keying hardware for one more frame, not a fresh
        acquisition -- so ARQ's per-frame PTT ON doesn't need to be
        suppressed to avoid clobbering the session's own reservation.
        If a hardware driver is attached and it fails to key, the
        request is rolled back and refused (fail closed: better to
        refuse the transmission than report success with no carrier).
        """
        with self._lock:
            if self._owner == owner and self._state == ChannelState.CONNECTED:
                pass  # session already owns the channel; just key hardware
            elif self._owner not in (None, owner):
                return False
            else:
                self._state = ChannelState.TRANSMITTING
                self._owner = owner

        if self.on_ptt:
            try:
                self.on_ptt(True)
            except Exception:
                log.exception("PTT hardware key failed for %s, refusing request", owner)
                with self._lock:
                    if self._owner == owner and self._state == ChannelState.TRANSMITTING:
                        self._state = ChannelState.IDLE
                        self._owner = None
                return False
        return True

    def release_ptt(self, owner: str) -> None:
        """Unkey PTT for `owner`.

        If `owner` holds the channel via an active session (CONNECTED),
        state/ownership are left alone -- this is a per-frame hardware
        unkey between ARQ frames, not the session ending (that's
        release_connection's job). Only a TRANSMITTING hold (acquired
        via request_ptt outside of a session) is actually freed here.
        """
        with self._lock:
            if self._owner != owner:
                return
            if self._state == ChannelState.TRANSMITTING:
                self._state = ChannelState.IDLE
                self._owner = None

        if self.on_ptt:
            try:
                self.on_ptt(False)
            except Exception:
                log.exception(
                    "PTT hardware unkey failed for %s -- possible stuck transmitter!", owner
                )

    def is_available(self) -> bool:
        with self._lock:
            return self._state == ChannelState.IDLE
