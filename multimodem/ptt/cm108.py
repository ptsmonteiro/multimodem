"""CM108/CM119 USB-audio-fob PTT keying via a raw HID GPIO report.

Digirig and most USB-sound-fob radio interfaces (SignaLink-style
devices too) are built around a CM108B-class chip with PTT wired to one
of its GPIO pins. That pin is set as an output and driven via a 4-byte
HID report: [report_id=0, unused=0, ddr_mask, data_mask], where each
mask is a bitmask over GPIO1-4 (bit 0 = GPIO1). This is the same scheme
Hamlib's cm108 backend, Direwolf, and fldigi use.

NOTE: this is public/prior-art protocol knowledge, not bench-verified
against physical hardware in this change -- confirm vendor_id/
product_id/gpio_pin/active_high against your actual interface (e.g.
`python -m hid` or the OS device list) before relying on it.
"""
from __future__ import annotations

import logging

import hid

from ..config import PttControlConfig
from . import PttDriver, PttError

log = logging.getLogger(__name__)

# Common digirig CM108B-class vendor/product id. Nonstandard interfaces
# should override these in config.
DEFAULT_VENDOR_ID = 0x0D8C
DEFAULT_PRODUCT_ID = 0x000C
DEFAULT_GPIO_PIN = 3


class Cm108PttDriver(PttDriver):
    def __init__(self, config: PttControlConfig) -> None:
        self.vendor_id = config.vendor_id if config.vendor_id is not None else DEFAULT_VENDOR_ID
        self.product_id = (
            config.product_id if config.product_id is not None else DEFAULT_PRODUCT_ID
        )
        self.gpio_pin = config.gpio_pin if config.gpio_pin is not None else DEFAULT_GPIO_PIN
        self.active_high = config.active_high if config.active_high is not None else True
        self._device: hid.device | None = None

    def start(self) -> None:
        device = hid.device()
        try:
            device.open(self.vendor_id, self.product_id)
        except OSError as exc:
            raise PttError(
                f"cannot open CM108 HID device {self.vendor_id:#06x}:{self.product_id:#06x}: {exc}"
            ) from exc
        self._device = device
        log.info(
            "cm108 PTT driver open (vid=%#06x pid=%#06x gpio=%d active_high=%s)",
            self.vendor_id, self.product_id, self.gpio_pin, self.active_high,
        )

    def stop(self) -> None:
        if not self._device:
            return
        try:
            self.set_ptt(False)
        except PttError:
            log.exception("cm108: failed to unkey on shutdown")
        self._device.close()
        self._device = None

    def set_ptt(self, on: bool) -> None:
        if not self._device:
            raise PttError("cm108 device not open")
        mask = 1 << (self.gpio_pin - 1)
        drive_high = on == self.active_high
        report = bytes([0x00, 0x00, mask, mask if drive_high else 0x00])
        try:
            self._device.write(report)
        except OSError as exc:
            raise PttError(f"cm108 HID write failed: {exc}") from exc
