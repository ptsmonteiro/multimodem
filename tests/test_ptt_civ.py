from __future__ import annotations

from types import SimpleNamespace

import pytest

from multimodem.config import PttControlConfig
from multimodem.ptt import PttError
from multimodem.ptt.civ import CivPttDriver, select_civ_port


class FakeSerial:
    def __init__(self) -> None:
        self.port = None
        self.baudrate = None
        self.timeout = None
        self.rts = True
        self.dtr = True
        self.opened = False
        self.written: list[bytes] = []
        self.closed = False
        self.reply = bytes.fromhex("fefee0a4fbfd")  # default: OK ack

    def open(self) -> None:
        self.opened = True

    def reset_input_buffer(self) -> None:
        pass

    def write(self, data: bytes) -> None:
        self.written.append(bytes(data))

    def flush(self) -> None:
        pass

    def read(self, _n: int) -> bytes:
        return self.reply

    def close(self) -> None:
        self.closed = True


def make_driver(monkeypatch, fake_serial: FakeSerial, **overrides) -> CivPttDriver:
    monkeypatch.setattr(
        "multimodem.ptt.civ.serial.Serial",
        lambda: fake_serial,
    )
    config = PttControlConfig(driver="civ", serial_port="/dev/fake", baud=19200, **overrides)
    return CivPttDriver(config)


def test_start_opens_port_with_rts_dtr_low(monkeypatch):
    fake = FakeSerial()
    driver = make_driver(monkeypatch, fake)

    driver.start()

    assert fake.rts is False
    assert fake.dtr is False
    assert fake.opened is True


def test_set_ptt_on_sends_civ_frame_with_default_address(monkeypatch):
    fake = FakeSerial()
    driver = make_driver(monkeypatch, fake)
    driver.start()

    driver.set_ptt(True)

    assert fake.written == [bytes.fromhex("fefea4e01c0001fd")]


def test_set_ptt_off_sends_civ_frame(monkeypatch):
    fake = FakeSerial()
    driver = make_driver(monkeypatch, fake)
    driver.start()

    driver.set_ptt(False)

    assert fake.written == [bytes.fromhex("fefea4e01c0000fd")]


def test_custom_radio_address_used_in_frame(monkeypatch):
    fake = FakeSerial()
    driver = make_driver(monkeypatch, fake, radio_address=0x58)
    driver.start()

    driver.set_ptt(True)

    assert fake.written == [bytes.fromhex("fefe58e01c0001fd")]


def test_ng_reply_raises_ptt_error(monkeypatch):
    fake = FakeSerial()
    fake.reply = bytes.fromhex("fefee0a4fafd")
    driver = make_driver(monkeypatch, fake)
    driver.start()

    with pytest.raises(PttError):
        driver.set_ptt(True)


def test_no_reply_raises_ptt_error(monkeypatch):
    fake = FakeSerial()
    fake.reply = b""
    driver = make_driver(monkeypatch, fake)
    driver.start()

    with pytest.raises(PttError):
        driver.set_ptt(True)


def test_set_ptt_before_start_raises():
    driver = CivPttDriver(PttControlConfig(driver="civ", serial_port="/dev/fake", baud=19200))
    with pytest.raises(PttError):
        driver.set_ptt(True)


def test_stop_unkeys_and_closes_port(monkeypatch):
    fake = FakeSerial()
    driver = make_driver(monkeypatch, fake)
    driver.start()

    driver.stop()

    assert fake.written == [bytes.fromhex("fefea4e01c0000fd")]
    assert fake.closed is True


# -- CI-V port auto-detect --------------------------------------------------


def port(device, vid=0x0C26, location=None, hwid=""):
    return SimpleNamespace(device=device, vid=vid, location=location, hwid=hwid)


def test_selects_the_only_icom_port():
    ports = [port("/dev/other", vid=0x0403), port("/dev/icom")]
    assert select_civ_port(ports) == "/dev/icom"


def test_no_icom_ports_raises():
    ports = [port("/dev/other", vid=0x0403)]
    with pytest.raises(PttError, match="no Icom"):
        select_civ_port(ports)


def test_multiple_icom_ports_prefers_posix_interface_zero():
    ports = [
        port("/dev/cu.usbmodem1", location="1-1.4:1.2"),
        port("/dev/cu.usbmodem3", location="1-1.4:1.0"),
    ]
    assert select_civ_port(ports) == "/dev/cu.usbmodem3"


def test_multiple_icom_ports_prefers_windows_mi_00():
    ports = [
        port("COM4", hwid="USB VID:PID=0C26:0036 MI_02"),
        port("COM3", hwid="USB VID:PID=0C26:0036 MI_00"),
    ]
    assert select_civ_port(ports) == "COM3"


def test_multiple_icom_ports_ambiguous_without_interface_hints_raises():
    ports = [port("/dev/cu.usbmodem1"), port("/dev/cu.usbmodem3")]
    with pytest.raises(PttError, match="multiple Icom"):
        select_civ_port(ports)
