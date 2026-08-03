"""Entry point: wire config -> backends -> client server -> rigctld."""
from __future__ import annotations

import argparse
import asyncio
import logging

from .agwpe_server import AgwpeClientServer
from .channel import ChannelArbiter
from .config import AppConfig
from .modems.agwpe import AgwpeModemBackend
from .modems.ardop import ArdopModemBackend
from .modems.base import ModemBackend
from .modems.vara import VaraModemBackend
from .modems.vara_kiss import VaraKissBackend
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Amateur radio modem multiplexer")
    parser.add_argument("-c", "--config", default="config.toml")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    config = AppConfig.from_file(args.config)
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
