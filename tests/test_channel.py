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


# -- on_ptt hardware hook -------------------------------------------------


def test_on_ptt_hook_fires_on_request_and_release():
    channel = ChannelArbiter()
    events = []
    channel.on_ptt = events.append

    assert channel.request_ptt("vara") is True
    assert events == [True]

    channel.release_ptt("vara")
    assert events == [True, False]


def test_on_ptt_hook_not_called_when_request_refused():
    channel = ChannelArbiter()
    channel.acquire_connection("vara")
    events = []
    channel.on_ptt = events.append

    assert channel.request_ptt("rigctld") is False
    assert events == []


def test_on_ptt_hook_not_called_when_release_has_no_effect():
    channel = ChannelArbiter()
    events = []
    channel.on_ptt = events.append

    channel.release_ptt("nobody-owns-this")  # never acquired -- no-op

    assert events == []


def test_on_ptt_hook_failure_on_key_refuses_request_and_frees_channel():
    channel = ChannelArbiter()

    def failing_hook(on: bool) -> None:
        raise OSError("device unplugged")

    channel.on_ptt = failing_hook

    assert channel.request_ptt("vara") is False
    assert channel.is_available()
    assert channel.owner is None


def test_on_ptt_hook_failure_on_unkey_still_frees_channel():
    # A failed unkey can't be "undone" in software -- the channel must
    # still go back to idle so other modems aren't locked out forever,
    # even though the hardware may be stuck transmitting (this gets
    # logged loudly by release_ptt, not silently swallowed).
    channel = ChannelArbiter()
    channel.request_ptt("vara")

    def failing_hook(on: bool) -> None:
        if not on:
            raise OSError("device unplugged")

    channel.on_ptt = failing_hook
    channel.release_ptt("vara")

    assert channel.is_available()
    assert channel.owner is None


def test_on_ptt_hook_fires_during_connected_session_without_disturbing_it():
    # A CONNECTED session already owns the channel via acquire_connection;
    # request_ptt/release_ptt during it (ARQ's per-frame PTT toggling)
    # must still drive the hardware hook, but must not flip the arbiter
    # state away from CONNECTED -- see the note in vara.py.
    channel = ChannelArbiter()
    channel.acquire_connection("vara")
    events = []
    channel.on_ptt = events.append

    assert channel.request_ptt("vara") is True
    assert channel.state == ChannelState.CONNECTED
    assert channel.owner == "vara"

    channel.release_ptt("vara")
    assert channel.state == ChannelState.CONNECTED
    assert channel.owner == "vara"

    assert events == [True, False]
