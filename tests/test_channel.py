import pytest

from multimodem.channel import ChannelArbiter, ChannelBusyError, ChannelState


def test_starts_idle():
    channel = ChannelArbiter()
    assert channel.state == ChannelState.IDLE
    assert channel.is_available()


def test_second_modem_cannot_acquire_while_connected():
    channel = ChannelArbiter()
    channel.acquire_connection("vara")
    with pytest.raises(ChannelBusyError):
        channel.acquire_connection("ardop")


def test_release_returns_channel_to_idle():
    channel = ChannelArbiter()
    channel.acquire_connection("vara")
    channel.release_connection("vara")
    assert channel.is_available()
    assert channel.owner is None


def test_ptt_refused_when_channel_held_by_another_owner():
    channel = ChannelArbiter()
    channel.acquire_connection("vara")
    assert channel.request_ptt("rigctld") is False
