"""Unit tests for VaraModemBackend's command-line/session state machine.

These drive the backend's internal methods directly against fake
writers -- no real sockets -- so they're fast and deterministic, and
pin down the behavior documented in modems/vara.py (sticky MYCALL
ownership, session tracking, channel arbitration) as a regression net.
They do *not* validate that the command wording itself matches real
VARA -- see test_vara_integration.py for the socket-level tests, and
the module docstring in modems/vara.py for the standing caveat that
the wording is a best-effort reconstruction.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from multimodem.agwpe_protocol import AgwFrame
from multimodem.channel import ChannelArbiter, ChannelBusyError, ChannelState
from multimodem.config import ModemConfig
from multimodem.modems.vara import VaraModemBackend


class FakeWriter:
    def __init__(self) -> None:
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        pass

    @property
    def text(self) -> str:
        return b"".join(self.written).decode("ascii")


class FakeReader:
    """Feeds pre-scripted lines/chunks, then EOF, to a pump loop."""

    def __init__(self, lines: list[bytes] | None = None, chunks: list[bytes] | None = None) -> None:
        self._lines = list(lines or [])
        self._chunks = list(chunks or [])

    async def readuntil(self, separator: bytes = b"\n") -> bytes:
        if not self._lines:
            raise asyncio.IncompleteReadError(partial=b"", expected=None)
        return self._lines.pop(0)

    async def read(self, _n: int) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


def make_backend(control_port: int | None = 8301) -> VaraModemBackend:
    config = ModemConfig(
        name="vara", type="vara", host="127.0.0.1", port=8300, control_port=control_port
    )
    return VaraModemBackend(config, ChannelArbiter())


# -- MYCALL ownership --------------------------------------------------


def test_apply_active_call_sends_mycall_and_listen_on():
    backend = make_backend()
    backend._cmd_writer = FakeWriter()
    backend._mycalls = ["N0CALL-1"]

    asyncio.run(backend._apply_active_call())

    assert backend._active_call == "N0CALL-1"
    assert backend._cmd_writer.text == "MYCALL N0CALL-1\rLISTEN ON\r"


def test_apply_active_call_sends_listen_off_once_owning_call_is_gone():
    backend = make_backend()
    backend._cmd_writer = FakeWriter()
    backend._active_call = "N0CALL-1"  # previously owned the listen slot
    backend._mycalls = []  # ...but no client has it registered anymore

    asyncio.run(backend._apply_active_call())

    assert backend._active_call is None
    assert backend._cmd_writer.text == "LISTEN OFF\r"


def test_second_registration_does_not_preempt_active_call(caplog):
    backend = make_backend()
    backend._cmd_writer = FakeWriter()

    asyncio.run(backend.update_registered_calls(["N0CALL-1"]))
    assert backend._active_call == "N0CALL-1"
    first_write_count = len(backend._cmd_writer.written)

    with caplog.at_level(logging.WARNING):
        asyncio.run(backend.update_registered_calls(["N0CALL-1", "N0CALL-2"]))

    assert backend._active_call == "N0CALL-1"  # still sticky
    assert len(backend._cmd_writer.written) == first_write_count  # no new MYCALL sent
    assert "N0CALL-2" in caplog.text


def test_next_call_takes_over_once_owner_unregisters():
    backend = make_backend()
    backend._cmd_writer = FakeWriter()

    asyncio.run(backend.update_registered_calls(["N0CALL-1", "N0CALL-2"]))
    backend._cmd_writer.written.clear()

    asyncio.run(backend.update_registered_calls(["N0CALL-2"]))

    assert backend._active_call == "N0CALL-2"
    assert backend._cmd_writer.text == "MYCALL N0CALL-2\rLISTEN ON\r"


def test_registration_deferred_while_session_active():
    backend = make_backend()
    backend._cmd_writer = FakeWriter()
    asyncio.run(backend.update_registered_calls(["N0CALL-1"]))
    backend._cmd_writer.written.clear()
    backend._session_local = "N0CALL-1"  # session in progress

    asyncio.run(backend.update_registered_calls(["N0CALL-2"]))

    assert backend._active_call == "N0CALL-1"  # unchanged mid-session
    assert backend._cmd_writer.written == []  # nothing sent, deferred to _end_session


# -- inbound command lines ----------------------------------------------


def test_connected_line_begins_session_and_emits_connect_frame():
    backend = make_backend()
    received = []
    backend.on_frame = received.append

    backend._handle_command_line("CONNECTED REMOTE-1 N0CALL-1")

    assert backend._session_remote == "REMOTE-1"
    assert backend._session_local == "N0CALL-1"
    assert backend.channel.state == ChannelState.CONNECTED
    assert backend.channel.owner == "vara"
    assert len(received) == 1
    frame = received[0]
    assert (frame.data_kind, frame.call_from, frame.call_to) == (b"C", "REMOTE-1", "N0CALL-1")


def test_connected_line_ignored_when_channel_held_elsewhere():
    backend = make_backend()
    backend.channel.acquire_connection("ardop")
    received = []
    backend.on_frame = received.append

    backend._handle_command_line("CONNECTED REMOTE-1 N0CALL-1")

    assert backend._session_local is None
    assert received == []


def test_disconnected_line_ends_session_and_emits_disconnect_frame():
    backend = make_backend()
    backend._mycalls = ["N0CALL-1"]
    received = []
    backend.on_frame = received.append
    backend._handle_command_line("CONNECTED REMOTE-1 N0CALL-1")
    received.clear()

    asyncio.run(_handle_line_async(backend, "DISCONNECTED"))

    assert backend._session_local is None
    assert backend.channel.is_available()
    assert len(received) == 1
    frame = received[0]
    assert (frame.data_kind, frame.call_from, frame.call_to) == (b"d", "REMOTE-1", "N0CALL-1")


async def _handle_line_async(backend: VaraModemBackend, line: str) -> None:
    # _end_session() may schedule asyncio.create_task(...), which needs a
    # running loop -- run the whole call inside one.
    backend._handle_command_line(line)
    await asyncio.sleep(0)  # let any scheduled task run


def test_busy_on_off_updates_channel_state():
    backend = make_backend()
    backend._handle_command_line("BUSY ON")
    assert backend.channel.state == ChannelState.BUSY
    backend._handle_command_line("BUSY OFF")
    assert backend.channel.is_available()


def test_ptt_on_off_before_connection_claims_and_releases_channel():
    # Real VARA/mercury key PTT for pre-connection activity (CQ frames,
    # the ARQ handshake on an inbound PENDING call, TUNE) before any
    # CONNECTED line arrives. That window isn't covered by
    # acquire_connection, so PTT ON/OFF must claim/release the channel
    # itself to keep another modem (or rigctld) from keying at the same
    # time.
    backend = make_backend()
    backend._handle_command_line("PTT ON")
    assert backend.channel.state == ChannelState.TRANSMITTING
    assert backend.channel.owner == "vara"

    backend._handle_command_line("PTT OFF")
    assert backend.channel.is_available()


def test_ptt_on_off_during_active_session_does_not_disturb_connected_state():
    # Once CONNECTED, the session already owns the channel via
    # acquire_connection, and ARQ toggles PTT once per frame -- treating
    # each toggle as a fresh PTT request/release would flip CONNECTED to
    # TRANSMITTING and then to IDLE on the very first mid-session PTT
    # OFF, dropping the session's channel reservation out from under it.
    backend = make_backend()
    backend._handle_command_line("CONNECTED REMOTE-1 N0CALL-1")
    assert backend.channel.state == ChannelState.CONNECTED

    backend._handle_command_line("PTT ON")
    assert backend.channel.state == ChannelState.CONNECTED
    assert backend.channel.owner == "vara"

    backend._handle_command_line("PTT OFF")
    assert backend.channel.state == ChannelState.CONNECTED
    assert backend.channel.owner == "vara"


def test_ptt_on_refused_logs_warning(caplog):
    backend = make_backend()
    backend.channel.acquire_connection("ardop")  # another owner holds it

    with caplog.at_level(logging.WARNING):
        backend._handle_command_line("PTT ON")

    assert backend.channel.owner == "ardop"  # unchanged, still refused
    assert "PTT ON refused" in caplog.text


def test_ptt_on_off_during_active_session_still_keys_hardware():
    # Unlike the arbiter state (pinned above), a hardware PTT hook must
    # fire on every mid-session toggle -- that's the actual over-the-air
    # keying for real ARQ traffic, not just pre-connection tones.
    backend = make_backend()
    events = []
    backend.channel.on_ptt = events.append
    backend._handle_command_line("CONNECTED REMOTE-1 N0CALL-1")

    backend._handle_command_line("PTT ON")
    backend._handle_command_line("PTT OFF")

    assert events == [True, False]


def test_connected_line_with_bandwidth_field_still_parses():
    # Real VARA/VARA-compatible TNCs (confirmed against mercury's
    # tnc_send_connected) send a 4th bandwidth field: "CONNECTED <src>
    # <dst> <bw>". Our tests elsewhere use the bare 3-token form; this
    # pins down that the real 4-token wire format also parses.
    backend = make_backend()
    received = []
    backend.on_frame = received.append

    backend._handle_command_line("CONNECTED REMOTE-1 N0CALL-1 2750")

    assert backend._session_remote == "REMOTE-1"
    assert backend._session_local == "N0CALL-1"
    assert len(received) == 1
    frame = received[0]
    assert (frame.data_kind, frame.call_from, frame.call_to) == (b"C", "REMOTE-1", "N0CALL-1")


@pytest.mark.parametrize(
    "line",
    [
        "OK",
        "WRONG",
        "PENDING",
        "CANCELPENDING",
        "IAMALIVE",
        "BUFFER 1234",
        "BITRATE (0) 1500 BPS",
    ],
)
def test_unrecognized_and_ack_lines_are_ignored(line):
    # Real TNCs (mercury included) send command acks ("OK"/"WRONG"),
    # keepalives ("IAMALIVE"), and status lines ("PENDING",
    # "CANCELPENDING", "BUFFER n", "BITRATE ...") that this backend
    # doesn't currently act on. They must be safely ignored rather than
    # raising or mutating session/channel state.
    backend = make_backend()
    received = []
    backend.on_frame = received.append

    backend._handle_command_line(line)

    assert received == []
    assert backend._session_local is None
    assert backend.channel.is_available()


# -- outbound: send_data / send_control ----------------------------------


def test_send_data_without_data_writer_raises():
    backend = make_backend()
    with pytest.raises(ConnectionError):
        asyncio.run(backend.send_data("N0CALL-1", "REMOTE-1", b"hi"))


def test_send_data_writes_raw_bytes_to_data_socket():
    backend = make_backend()
    backend._data_writer = FakeWriter()

    asyncio.run(backend.send_data("N0CALL-1", "REMOTE-1", b"hi"))

    assert backend._data_writer.written == [b"hi"]


def test_send_data_blocked_when_channel_held_by_other_owner():
    backend = make_backend()
    backend._data_writer = FakeWriter()
    backend.channel.acquire_connection("ardop")

    with pytest.raises(ChannelBusyError):
        asyncio.run(backend.send_data("N0CALL-1", "REMOTE-1", b"hi"))


def test_send_control_connect_sends_connect_command():
    backend = make_backend()
    backend._cmd_writer = FakeWriter()
    frame = AgwFrame(port=0, data_kind=b"C", call_from="N0CALL-1", call_to="REMOTE-1")

    asyncio.run(backend.send_control(frame))

    assert backend._cmd_writer.text == "CONNECT N0CALL-1 REMOTE-1\r"
    assert backend._session_local == "N0CALL-1"
    assert backend._session_remote == "REMOTE-1"
    assert backend.channel.owner == "vara"


def test_send_control_connect_refused_when_channel_busy():
    backend = make_backend()
    backend._cmd_writer = FakeWriter()
    backend.channel.acquire_connection("ardop")
    frame = AgwFrame(port=0, data_kind=b"C", call_from="N0CALL-1", call_to="REMOTE-1")

    asyncio.run(backend.send_control(frame))

    assert backend._cmd_writer.written == []  # never sent
    assert backend._session_local is None


def test_send_control_disconnect_sends_disconnect_command_and_ends_session():
    backend = make_backend()
    backend._cmd_writer = FakeWriter()
    backend._mycalls = ["N0CALL-1"]
    backend._session_local = "N0CALL-1"
    backend._session_remote = "REMOTE-1"
    backend.channel.acquire_connection("vara")
    frame = AgwFrame(port=0, data_kind=b"d", call_from="N0CALL-1", call_to="REMOTE-1")

    asyncio.run(backend.send_control(frame))

    assert "DISCONNECT\r" in backend._cmd_writer.text
    assert backend._session_local is None
    assert backend.channel.is_available()


# -- inbound data pump ----------------------------------------------------


def test_pump_data_delivers_frame_only_during_active_session():
    backend = make_backend()
    received = []
    backend.on_frame = received.append

    # No session yet: chunk is dropped.
    asyncio.run(backend._pump_data(FakeReader(chunks=[b"ignored"])))
    assert received == []

    # With a session, the chunk becomes a DATA AgwFrame addressed to the
    # local/remote pair recorded by CONNECTED.
    backend._session_local = "N0CALL-1"
    backend._session_remote = "REMOTE-1"
    asyncio.run(backend._pump_data(FakeReader(chunks=[b"payload"])))

    assert len(received) == 1
    frame = received[0]
    assert (frame.data_kind, frame.call_from, frame.call_to, frame.data) == (
        b"D", "REMOTE-1", "N0CALL-1", b"payload",
    )


def test_pump_commands_dispatches_each_line():
    backend = make_backend()
    seen = []
    backend.on_frame = seen.append

    asyncio.run(
        backend._pump_commands(
            FakeReader(lines=[b"BUSY ON\r\n", b"BUSY OFF\r\n"])
        )
    )

    assert backend.channel.is_available()  # ended on BUSY OFF, net idle
