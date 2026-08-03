"""Backend for VARA's KISS interface (unproto/UI traffic, e.g. APRS).

VARA's own command/data API (modem_vara.py) is ARQ/connected-mode only
and has no concept of UI frames. For APRS-style unproto traffic, VARA
instead exposes a standard KISS TCP port carrying raw AX.25 frames --
this backend talks that instead.

Both backends can point at the *same* VARA instance (it decodes both
concurrently off the same audio), and both should use the same
`name` in config so they share one ChannelArbiter owner: the RF
channel is still single, so a KISS transmission and an ARQ session
still can't happen at once, and vice versa.
"""
from __future__ import annotations

import asyncio
import logging

from .. import ax25
from ..agwpe_protocol import AgwFrame
from ..kiss_protocol import KissDecoder, encode_kiss_frame
from .base import ModemBackend

log = logging.getLogger(__name__)

KIND_UNPROTO = b"M"


class VaraKissBackend(ModemBackend):
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
                log.info("connected to VARA KISS port %s", self.name)
                await self._pump(reader)
            except (ConnectionError, OSError) as exc:
                log.warning("modem %s KISS connection error: %s", self.name, exc)
            finally:
                self._writer = None
            await asyncio.sleep(5)

    async def _pump(self, reader: asyncio.StreamReader) -> None:
        decoder = KissDecoder()
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                break
            for ax25_frame in decoder.feed(chunk):
                self._handle_ax25_frame(ax25_frame)

    def _handle_ax25_frame(self, raw: bytes) -> None:
        decoded = ax25.decode_ui_frame(raw)
        if decoded is None:
            return  # not a UI frame (e.g. a connected-mode I/S frame); ignore
        dest, source, _digipeaters, payload = decoded
        if self.on_frame:
            self.on_frame(
                AgwFrame(
                    port=0,
                    data_kind=KIND_UNPROTO,
                    call_from=source,
                    call_to=dest,
                    data=payload,
                )
            )

    async def send_data(self, call_from: str, call_to: str, data: bytes) -> None:
        if not self._writer:
            raise ConnectionError(f"modem {self.name} KISS port not connected")
        if not self.channel.is_available():
            # Unproto traffic never "owns" the channel (see channel.py) --
            # it's fire-and-forget, so if anything else (a connected
            # session or another TX) is in progress right now, drop
            # rather than collide on air.
            log.warning("%s: RF channel busy, dropping unproto frame", self.name)
            return
        ax25_frame = ax25.encode_ui_frame(call_to, call_from, data)
        self._writer.write(encode_kiss_frame(ax25_frame))
        await self._writer.drain()
