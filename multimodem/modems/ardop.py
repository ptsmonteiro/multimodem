"""Backend for the ARDOP TNC command/data API (piardopc / ardopcf style).

ARDOP exposes two persistent TCP sockets:
  - a command port: line-based text commands out, unsolicited status
    lines in (e.g. "PTT TRUE", "BUSY FALSE", "NEWSTATE CONNECTED").
  - a data port: once a connection is up, raw payload bytes for that
    link flow both directions over this socket.

NOTE: exact command/response syntax below (MYCALL, LISTEN, ARDOPCALL,
NEWSTATE wording) is a best-effort reconstruction from public ARDOP TNC
documentation and may need adjusting against the specific ARDOP build
in use (piardopc vs ardopcf vary slightly). The framing strategy --
translate connect/data/disconnect events to AgwFrame and hand them to
on_frame -- is what matters for multiplexer integration and is stable
regardless of exact wording.
"""
from __future__ import annotations

import asyncio
import logging

from ..agwpe_protocol import AgwFrame
from ..channel import ChannelBusyError
from .base import ModemBackend

log = logging.getLogger(__name__)

KIND_CONNECT = b"C"
KIND_DISCONNECT = b"d"
KIND_DATA = b"D"


class ArdopModemBackend(ModemBackend):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._cmd_writer: asyncio.StreamWriter | None = None
        self._data_writer: asyncio.StreamWriter | None = None
        self._mycalls: list[str] = []
        # Callsigns of the currently active (or just-torn-down) session.
        self._session_local: str | None = None
        self._session_remote: str | None = None

    async def _run(self) -> None:
        while True:
            try:
                cmd_reader, cmd_writer = await asyncio.open_connection(
                    self.config.host, self.config.port
                )
                self._cmd_writer = cmd_writer

                data_reader = data_writer = None
                if self.config.control_port:
                    data_reader, data_writer = await asyncio.open_connection(
                        self.config.host, self.config.control_port
                    )
                    self._data_writer = data_writer

                log.info("connected to ARDOP modem %s", self.name)
                await self._send_mycall()

                tasks = [asyncio.create_task(self._pump_commands(cmd_reader))]
                if data_reader is not None:
                    tasks.append(asyncio.create_task(self._pump_data(data_reader)))
                await asyncio.gather(*tasks)
            except (ConnectionError, OSError) as exc:
                log.warning("modem %s connection error: %s", self.name, exc)
            finally:
                self._cmd_writer = None
                self._data_writer = None
                self._end_session()
                self.channel.mark_idle(self.name)
                self.channel.release_connection(self.name)
            await asyncio.sleep(5)

    # -- command channel -----------------------------------------------

    async def _pump_commands(self, reader: asyncio.StreamReader) -> None:
        while True:
            line = await reader.readline()
            if not line:
                break
            self._handle_command_line(line.decode("ascii", "ignore").strip())

    def _handle_command_line(self, line: str) -> None:
        if not line:
            return
        log.debug("%s <- %s", self.name, line)
        parts = line.split()
        keyword = parts[0] if parts else ""

        if line.startswith("BUSY TRUE"):
            self.channel.mark_busy(self.name)
        elif line.startswith("BUSY FALSE"):
            self.channel.mark_idle(self.name)

        elif keyword == "NEWSTATE" and len(parts) >= 2:
            state = parts[1]
            if state == "CONNECTED":
                # Best-effort: some ARDOP builds emit the peer callsign
                # as a following TARGET/CONNECTED line rather than on
                # NEWSTATE itself; _session_remote may already be set
                # by such a line seen just before this one.
                self._begin_session(remote=self._session_remote)
            elif state == "DISCONNECTED":
                self._end_session()

        elif keyword in ("TARGET", "CONNECTED") and len(parts) >= 2:
            # Peer callsign announced separately from NEWSTATE.
            self._session_remote = parts[1].upper()
            if keyword == "CONNECTED" and self._session_local is None:
                self._begin_session(remote=self._session_remote)

    def _begin_session(self, remote: str | None) -> None:
        try:
            self.channel.acquire_connection(self.name)
        except ChannelBusyError as exc:
            log.warning("%s: %s", self.name, exc)
            return
        local = self._mycalls[0] if self._mycalls else ""
        self._session_local = local
        self._session_remote = remote or self._session_remote or ""
        if self.on_frame:
            self.on_frame(
                AgwFrame(
                    port=0,
                    data_kind=KIND_CONNECT,
                    call_from=self._session_remote,
                    call_to=self._session_local,
                )
            )

    def _end_session(self) -> None:
        had_session = self._session_local is not None
        self.channel.release_connection(self.name)
        if had_session and self.on_frame:
            self.on_frame(
                AgwFrame(
                    port=0,
                    data_kind=KIND_DISCONNECT,
                    call_from=self._session_remote or "",
                    call_to=self._session_local or "",
                )
            )
        self._session_local = None
        self._session_remote = None

    # -- data channel -----------------------------------------------

    async def _pump_data(self, reader: asyncio.StreamReader) -> None:
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                break
            if self.on_frame and self._session_local is not None:
                self.on_frame(
                    AgwFrame(
                        port=0,
                        data_kind=KIND_DATA,
                        call_from=self._session_remote or "",
                        call_to=self._session_local or "",
                        data=chunk,
                    )
                )

    # -- outbound ---------------------------------------------------

    async def _send_mycall(self) -> None:
        if self._cmd_writer and self._mycalls:
            cmd = f"MYCALL {','.join(self._mycalls)}\r"
            self._cmd_writer.write(cmd.encode("ascii"))
            self._cmd_writer.write(b"LISTEN TRUE\r")
            await self._cmd_writer.drain()

    async def update_registered_calls(self, calls: list[str]) -> None:
        self._mycalls = calls
        await self._send_mycall()

    async def send_data(self, call_from: str, call_to: str, data: bytes) -> None:
        if not self._data_writer:
            raise ConnectionError(f"modem {self.name} data port not connected")
        if not self.channel.is_available() and self.channel.owner != self.name:
            raise ChannelBusyError("RF channel busy, cannot transmit")
        self._data_writer.write(data)
        await self._data_writer.drain()

    async def send_control(self, frame: AgwFrame) -> None:
        if not self._cmd_writer:
            raise ConnectionError(f"modem {self.name} not connected")
        if frame.data_kind == KIND_CONNECT:
            try:
                self.channel.acquire_connection(self.name)
            except ChannelBusyError as exc:
                log.warning("%s: %s", self.name, exc)
                return
            self._session_local = frame.call_from
            self._session_remote = frame.call_to
            cmd = f"ARDOPCALL {frame.call_to}\r"
            self._cmd_writer.write(cmd.encode("ascii"))
            await self._cmd_writer.drain()
        elif frame.data_kind == KIND_DISCONNECT:
            self._cmd_writer.write(b"DISCONNECT\r")
            await self._cmd_writer.drain()
            self._end_session()
