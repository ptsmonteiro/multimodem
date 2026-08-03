"""Minimal rigctld-compatible PTT server.

Exposes a single shared endpoint that modems (or rig-control scripts)
use to request PTT. Since all backend modems share one radio, a PTT
request is only granted when the shared RF channel is idle; if a
connection or transmission is already in progress, the request is
refused so a second modem can't key up on top of it.

Implements just enough of the hamlib rigctld line protocol to be
useful: `T 1` / `T 0` (or `\\set_ptt`) to key/unkey, `t` to query.
"""
from __future__ import annotations

import asyncio
import logging

from .channel import ChannelArbiter

log = logging.getLogger(__name__)

PTT_OWNER = "rigctld"


class RigctldServer:
    def __init__(self, host: str, port: int, channel: ChannelArbiter) -> None:
        self.host = host
        self.port = port
        self.channel = channel
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        log.info("rigctld PTT server listening on %s:%s", self.host, self.port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        self.channel.release_ptt(PTT_OWNER)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                reply = self._handle_command(line.decode("ascii", "ignore").strip())
                writer.write((reply + "\n").encode())
                await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()

    def _handle_command(self, cmd: str) -> str:
        cmd = cmd.strip()
        if cmd in ("t", "\\get_ptt"):
            keyed = self.channel.owner == PTT_OWNER and self.channel.state.value == "transmitting"
            return "1" if keyed else "0"

        if cmd.startswith("T ") or cmd.startswith("\\set_ptt"):
            arg = cmd.split()[-1]
            if arg == "1":
                granted = self.channel.request_ptt(PTT_OWNER)
                if not granted:
                    log.warning("PTT request refused: channel busy (%s)", self.channel.owner)
                    return "RPRT -6"  # RIG_EBUSBUSY-ish: reject, resource busy
                return "RPRT 0"
            else:
                self.channel.release_ptt(PTT_OWNER)
                return "RPRT 0"

        return "RPRT -1"  # unsupported command
