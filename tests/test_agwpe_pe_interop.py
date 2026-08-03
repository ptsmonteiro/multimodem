"""End-to-end interop tests: a real pyham_pe AGWPE client against our
real AgwpeClientServer over an actual TCP socket.

Unlike the other test_agwpe_* files, which unit-test our own frame
encode/decode and internal methods, these tests drive the server the
same way a real client app (Winlink Express, a BBS, pyham_pe-based
tools) would: connect, wait for the R/G handshake, register a
callsign, exchange unproto and connected-mode traffic, and disconnect
-- using an independent, third-party implementation of the AGWPE
protocol (https://pypi.org/project/pyham_pe/) as the client, so a bug
in our framing or handshake would show up as pyham_pe misbehaving, not
just as an assertion against our own code.

The backend modem is a fake (no real direwolf/VARA needed) so these
tests only exercise the client-facing AGWPE server.
"""
from __future__ import annotations

import asyncio
import queue
import threading

import pe
import pytest

from multimodem.agwpe_protocol import AgwFrame
from multimodem.agwpe_server import AgwpeClientServer

KIND_CONNECT = b"C"
KIND_DISCONNECT = b"d"
KIND_DATA = b"D"
KIND_UNPROTO = b"M"


class FakeModemBackend:
    """Records what the server sends it; lets tests inject inbound frames."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.on_frame = None
        self.sent_data: list[tuple[str, str, bytes]] = []
        self.control_frames: list[AgwFrame] = []
        self.registered_calls: list[str] = []

    async def send_data(self, call_from: str, call_to: str, data: bytes) -> None:
        self.sent_data.append((call_from, call_to, data))

    async def send_control(self, frame: AgwFrame) -> None:
        self.control_frames.append(frame)

    async def update_registered_calls(self, calls: list[str]) -> None:
        self.registered_calls = list(calls)


class _ServerThread(threading.Thread):
    """Runs AgwpeClientServer on its own asyncio loop in a background thread.

    pyham_pe's client is a blocking, thread-based socket client, so the
    server needs to live somewhere other than the test's own thread.
    """

    def __init__(self, modems: dict[int, FakeModemBackend]) -> None:
        super().__init__(daemon=True)
        self.modems = modems
        self.host = "127.0.0.1"
        self.port: int | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.server: AgwpeClientServer | None = None
        self._ready = threading.Event()

    def run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._start())
        self._ready.set()
        self.loop.run_forever()

    async def _start(self) -> None:
        self.server = AgwpeClientServer(self.host, 0, self.modems)
        await self.server.start()
        self.port = self.server._server.sockets[0].getsockname()[1]

    def wait_ready(self) -> None:
        self._ready.wait(timeout=5)

    def inject(self, port: int, frame: AgwFrame) -> None:
        """Simulate a frame arriving from a backend modem (radio traffic)."""
        modem = self.modems[port]
        self.loop.call_soon_threadsafe(modem.on_frame, frame)

    def stop(self) -> None:
        async def _stop():
            await self.server.stop()

        asyncio.run_coroutine_threadsafe(_stop(), self.loop).result(timeout=5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.join(timeout=5)


class QueueingReceiveHandler(pe.ReceiveHandler):
    """Pushes every callback pyham_pe fires onto a queue tests can poll.

    pyham_pe delivers these from its own receiver thread, so a
    thread-safe queue -- not plain attributes -- is what makes them
    safely observable from the test thread.
    """

    def __init__(self) -> None:
        self.events: queue.Queue = queue.Queue()

    def _push(self, name, *args):
        self.events.put((name, args))

    def version_info(self, major, minor):
        self._push("version_info", major, minor)

    def callsign_registered(self, callsign, success):
        self._push("callsign_registered", callsign, success)

    def port_info(self, info):
        self._push("port_info", info)

    def connection_received(self, port, call_from, call_to, incoming, message):
        self._push("connection_received", port, call_from, call_to, incoming, message)

    def connected_data(self, port, call_from, call_to, pid, data):
        self._push("connected_data", port, call_from, call_to, pid, data)

    def disconnected(self, port, call_from, call_to, message):
        self._push("disconnected", port, call_from, call_to, message)

    def wait_for(self, name, timeout=5):
        while True:
            event = self.events.get(timeout=timeout)
            if event[0] == name:
                return event[1]


@pytest.fixture
def server():
    modems = {0: FakeModemBackend("test-modem")}
    thread = _ServerThread(modems)
    thread.start()
    thread.wait_ready()
    yield thread, modems
    thread.stop()


@pytest.fixture
def client(server):
    thread, _modems = server
    handler = QueueingReceiveHandler()
    engine = pe.PacketEngine(handler)
    engine.connect_to_server(thread.host, thread.port)
    deadline = pe.tocsin.signal(pe.SIG_ENGINE_READY)
    ready = threading.Event()
    deadline.listen(lambda *_: ready.set())
    assert ready.wait(timeout=5), "pyham_pe client never became ready"
    yield engine, handler
    if engine.connected_to_server:
        engine.disconnect_from_server()


def test_handshake_reports_port_info(client, server):
    engine, _handler = client
    assert engine.version_info == (2, 1)
    assert engine.get_cached_port_info() == ["Port1 test-modem"]


def test_register_callsign_is_confirmed(client):
    engine, handler = client
    engine.register_callsign("N0CALL-1")

    callsign, success = handler.wait_for("callsign_registered")
    assert callsign == "N0CALL-1"
    assert success is True
    assert engine.is_callsign_registered("N0CALL-1")


def test_register_callsign_reaches_backend_modem(client, server):
    engine, handler = client
    _thread, modems = server

    engine.register_callsign("N0CALL-1")
    handler.wait_for("callsign_registered")

    assert modems[0].registered_calls == ["N0CALL-1"]


def test_send_unproto_reaches_backend_modem(client, server):
    engine, _handler = client
    _thread, modems = server
    engine.register_callsign("N0CALL-1")

    engine.send_unproto(0, "N0CALL-1", "APRS", b"hello world")

    for _ in range(50):
        if modems[0].sent_data:
            break
        threading.Event().wait(0.05)
    assert modems[0].sent_data == [("N0CALL-1", "APRS", b"hello world")]


def test_inbound_connect_and_data_round_trip(client, server):
    engine, handler = client
    thread, modems = server
    engine.register_callsign("N0CALL-1")
    handler.wait_for("callsign_registered")

    # Simulate the backend modem reporting an inbound connect from the
    # radio (e.g. VARA's "CONNECTED REMOTE N0CALL-1" or an AGWPE 'C').
    thread.inject(
        0, AgwFrame(port=0, data_kind=KIND_CONNECT, call_from="REMOTE-1", call_to="N0CALL-1")
    )
    port, call_from, call_to, _incoming, _msg = handler.wait_for("connection_received")
    assert (call_from, call_to) == ("REMOTE-1", "N0CALL-1")

    # Client sends data on the now-open connection.
    engine.send_data(0, "N0CALL-1", "REMOTE-1", b"payload")
    for _ in range(50):
        if modems[0].sent_data:
            break
        threading.Event().wait(0.05)
    assert modems[0].sent_data == [("N0CALL-1", "REMOTE-1", b"payload")]

    # Backend delivers inbound data on the same session.
    thread.inject(
        0,
        AgwFrame(
            port=0, data_kind=KIND_DATA, call_from="REMOTE-1", call_to="N0CALL-1", data=b"reply"
        ),
    )
    _port, call_from, call_to, _pid, data = handler.wait_for("connected_data")
    assert (call_from, call_to, bytes(data)) == ("REMOTE-1", "N0CALL-1", b"reply")

    # Client disconnects; the request must reach the backend modem.
    engine.disconnect(0, "N0CALL-1", "REMOTE-1")
    for _ in range(50):
        if modems[0].control_frames:
            break
        threading.Event().wait(0.05)
    assert modems[0].control_frames[-1].data_kind == KIND_DISCONNECT
