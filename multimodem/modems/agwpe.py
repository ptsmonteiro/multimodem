"""Backend for AGWPE-speaking modems (e.g. direwolf)."""
from __future__ import annotations

import asyncio
import logging

from ..agwpe_protocol import AgwFrame, read_frame
from ..channel import ChannelBusyError
from .base import ModemBackend

log = logging.getLogger(__name__)

# AGWPE data_kind bytes we care about at the multiplexer level.
KIND_CONNECT = b"C"
KIND_DISCONNECT = b"d"
KIND_DATA = b"D"
KIND_UNPROTO = b"M"
KIND_MONITOR = b"K"


class AgwpeModemBackend(ModemBackend):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._writer: asyncio.StreamWriter | None = None

    async def _run(self) -> None:
        while True:
            try:
                reader, writer = await asyncio.open_connection(
                    self.config.host, self.config.port
                )
                self._writer = writer
                log.info("connected to AGWPE modem %s", self.name)
                await self._pump(reader)
            except (ConnectionError, OSError) as exc:
                log.warning("modem %s connection error: %s", self.name, exc)
            finally:
                self._writer = None
                self.channel.mark_idle(self.name)
                self.channel.release_connection(self.name)
            await asyncio.sleep(5)  # reconnect backoff

    async def _pump(self, reader: asyncio.StreamReader) -> None:
        while True:
            frame = await read_frame(reader)
            if frame is None:
                break
            await self._handle_frame(frame)

    async def _handle_frame(self, frame: AgwFrame) -> None:
        if frame.port != self.config.agwpe_port:
            # Another channel on the same backend engine (e.g. a second
            # direwolf radio reachable on the same TCP connection but a
            # different AGWPE port) -- not ours, ignore it.
            return

        if frame.data_kind == KIND_CONNECT:
            try:
                self.channel.acquire_connection(self.name)
            except ChannelBusyError as exc:
                log.warning("%s: %s", self.name, exc)
                return
        elif frame.data_kind == KIND_DISCONNECT:
            self.channel.release_connection(self.name)

        if self.on_frame:
            self.on_frame(frame)

    async def send_data(self, call_from: str, call_to: str, data: bytes) -> None:
        if not self._writer:
            raise ConnectionError(f"modem {self.name} not connected")
        if not self.channel.is_available() and self.channel.owner != self.name:
            raise ChannelBusyError("RF channel busy, cannot transmit")
        frame = AgwFrame(
            port=self.config.agwpe_port,
            data_kind=KIND_DATA,
            call_from=call_from,
            call_to=call_to,
            data=data,
        )
        self._writer.write(frame.encode())
        await self._writer.drain()

    async def send_control(self, frame: AgwFrame) -> None:
        if not self._writer:
            raise ConnectionError(f"modem {self.name} not connected")
        if frame.data_kind == KIND_CONNECT:
            try:
                self.channel.acquire_connection(self.name)
            except ChannelBusyError as exc:
                log.warning("%s: %s", self.name, exc)
                return
        elif frame.data_kind == KIND_DISCONNECT:
            self.channel.release_connection(self.name)
        outbound = AgwFrame(
            port=self.config.agwpe_port,
            data_kind=frame.data_kind,
            call_from=frame.call_from,
            call_to=frame.call_to,
            data=frame.data,
        )
        self._writer.write(outbound.encode())
        await self._writer.drain()
