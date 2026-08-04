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
class PttControlConfig:
    """Hardware PTT keying, selected by `driver`. Fields not used by the
    selected driver are ignored; most have a sane default and only need
    setting for nonstandard hardware -- see config.example.toml.
    """

    driver: str = "none"  # "none" | "cm108" | "civ" | "rts" | "rigctld"

    # civ: CAT PTT over a USB-serial CI-V connection (e.g. IC-705).
    # rts: also uses serial_port -- the USB-serial port whose RTS line is
    # wired to PTT.
    serial_port: str | None = None  # omit to auto-detect a single Icom USB device (civ only)
    baud: int | None = None  # required when driver = "civ"; defaults to 9600 for "rts"
    radio_address: int | None = None  # defaults to the IC-705's 0xa4

    # cm108: USB-audio-fob GPIO PTT (Digirig, SignaLink, ...).
    # active_high is also used by "rts" (True = PTT keys on RTS high).
    vendor_id: int | None = None  # defaults to a common digirig CM108B id
    product_id: int | None = None
    gpio_pin: int | None = None  # defaults to 3, the common PTT wiring
    active_high: bool | None = None  # defaults to True

    # rigctld: forward granted PTT requests to an externally-managed
    # hamlib rigctld process. Distinct from the [rigctld] section above,
    # which is the *server* multimodem itself exposes to clients.
    host: str = "127.0.0.1"
    port: int = 4532

    def __post_init__(self) -> None:
        if self.driver not in ("none", "cm108", "civ", "rts", "rigctld"):
            raise ValueError(f"unknown ptt_control.driver: {self.driver!r}")
        if self.driver == "civ" and self.baud is None:
            raise ValueError('ptt_control.baud is required when driver = "civ"')
        if self.driver == "rts" and self.serial_port is None:
            raise ValueError('ptt_control.serial_port is required when driver = "rts"')


@dataclass
class AppConfig:
    client_server: ClientServerConfig = field(default_factory=ClientServerConfig)
    rigctld: RigctldConfig = field(default_factory=RigctldConfig)
    ptt_control: PttControlConfig = field(default_factory=PttControlConfig)
    modems: list[ModemConfig] = field(default_factory=list)

    @classmethod
    def from_file(cls, path: str) -> "AppConfig":
        with open(path, "rb") as f:
            raw = tomllib.load(f)

        cs_raw = raw.get("client_server", {})
        rig_raw = raw.get("rigctld", {})
        ptt_raw = raw.get("ptt_control", {})
        modems_raw = raw.get("modems", [])

        return cls(
            client_server=ClientServerConfig(**cs_raw),
            rigctld=RigctldConfig(**rig_raw),
            ptt_control=PttControlConfig(**ptt_raw),
            modems=[ModemConfig(**m) for m in modems_raw],
        )
