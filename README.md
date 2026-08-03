# multimodem

AGWPE multiplexer that lets several client apps (Winlink Express, a BBS,
APRS software) share one radio across several backend modems (direwolf,
ARDOP, VARA ARQ, VARA's KISS port), serializing access to the single RF
channel via a shared arbiter, and exposing a rigctld-compatible PTT
endpoint.

## Install

```
pip install -e .
```

## Configure

Copy the example config and edit it for your station (hosts/ports for
your actual direwolf/ARDOP/VARA instances). `config.toml` is gitignored
since it's local to each station -- only `config.example.toml` is
tracked.

```
cp config.example.toml config.toml
```

## Run

```
multimodem -c config.toml
```

or, without installing:

```
python -m multimodem -c config.toml
```

## Tests

```
pip install -e ".[dev]"
pytest
```
