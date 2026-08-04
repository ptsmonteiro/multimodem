"""RigctldPttDriver against a real RigctldServer over an actual socket.

RigctldPttDriver speaks the same rigctld line protocol our own
RigctldServer implements (rigctld_server.py) -- so the most direct
proof it works is pointing one at the other: a real target arbiter's
state should change exactly as if a rigctld client like Winlink had
sent the same commands.

RigctldServer is asyncio-based; RigctldPttDriver is a blocking socket
client (deliberately -- see the module docstring in ptt/__init__.py).
Following the pattern in test_agwpe_pe_interop.py, the server runs on
its own event loop in a background thread so the test's main thread can
drive the blocking client directly.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from multimodem.channel import ChannelArbiter, ChannelState
from multimodem.config import PttControlConfig
from multimodem.ptt import PttError
from multimodem.ptt.rigctld_client import RigctldPttDriver
from multimodem.rigctld_server import PTT_OWNER, RigctldServer


class _ServerThread(threading.Thread):
    def __init__(self, channel: ChannelArbiter) -> None:
        super().__init__(daemon=True)
        self.channel = channel
        self.port: int | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.server: RigctldServer | None = None
        self._ready = threading.Event()

    def run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._start())
        self._ready.set()
        self.loop.run_forever()

    async def _start(self) -> None:
        self.server = RigctldServer("127.0.0.1", 0, self.channel)
        await self.server.start()
        self.port = self.server._server.sockets[0].getsockname()[1]

    def wait_ready(self) -> None:
        assert self._ready.wait(timeout=5)

    def stop(self) -> None:
        async def _stop():
            await self.server.stop()

        asyncio.run_coroutine_threadsafe(_stop(), self.loop).result(timeout=5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.join(timeout=5)


@pytest.fixture
def server():
    channel = ChannelArbiter()
    thread = _ServerThread(channel)
    thread.start()
    thread.wait_ready()
    yield thread
    thread.stop()


def test_set_ptt_on_keys_the_target_arbiter(server):
    driver = RigctldPttDriver(PttControlConfig(driver="rigctld", host="127.0.0.1", port=server.port))
    driver.start()

    driver.set_ptt(True)

    assert server.channel.state == ChannelState.TRANSMITTING
    assert server.channel.owner == PTT_OWNER
    driver.stop()


def test_set_ptt_off_releases_the_target_arbiter(server):
    driver = RigctldPttDriver(PttControlConfig(driver="rigctld", host="127.0.0.1", port=server.port))
    driver.start()
    driver.set_ptt(True)

    driver.set_ptt(False)

    assert server.channel.is_available()
    driver.stop()


def test_set_ptt_on_refused_raises_ptt_error(server):
    server.channel.acquire_connection("someone-else")
    driver = RigctldPttDriver(PttControlConfig(driver="rigctld", host="127.0.0.1", port=server.port))
    driver.start()

    with pytest.raises(PttError):
        driver.set_ptt(True)

    driver.stop()


def test_start_raises_when_nothing_listening():
    driver = RigctldPttDriver(PttControlConfig(driver="rigctld", host="127.0.0.1", port=1))
    with pytest.raises(PttError):
        driver.start()


def test_set_ptt_before_start_raises():
    driver = RigctldPttDriver(PttControlConfig(driver="rigctld", host="127.0.0.1", port=4532))
    with pytest.raises(PttError):
        driver.set_ptt(True)
