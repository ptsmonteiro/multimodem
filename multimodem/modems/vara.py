"""Backend for the VARA modem command/data API (VARA HF/FM).

Same two-socket shape as ARDOP: a line-based command port and a raw
data port that carries session payload once connected. See the note
in modem_ardop.py -- exact VARA command wording (MYCALL, CONNECT,
LISTEN, CONNECTED/DISCONNECTED) is a best-effort reconstruction from
public VARA documentation and should be checked against the specific
VARA version in use; the event-translation strategy is what's stable.
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


class VaraModemBackend(ModemBackend):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._cmd_writer: asyncio.StreamWriter | None = None
        self._data_writer: asyncio.StreamWriter | None = None
        self._mycalls: list[str] = []
        self._active_call: str | None = None
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

                log.info("connected to VARA modem %s", self.name)
                self._active_call = None  # force a fresh MYCALL on reconnect
                await self._apply_active_call()

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

        if line.startswith("BUSY ON"):
            self.channel.mark_busy(self.name)
        elif line.startswith("BUSY OFF"):
            self.channel.mark_idle(self.name)

        elif keyword == "CONNECTED" and len(parts) >= 3:
            # VARA: "CONNECTED <remotecall> <mycall>"
            self._begin_session(remote=parts[1].upper(), local=parts[2].upper())

        elif keyword == "DISCONNECTED":
            self._end_session()

    def _begin_session(self, remote: str, local: str) -> None:
        try:
            self.channel.acquire_connection(self.name)
        except ChannelBusyError as exc:
            log.warning("%s: %s", self.name, exc)
            return
        self._session_local = local
        self._session_remote = remote
        if self.on_frame:
            self.on_frame(
                AgwFrame(port=0, data_kind=KIND_CONNECT, call_from=remote, call_to=local)
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
        # Now that VARA is idle, apply any MYCALL change that came in
        # while a session was up (we never swap MYCALL mid-session).
        if self._active_call not in self._mycalls:
            asyncio.create_task(self._apply_active_call())

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

    async def _apply_active_call(self) -> None:
        """Pick and send the MYCALL that should own VARA's listen slot.

        Sticky: if the currently active call is still registered, keep
        it -- we never re-send MYCALL/LISTEN just because a second
        client registered, since that would be a no-op churn on the
        wire and, worse, would be indistinguishable from "switch now"
        if it ever raced with a real handover. Only picks a new call
        when the active one is gone (its client disconnected or
        unregistered) or there wasn't one yet. Never called while a
        session is up -- see _end_session, which is what re-triggers
        this once VARA goes idle again.
        """
        if self._active_call in self._mycalls:
            return  # still owns the slot, nothing to do
        new_call = self._mycalls[0] if self._mycalls else None
        if new_call == self._active_call:
            return
        self._active_call = new_call
        if self._cmd_writer and new_call:
            log.info("%s: MYCALL -> %s", self.name, new_call)
            self._cmd_writer.write(f"MYCALL {new_call}\r".encode("ascii"))
            self._cmd_writer.write(b"LISTEN ON\r")
            await self._cmd_writer.drain()
        elif self._cmd_writer:
            self._cmd_writer.write(b"LISTEN OFF\r")
            await self._cmd_writer.drain()

    async def update_registered_calls(self, calls: list[str]) -> None:
        # VARA only accepts a single MYCALL at a time (unlike ARDOP's
        # comma-separated list). Ownership of that slot is sticky: the
        # first client to register keeps it even if others register
        # later, and it's only handed to the next candidate once the
        # owner disconnects/unregisters -- and even then, not while a
        # session is actively connected (see _end_session).
        self._mycalls = calls
        others = [c for c in calls if c != self._active_call]
        if self._active_call in calls and others:
            log.warning(
                "%s: VARA supports one MYCALL at a time; %s holds the slot, "
                "%s unreachable via VARA until it frees up",
                self.name, self._active_call, others,
            )
        if self._session_local is not None:
            return  # defer to _end_session, don't swap mid-session
        await self._apply_active_call()

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
            cmd = f"CONNECT {frame.call_from} {frame.call_to}\r"
            self._cmd_writer.write(cmd.encode("ascii"))
            await self._cmd_writer.drain()
        elif frame.data_kind == KIND_DISCONNECT:
            self._cmd_writer.write(b"DISCONNECT\r")
            await self._cmd_writer.drain()
            self._end_session()
