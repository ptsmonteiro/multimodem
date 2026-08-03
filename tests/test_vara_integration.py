"""Socket-level tests: VaraModemBackend against a fake VARA TCP double.

test_vara_backend.py exercises the state machine (_handle_command_line,
_apply_active_call, send_data/send_control) directly, with no sockets
involved. This file instead runs the backend for real -- asyncio.
open_connection to two real listening ports, exactly as it would
against actual VARA -- so it's the two-socket connect sequence, the
concurrent command/data pump tasks, and clean shutdown that get
exercised here, which the unit tests can't reach.

This is *not* proof the wording matches real VARA (nothing here is
independently authored the way pyham_pe is for AGWPE -- both ends of
this conversation are ours). It only proves the backend's networking
code does what modems/vara.py claims against a server that speaks the
documented lines back. Validating the wording itself needs a real VARA
instance -- see the caveat in modems/vara.py.
"""
from __future__ import annotations

import asyncio

import pytest

from multimodem.channel import ChannelArbiter, ChannelState
from multimodem.config import ModemConfig
from multimodem.modems.vara import VaraModemBackend


class FakeVaraServer:
    """Listens on two ports, like real VARA's command and data sockets."""

    def __init__(self) -> None:
        self.cmd_port: int | None = None
        self.data_port: int | None = None
        self._cmd_server: asyncio.AbstractServer | None = None
        self._data_server: asyncio.AbstractServer | None = None
        self._cmd_reader: asyncio.StreamReader | None = None
        self._cmd_writer: asyncio.StreamWriter | None = None
        self._data_reader: asyncio.StreamReader | None = None
        self._data_writer: asyncio.StreamWriter | None = None
        self._cmd_connected = asyncio.Event()
        self._data_connected = asyncio.Event()

    async def start(self) -> None:
        self._cmd_server = await asyncio.start_server(self._on_cmd, "127.0.0.1", 0)
        self._data_server = await asyncio.start_server(self._on_data, "127.0.0.1", 0)
        self.cmd_port = self._cmd_server.sockets[0].getsockname()[1]
        self.data_port = self._data_server.sockets[0].getsockname()[1]

    async def _on_cmd(self, reader, writer):
        self._cmd_reader, self._cmd_writer = reader, writer
        self._cmd_connected.set()

    async def _on_data(self, reader, writer):
        self._data_reader, self._data_writer = reader, writer
        self._data_connected.set()

    async def wait_connected(self, timeout=5) -> None:
        await asyncio.wait_for(self._cmd_connected.wait(), timeout)
        await asyncio.wait_for(self._data_connected.wait(), timeout)

    async def recv_cmd_line(self, timeout=5) -> str:
        # The backend terminates commands with a bare '\r' (no '\n'), so
        # StreamReader.readline() -- which only splits on '\n' -- would
        # hang forever; read up to the '\r' instead.
        line = await asyncio.wait_for(self._cmd_reader.readuntil(b"\r"), timeout)
        return line.decode("ascii").strip()

    def send_cmd_line(self, line: str) -> None:
        # Bare '\r', no '\n' -- matches real VARA/mercury wire format.
        # Sending '\r\n' here would mask a backend bug where it reads
        # lines with readline() (splits only on '\n') instead of
        # tracking the '\r' terminator the modem actually uses.
        self._cmd_writer.write((line + "\r").encode("ascii"))

    async def drain_cmd(self) -> None:
        await self._cmd_writer.drain()

    async def recv_data(self, nbytes: int, timeout=5) -> bytes:
        return await asyncio.wait_for(self._data_reader.readexactly(nbytes), timeout)

    def send_data(self, data: bytes) -> None:
        self._data_writer.write(data)

    async def drain_data(self) -> None:
        await self._data_writer.drain()

    async def stop(self) -> None:
        for writer in (self._cmd_writer, self._data_writer):
            if writer is not None:
                writer.close()
        for server in (self._cmd_server, self._data_server):
            server.close()
            await server.wait_closed()


async def _run_scenario(coro_fn) -> None:
    """Start a fake server + backend, run the scenario, then tear both down."""
    fake = FakeVaraServer()
    await fake.start()
    config = ModemConfig(
        name="vara", type="vara", host="127.0.0.1", port=fake.cmd_port, control_port=fake.data_port
    )
    backend = VaraModemBackend(config, ChannelArbiter())
    received = []
    backend.on_frame = received.append
    backend.start()
    try:
        await fake.wait_connected()
        await coro_fn(fake, backend, received)
    finally:
        await backend.stop()
        await fake.stop()


def test_connect_sends_mycall_and_listen_on_once_registered():
    async def scenario(fake, backend, _received):
        await backend.update_registered_calls(["N0CALL-1"])
        assert await fake.recv_cmd_line() == "MYCALL N0CALL-1"
        assert await fake.recv_cmd_line() == "LISTEN ON"

    asyncio.run(_run_scenario(scenario))


def test_inbound_connected_line_produces_connect_frame():
    async def scenario(fake, backend, received):
        fake.send_cmd_line("CONNECTED REMOTE-1 N0CALL-1")
        await fake.drain_cmd()

        for _ in range(50):
            if received:
                break
            await asyncio.sleep(0.05)
        assert len(received) == 1
        frame = received[0]
        assert (frame.data_kind, frame.call_from, frame.call_to) == (b"C", "REMOTE-1", "N0CALL-1")
        assert backend.channel.state == ChannelState.CONNECTED

    asyncio.run(_run_scenario(scenario))


def test_data_flows_both_directions_once_connected():
    async def scenario(fake, backend, received):
        fake.send_cmd_line("CONNECTED REMOTE-1 N0CALL-1")
        await fake.drain_cmd()
        for _ in range(50):
            if received:
                break
            await asyncio.sleep(0.05)
        received.clear()

        # Backend -> fake VARA (outbound, e.g. an AGWPE client's data frame).
        await backend.send_data("N0CALL-1", "REMOTE-1", b"outbound")
        assert await fake.recv_data(len(b"outbound")) == b"outbound"

        # Fake VARA -> backend (inbound, off the air).
        fake.send_data(b"inbound")
        await fake.drain_data()
        for _ in range(50):
            if received:
                break
            await asyncio.sleep(0.05)
        assert len(received) == 1
        frame = received[0]
        assert (frame.data_kind, frame.call_from, frame.call_to, frame.data) == (
            b"D", "REMOTE-1", "N0CALL-1", b"inbound",
        )

    asyncio.run(_run_scenario(scenario))


def test_disconnected_line_releases_channel():
    async def scenario(fake, backend, received):
        fake.send_cmd_line("CONNECTED REMOTE-1 N0CALL-1")
        await fake.drain_cmd()
        for _ in range(50):
            if received:
                break
            await asyncio.sleep(0.05)
        received.clear()

        fake.send_cmd_line("DISCONNECTED")
        await fake.drain_cmd()
        for _ in range(50):
            if received:
                break
            await asyncio.sleep(0.05)
        assert len(received) == 1
        assert received[0].data_kind == b"d"
        assert backend.channel.is_available()

    asyncio.run(_run_scenario(scenario))


def test_ack_lines_interleaved_with_commands_are_ignored():
    # Real VARA-compatible TNCs (confirmed against mercury's
    # execute_control_command) reply "OK\r"/"WRONG\r" to every command
    # line, and send unsolicited "IAMALIVE\r" keepalives too. The
    # backend doesn't consume these acks, so they arrive interleaved
    # with the lines it does care about -- this confirms that doesn't
    # derail session tracking.
    async def scenario(fake, backend, received):
        await backend.update_registered_calls(["N0CALL-1"])
        assert await fake.recv_cmd_line() == "MYCALL N0CALL-1"
        assert await fake.recv_cmd_line() == "LISTEN ON"

        fake.send_cmd_line("OK")
        fake.send_cmd_line("OK")
        fake.send_cmd_line("IAMALIVE")
        fake.send_cmd_line("CONNECTED REMOTE-1 N0CALL-1 2750")
        await fake.drain_cmd()

        for _ in range(50):
            if received:
                break
            await asyncio.sleep(0.05)
        assert len(received) == 1
        frame = received[0]
        assert (frame.data_kind, frame.call_from, frame.call_to) == (b"C", "REMOTE-1", "N0CALL-1")
        assert backend.channel.state == ChannelState.CONNECTED

    asyncio.run(_run_scenario(scenario))


def test_client_initiated_connect_sends_connect_command():
    async def scenario(fake, backend, _received):
        from multimodem.agwpe_protocol import AgwFrame

        await backend.send_control(
            AgwFrame(port=0, data_kind=b"C", call_from="N0CALL-1", call_to="REMOTE-1")
        )
        assert await fake.recv_cmd_line() == "CONNECT N0CALL-1 REMOTE-1"
        assert backend.channel.state == ChannelState.CONNECTED

    asyncio.run(_run_scenario(scenario))
