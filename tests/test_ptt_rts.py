from __future__ import annotations

import pytest

from multimodem.config import PttControlConfig
from multimodem.ptt import PttError
from multimodem.ptt.rts import RtsPttDriver


class FakeSerial:
    def __init__(self) -> None:
        self.port = None
        self.baudrate = None
        self.rts = True
        self.dtr = True
        self.opened = False
        self.closed = False

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True


def make_driver(monkeypatch, fake_serial: FakeSerial, **overrides) -> RtsPttDriver:
    monkeypatch.setattr("multimodem.ptt.rts.serial.Serial", lambda: fake_serial)
    config = PttControlConfig(driver="rts", serial_port="COM5", **overrides)
    return RtsPttDriver(config)


def test_start_opens_port_with_rts_idle_low_by_default(monkeypatch):
    fake = FakeSerial()
    driver = make_driver(monkeypatch, fake)

    driver.start()

    assert fake.rts is False  # active_high defaults True -> idle is low
    assert fake.dtr is False
    assert fake.opened is True
    assert fake.baudrate == 9600


def test_start_with_active_low_idles_rts_high(monkeypatch):
    fake = FakeSerial()
    driver = make_driver(monkeypatch, fake, active_high=False)

    driver.start()

    assert fake.rts is True


def test_set_ptt_on_drives_rts_high_when_active_high(monkeypatch):
    fake = FakeSerial()
    driver = make_driver(monkeypatch, fake)
    driver.start()

    driver.set_ptt(True)

    assert fake.rts is True


def test_set_ptt_off_drives_rts_low_when_active_high(monkeypatch):
    fake = FakeSerial()
    driver = make_driver(monkeypatch, fake)
    driver.start()

    driver.set_ptt(True)
    driver.set_ptt(False)

    assert fake.rts is False


def test_set_ptt_on_drives_rts_low_when_active_low(monkeypatch):
    fake = FakeSerial()
    driver = make_driver(monkeypatch, fake, active_high=False)
    driver.start()

    driver.set_ptt(True)

    assert fake.rts is False


def test_set_ptt_before_start_raises():
    driver = RtsPttDriver(PttControlConfig(driver="rts", serial_port="COM5"))
    with pytest.raises(PttError):
        driver.set_ptt(True)


def test_stop_unkeys_and_closes_port(monkeypatch):
    fake = FakeSerial()
    driver = make_driver(monkeypatch, fake)
    driver.start()
    driver.set_ptt(True)

    driver.stop()

    assert fake.rts is False
    assert fake.closed is True


def test_custom_baud_used(monkeypatch):
    fake = FakeSerial()
    driver = make_driver(monkeypatch, fake, baud=4800)

    driver.start()

    assert fake.baudrate == 4800


def test_serial_port_required_for_rts_driver():
    with pytest.raises(ValueError, match="serial_port"):
        PttControlConfig(driver="rts")
