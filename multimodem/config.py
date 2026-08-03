"""Configuration loading for multimodem."""
from __future__ import annotations

from dataclasses import dataclass, field

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib


@dataclass
class ModemConfig:
    name: str
    type: str  # "agwpe" | "ardop" | "vara"
    host: str
    port: int
    # VARA/ARDOP often expose a second control/data port pair
    control_port: int | None = None
    # For type="agwpe": which AGWPE port/channel on the backend engine
    # (direwolf, UZ7HO soundmodem, ...) this entry addresses. A single
    # backend instance can expose several channels (e.g. two radios on
    # one direwolf), each reachable as a separate AGWPE port on the same
    # TCP connection; defaults to 0, the engine's first port.
    agwpe_port: int = 0


@dataclass
class ClientServerConfig:
    host: str = "0.0.0.0"
    port: int = 8000


@dataclass
class RigctldConfig:
    host: str = "0.0.0.0"
    port: int = 4532


@dataclass
class AppConfig:
    client_server: ClientServerConfig = field(default_factory=ClientServerConfig)
    rigctld: RigctldConfig = field(default_factory=RigctldConfig)
    modems: list[ModemConfig] = field(default_factory=list)

    @classmethod
    def from_file(cls, path: str) -> "AppConfig":
        with open(path, "rb") as f:
            raw = tomllib.load(f)

        cs_raw = raw.get("client_server", {})
        rig_raw = raw.get("rigctld", {})
        modems_raw = raw.get("modems", [])

        return cls(
            client_server=ClientServerConfig(**cs_raw),
            rigctld=RigctldConfig(**rig_raw),
            modems=[ModemConfig(**m) for m in modems_raw],
        )
