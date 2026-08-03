from multimodem.agwpe_protocol import AgwFrame, HEADER_SIZE


def test_encode_decode_roundtrip():
    frame = AgwFrame(
        port=2, data_kind=b"D", call_from="N0CALL-1", call_to="N0CALL-2", data=b"hello"
    )
    encoded = frame.encode()

    decoded, data_len = AgwFrame.decode_header(encoded[:HEADER_SIZE])
    payload = encoded[HEADER_SIZE : HEADER_SIZE + data_len]

    assert decoded.port == 2
    assert decoded.data_kind == b"D"
    assert decoded.call_from == "N0CALL-1"
    assert decoded.call_to == "N0CALL-2"
    assert payload == b"hello"


def test_call_signs_are_null_padded_and_stripped():
    frame = AgwFrame(port=0, data_kind=b"X", call_from="AB1CDE")
    encoded = frame.encode()
    decoded, _ = AgwFrame.decode_header(encoded[:HEADER_SIZE])
    assert decoded.call_from == "AB1CDE"
    assert decoded.call_to == ""
