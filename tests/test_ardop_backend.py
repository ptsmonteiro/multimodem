"""Unit tests for ArdopModemBackend's PTT handling.

Mirrors the PTT coverage in test_vara_backend.py -- ARDOP's "PTT TRUE"/
"PTT FALSE" lines are the same shape as VARA's "PTT ON"/"PTT OFF" (see
the note in modems/ardop.py). This file only covers that new PTT
behavior, not the rest of ArdopModemBackend's state machine, which has
no prior test coverage to extend here.
"""
from __future__ import annotations

import logging

from multimodem.channel import ChannelArbiter, ChannelState
from multimodem.config import ModemConfig
from multimodem.modems.ardop import ArdopModemBackend


def make_backend(control_port: int | None = 8516) -> ArdopModemBackend:
    config = ModemConfig(
        name="ardop", type="ardop", host="127.0.0.1", port=8515, control_port=control_port
    )
    return ArdopModemBackend(config, ChannelArbiter())


def test_ptt_true_false_before_connection_claims_and_releases_channel():
    backend = make_backend()
    backend._handle_command_line("PTT TRUE")
    assert backend.channel.state == ChannelState.TRANSMITTING
    assert backend.channel.owner == "ardop"

    backend._handle_command_line("PTT FALSE")
    assert backend.channel.is_available()


def test_ptt_true_false_during_active_session_does_not_disturb_connected_state():
    backend = make_backend()
    backend._handle_command_line("NEWSTATE CONNECTED")
    assert backend.channel.state == ChannelState.CONNECTED

    backend._handle_command_line("PTT TRUE")
    assert backend.channel.state == ChannelState.CONNECTED
    assert backend.channel.owner == "ardop"

    backend._handle_command_line("PTT FALSE")
    assert backend.channel.state == ChannelState.CONNECTED
    assert backend.channel.owner == "ardop"


def test_ptt_true_false_during_active_session_still_keys_hardware():
    backend = make_backend()
    events = []
    backend.channel.on_ptt = events.append
    backend._handle_command_line("NEWSTATE CONNECTED")

    backend._handle_command_line("PTT TRUE")
    backend._handle_command_line("PTT FALSE")

    assert events == [True, False]


def test_ptt_true_refused_logs_warning(caplog):
    backend = make_backend()
    backend.channel.acquire_connection("vara")  # another owner holds it

    with caplog.at_level(logging.WARNING):
        backend._handle_command_line("PTT TRUE")

    assert backend.channel.owner == "vara"  # unchanged, still refused
    assert "PTT TRUE refused" in caplog.text
