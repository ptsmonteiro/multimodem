import asyncio

from multimodem.agwpe_protocol import HEADER_SIZE, AgwFrame
from multimodem.channel import ChannelArbiter
from multimodem.config import ModemConfig
from multimodem.modems.agwpe import AgwpeModemBackend


def test_default_agwpe_port_is_zero():
    config = ModemConfig(name="direwolf", type="agwpe", host="127.0.0.1", port=8001)
    assert config.agwpe_port == 0


def test_send_data_stamps_configured_agwpe_port():
    config = ModemConfig(
        name="direwolf-uhf", type="agwpe", host="127.0.0.1", port=8001, agwpe_port=1
    )
    backend = AgwpeModemBackend(config, ChannelArbiter())
    backend._writer = _FakeWriter()

    asyncio.run(backend.send_data("N0CALL-1", "N0CALL-2", b"hi"))

    sent = AgwFrame.decode_header(backend._writer.written[0][:HEADER_SIZE])[0]
    assert sent.port == 1


def test_handle_frame_ignores_other_channels():
    config = ModemConfig(
        name="direwolf-uhf", type="agwpe", host="127.0.0.1", port=8001, agwpe_port=1
    )
    backend = AgwpeModemBackend(config, ChannelArbiter())
    received = []
    backend.on_frame = received.append

    asyncio.run(backend._handle_frame(AgwFrame(port=0, data_kind=b"D")))
    assert received == []

    asyncio.run(backend._handle_frame(AgwFrame(port=1, data_kind=b"D")))
    assert len(received) == 1


class _FakeWriter:
    def __init__(self) -> None:
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        pass
