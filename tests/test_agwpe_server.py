from multimodem.agwpe_protocol import AgwFrame, HEADER_SIZE
from multimodem.agwpe_server import AgwpeClientServer, KIND_PORT_INFO


class _FakeModem:
    def __init__(self, name: str) -> None:
        self.name = name
        self.on_frame = None


def test_port_info_frame_lists_configured_ports():
    modems = {0: _FakeModem("direwolf-vhf"), 1: _FakeModem("ardop-hf")}
    server = AgwpeClientServer("127.0.0.1", 8000, modems)

    frame = server._port_info_frame()

    assert frame.data_kind == KIND_PORT_INFO
    assert frame.data == b"2;Port1 direwolf-vhf;Port2 ardop-hf;\x00"


def test_port_info_frame_roundtrips_through_encode_decode():
    modems = {0: _FakeModem("only-port")}
    server = AgwpeClientServer("127.0.0.1", 8000, modems)

    encoded = server._port_info_frame().encode()
    decoded, data_len = AgwFrame.decode_header(encoded[:HEADER_SIZE])
    payload = encoded[HEADER_SIZE : HEADER_SIZE + data_len]

    assert decoded.data_kind == KIND_PORT_INFO
    assert payload == b"1;Port1 only-port;\x00"
