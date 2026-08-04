from __future__ import annotations

import textwrap

import pytest

from multimodem.config import AppConfig, PttControlConfig


def write_config(tmp_path, text: str) -> str:
    path = tmp_path / "config.toml"
    path.write_text(textwrap.dedent(text))
    return str(path)


def test_ptt_control_defaults_to_no_hardware():
    config = PttControlConfig()
    assert config.driver == "none"


def test_app_config_defaults_ptt_control_when_section_absent(tmp_path):
    path = write_config(
        tmp_path,
        """
        [client_server]
        host = "0.0.0.0"
        port = 9000
        """,
    )
    config = AppConfig.from_file(path)
    assert config.ptt_control == PttControlConfig()


def test_app_config_parses_cm108_section(tmp_path):
    path = write_config(
        tmp_path,
        """
        [ptt_control]
        driver = "cm108"
        vendor_id = 0x0d8c
        product_id = 0x000c
        gpio_pin = 1
        active_high = false
        """,
    )
    config = AppConfig.from_file(path)
    assert config.ptt_control.driver == "cm108"
    assert config.ptt_control.vendor_id == 0x0D8C
    assert config.ptt_control.gpio_pin == 1
    assert config.ptt_control.active_high is False


def test_app_config_parses_civ_section(tmp_path):
    path = write_config(
        tmp_path,
        """
        [ptt_control]
        driver = "civ"
        serial_port = "/dev/cu.usbserial-1410"
        baud = 19200
        radio_address = 0xa4
        """,
    )
    config = AppConfig.from_file(path)
    assert config.ptt_control.serial_port == "/dev/cu.usbserial-1410"
    assert config.ptt_control.baud == 19200
    assert config.ptt_control.radio_address == 0xA4


def test_app_config_parses_rigctld_client_section(tmp_path):
    path = write_config(
        tmp_path,
        """
        [ptt_control]
        driver = "rigctld"
        host = "127.0.0.1"
        port = 4533
        """,
    )
    config = AppConfig.from_file(path)
    assert config.ptt_control.driver == "rigctld"
    assert config.ptt_control.port == 4533


def test_civ_driver_requires_baud():
    with pytest.raises(ValueError, match="baud"):
        PttControlConfig(driver="civ")


def test_unknown_driver_rejected():
    with pytest.raises(ValueError, match="unknown ptt_control.driver"):
        PttControlConfig(driver="carrier-pigeon")


def test_example_config_file_is_valid():
    config = AppConfig.from_file("config.example.toml")
    assert config.ptt_control.driver == "none"
    assert len(config.modems) >= 1
