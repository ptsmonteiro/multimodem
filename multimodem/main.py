"""Entry point: wire config -> backends -> client server -> rigctld."""
from __future__ import annotations

import argparse
import asyncio
import logging
import time

from .agwpe_server import AgwpeClientServer
from .channel import ChannelArbiter
from .config import AppConfig
from .modems.agwpe import AgwpeModemBackend
from .modems.ardop import ArdopModemBackend
from .modems.base import ModemBackend
from .modems.vara import VaraModemBackend
from .modems.vara_kiss import VaraKissBackend
from .ptt import PttError, build_ptt_driver
from .rigctld_server import RigctldServer

log = logging.getLogger(__name__)

BACKEND_TYPES = {
    "agwpe": AgwpeModemBackend,
    "ardop": ArdopModemBackend,
    "vara": VaraModemBackend,
    "vara_kiss": VaraKissBackend,
}


def build_backends(config: AppConfig, channel: ChannelArbiter) -> dict[int, ModemBackend]:
    modems: dict[int, ModemBackend] = {}
    for i, modem_cfg in enumerate(config.modems):
        backend_cls = BACKEND_TYPES.get(modem_cfg.type)
        if backend_cls is None:
            raise ValueError(f"unknown modem type: {modem_cfg.type}")
        # AGWPE ports assigned to clients are the index in config, 0-based.
        modems[i] = backend_cls(modem_cfg, channel)
    return modems


async def run(config: AppConfig) -> None:
    channel = ChannelArbiter()
    ptt_driver = build_ptt_driver(config.ptt_control)
    ptt_driver.start()
    channel.on_ptt = ptt_driver.set_ptt

    modems = build_backends(config, channel)

    for modem in modems.values():
        modem.start()

    client_server = AgwpeClientServer(
        config.client_server.host, config.client_server.port, modems
    )
    rigctld = RigctldServer(config.rigctld.host, config.rigctld.port, channel)

    await client_server.start()
    await rigctld.start()

    try:
        await asyncio.Event().wait()
    finally:
        await client_server.stop()
        await rigctld.stop()
        for modem in modems.values():
            await modem.stop()
        ptt_driver.stop()


def ptt_test(config: AppConfig, hold_seconds: float = 1.0) -> None:
    """Key the configured PTT driver on, hold, then unkey. For bench-testing PTT wiring/config."""
    if config.ptt_control.driver == "none":
        log.warning("ptt_control.driver is \"none\" -- nothing to test, no radio configured")
        return

    log.info("ptt-test: driver=%s, starting driver", config.ptt_control.driver)
    try:
        ptt_driver = build_ptt_driver(config.ptt_control)
        ptt_driver.start()
    except PttError as exc:
        log.error("ptt-test: failed to start driver: %s", exc)
        raise SystemExit(1) from exc

    try:
        log.info("ptt-test: keying PTT ON")
        ptt_driver.set_ptt(True)
        log.info("ptt-test: PTT keyed, holding for %.1fs", hold_seconds)
        time.sleep(hold_seconds)
        log.info("ptt-test: unkeying PTT OFF")
        ptt_driver.set_ptt(False)
        log.info("ptt-test: PTT unkeyed")
    except PttError as exc:
        log.error("ptt-test: PTT command failed: %s", exc)
        raise SystemExit(1) from exc
    finally:
        ptt_driver.stop()
    log.info("ptt-test: done")


def main() -> None:
    parser = argparse.ArgumentParser(description="Amateur radio modem multiplexer")
    parser.add_argument("-c", "--config", default="config.toml")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--ptt-test",
        action="store_true",
        help="key PTT on for 1s then off using the configured ptt_control driver, then exit",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    config = AppConfig.from_file(args.config)

    if args.ptt_test:
        ptt_test(config)
        return

    asyncio.run(run(config))


if __name__ == "__main__":
    main()
