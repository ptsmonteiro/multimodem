"""Hardware PTT keying drivers, selected by config.ptt_control.driver.

ChannelArbiter calls a driver's set_ptt() directly and synchronously
from request_ptt/release_ptt (see channel.py), so implementations must
return quickly -- a few ms of local HID/serial/socket I/O is fine, but
nothing that can block indefinitely, since that would stall whichever
event loop/thread granted PTT.
"""
from __future__ import annotations

import abc

from ..config import PttControlConfig


class PttError(Exception):
    """Raised by a driver when it fails to key or unkey the radio."""


class PttDriver(abc.ABC):
    def start(self) -> None:
        """Open the underlying device. No-op unless overridden."""

    def stop(self) -> None:
        """Force PTT off and release the underlying device. No-op unless overridden."""

    @abc.abstractmethod
    def set_ptt(self, on: bool) -> None:
        """Key (on=True) or unkey (on=False) the radio. Raises PttError on failure."""


class NullPttDriver(PttDriver):
    """driver = "none" (the default): arbitration only, no radio attached."""

    def set_ptt(self, on: bool) -> None:
        pass


def build_ptt_driver(config: PttControlConfig) -> PttDriver:
    if config.driver == "none":
        return NullPttDriver()
    if config.driver == "cm108":
        from .cm108 import Cm108PttDriver

        return Cm108PttDriver(config)
    if config.driver == "civ":
        from .civ import CivPttDriver

        return CivPttDriver(config)
    if config.driver == "rigctld":
        from .rigctld_client import RigctldPttDriver

        return RigctldPttDriver(config)
    raise ValueError(f"unknown ptt_control.driver: {config.driver!r}")
