from __future__ import annotations

import pytest

from multimodem.config import PttControlConfig
from multimodem.ptt import PttError
from multimodem.ptt.cm108 import Cm108PttDriver


class FakeHidDevice:
    def __init__(self) -> None:
        self.opened: tuple[int, int] | None = None
        self.written: list[bytes] = []
        self.closed = False
        self.open_error: Exception | None = None
        self.write_error: Exception | None = None

    def open(self, vendor_id: int, product_id: int) -> None:
        if self.open_error:
            raise self.open_error
        self.opened = (vendor_id, product_id)

    def write(self, report: bytes) -> None:
        if self.write_error:
            raise self.write_error
        self.written.append(bytes(report))

    def close(self) -> None:
        self.closed = True


def make_driver(monkeypatch, device: FakeHidDevice, **overrides) -> Cm108PttDriver:
    monkeypatch.setattr("multimodem.ptt.cm108.hid.device", lambda: device)
    config = PttControlConfig(driver="cm108", **overrides)
    return Cm108PttDriver(config)


def test_start_opens_default_digirig_ids(monkeypatch):
    device = FakeHidDevice()
    driver = make_driver(monkeypatch, device)

    driver.start()

    assert device.opened == (0x0D8C, 0x000C)


def test_start_opens_configured_ids(monkeypatch):
    device = FakeHidDevice()
    driver = make_driver(monkeypatch, device, vendor_id=0x1234, product_id=0x5678)

    driver.start()

    assert device.opened == (0x1234, 0x5678)


def test_start_raises_ptt_error_when_device_cannot_open(monkeypatch):
    device = FakeHidDevice()
    device.open_error = OSError("no such device")
    driver = make_driver(monkeypatch, device)

    with pytest.raises(PttError):
        driver.start()


def test_set_ptt_on_drives_default_gpio3_high(monkeypatch):
    device = FakeHidDevice()
    driver = make_driver(monkeypatch, device)
    driver.start()

    driver.set_ptt(True)

    assert device.written == [bytes([0x00, 0x00, 0x04, 0x04])]


def test_set_ptt_off_drives_default_gpio3_low(monkeypatch):
    device = FakeHidDevice()
    driver = make_driver(monkeypatch, device)
    driver.start()

    driver.set_ptt(False)

    assert device.written == [bytes([0x00, 0x00, 0x04, 0x00])]


def test_custom_gpio_pin_selects_correct_bit(monkeypatch):
    device = FakeHidDevice()
    driver = make_driver(monkeypatch, device, gpio_pin=1)
    driver.start()

    driver.set_ptt(True)

    assert device.written == [bytes([0x00, 0x00, 0x01, 0x01])]


def test_active_low_inverts_drive_level(monkeypatch):
    device = FakeHidDevice()
    driver = make_driver(monkeypatch, device, active_high=False)
    driver.start()

    driver.set_ptt(True)
    driver.set_ptt(False)

    assert device.written == [
        bytes([0x00, 0x00, 0x04, 0x00]),  # keyed: drive low
        bytes([0x00, 0x00, 0x04, 0x04]),  # unkeyed: drive high
    ]


def test_set_ptt_before_start_raises():
    driver = Cm108PttDriver(PttControlConfig(driver="cm108"))
    with pytest.raises(PttError):
        driver.set_ptt(True)


def test_write_failure_raises_ptt_error(monkeypatch):
    device = FakeHidDevice()
    device.write_error = OSError("device unplugged")
    driver = make_driver(monkeypatch, device)
    driver.start()

    with pytest.raises(PttError):
        driver.set_ptt(True)


def test_stop_unkeys_and_closes_device(monkeypatch):
    device = FakeHidDevice()
    driver = make_driver(monkeypatch, device)
    driver.start()
    driver.set_ptt(True)

    driver.stop()

    assert device.written[-1] == bytes([0x00, 0x00, 0x04, 0x00])  # unkeyed
    assert device.closed is True


def test_stop_before_start_is_a_no_op():
    driver = Cm108PttDriver(PttControlConfig(driver="cm108"))
    driver.stop()  # must not raise
