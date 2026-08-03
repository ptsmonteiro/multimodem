"""Shared-RF-channel arbitration.

There is exactly one radio behind this multiplexer. Only one backend
modem may key PTT or hold an active connection at a time, and no other
modem may start a new (non-connected/unproto) transmission while one is
in progress. This module is the single source of truth for that state.
"""
from __future__ import annotations

import enum
import threading


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
        """Return True if PTT may be keyed for `owner` right now."""
        with self._lock:
            if self._owner not in (None, owner):
                return False
            self._state = ChannelState.TRANSMITTING
            self._owner = owner
            return True

    def release_ptt(self, owner: str) -> None:
        with self._lock:
            if self._owner == owner:
                self._state = ChannelState.IDLE
                self._owner = None

    def is_available(self) -> bool:
        with self._lock:
            return self._state == ChannelState.IDLE
