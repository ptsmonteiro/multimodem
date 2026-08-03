"""Common interface for backend modem connections (direwolf, ardop, vara...)."""
from __future__ import annotations

import abc
import asyncio
import logging
from typing import Callable

from ..agwpe_protocol import AgwFrame
from ..channel import ChannelArbiter
from ..config import ModemConfig

log = logging.getLogger(__name__)

# Called as on_frame(frame) whenever the backend has an inbound frame
# (connect request, data, disconnect) to deliver to whichever client
# app registered its destination callsign, on this modem's
# client-facing AGWPE port.
OnFrameCallback = Callable[[AgwFrame], None]


class ModemBackend(abc.ABC):
    """One connection to a physical/software modem behind the multiplexer.

    Subclasses translate between the modem's native wire protocol and the
    multiplexer's internal event stream, and must respect the shared
    ChannelArbiter before keying PTT or opening a connection.
    """

    def __init__(self, config: ModemConfig, channel: ChannelArbiter) -> None:
        self.config = config
        self.channel = channel
        self._task: asyncio.Task | None = None
        self.on_frame: OnFrameCallback | None = None

    @property
    def name(self) -> str:
        return self.config.name

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=f"modem:{self.name}")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.channel.mark_idle(self.name)
        self.channel.release_connection(self.name)
        self.channel.release_ptt(self.name)

    @abc.abstractmethod
    async def _run(self) -> None:
        """Connect to the backend modem and pump frames until cancelled."""

    @abc.abstractmethod
    async def send_data(self, call_from: str, call_to: str, data: bytes) -> None:
        """Send outbound data destined for this modem, honoring the channel."""

    async def send_control(self, frame: AgwFrame) -> None:
        """Send a client-initiated connect/disconnect request to the modem.

        Default is a no-op; subclasses that support connected mode
        (currently just AGWPE) override this.
        """
        log.warning("%s: send_control not implemented for this backend", self.name)

    async def update_registered_calls(self, calls: list[str]) -> None:
        """Notify the backend which callsigns clients have registered.

        ARDOP/VARA only accept connections for callsign(s) set via their
        own MYCALL command, so the backend needs to know the current set
        whenever a client registers/unregisters. No-op by default (the
        AGWPE backend doesn't need this -- direwolf handles its own
        callsign/SSID matching independently).
        """
