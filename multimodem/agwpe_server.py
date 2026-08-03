"""Client-facing AGWPE server.

Accepts multiple simultaneous client app connections (e.g. Winlink
Express, a BBS app), each speaking standard AGWPE. Outbound frames are
routed to the appropriate backend ModemBackend by AGWPE port number.
Inbound connected-mode frames (a connect request, or data on an
existing connection) are routed to whichever client *registered* the
destination callsign on that port via an 'X' frame -- this is how a
single AGWPE port can be shared by multiple independent apps (e.g. a
BBS on N0CALL-1 and Winlink on N0CALL-2), exactly as real AGWPE
engines (direwolf, UZ7HO, etc.) do it. Unproto/UI traffic (APRS et
al.) has no such per-callsign owner -- it's broadcast to every client
connected on that port, same as AGWPE monitor traffic, and clients
filter locally the way real AGWPE client apps already do.

The shared ChannelArbiter enforces that only one connection/
transmission happens on the real RF channel at a time, regardless of
how many clients or backend modems are configured.
"""
from __future__ import annotations

import asyncio
import logging

from .agwpe_protocol import AgwFrame, read_frame
from .channel import ChannelBusyError
from .modems.base import ModemBackend

log = logging.getLogger(__name__)


def _describe(frame: AgwFrame) -> str:
    kind = frame.data_kind.decode("ascii", "replace")
    return (
        f"kind={kind!r} port={frame.port} from={frame.call_from!r} "
        f"to={frame.call_to!r} data={frame.data!r}"
    )

KIND_DATA = b"D"
KIND_UNPROTO = b"M"
KIND_CONNECT = b"C"
KIND_DISCONNECT = b"d"
KIND_VERSION = b"R"
KIND_REGISTER = b"X"
KIND_UNREGISTER = b"x"
KIND_PORT_INFO = b"G"


class AgwpeClientServer:
    def __init__(self, host: str, port: int, modems: dict[int, ModemBackend]) -> None:
        self.host = host
        self.port = port
        self.modems = modems  # AGWPE port number -> backend
        self._server: asyncio.AbstractServer | None = None
        # (agwpe_port, callsign) -> client writer that registered it.
        self._registrations: dict[tuple[int, str], asyncio.StreamWriter] = {}
        # Every connected client, for broadcasting unproto/monitor traffic.
        self._all_clients: set[asyncio.StreamWriter] = set()
        for client_port, modem in modems.items():
            # Bind the client-facing port explicitly: a backend's own
            # AGWPE port numbering (e.g. direwolf's) is unrelated to the
            # port index clients use to address this modem here.
            modem.on_frame = (
                lambda frame, client_port=client_port: self._on_backend_frame(
                    client_port, frame
                )
            )

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        log.info("AGWPE client server listening on %s:%s", self.host, self.port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        log.info("client connected: %s", peer)
        self._all_clients.add(writer)
        try:
            while True:
                frame = await read_frame(reader)
                if frame is None:
                    break
                log.debug("<- %s: %s", peer, _describe(frame))
                await self._handle_frame(frame, writer)
        except asyncio.IncompleteReadError as exc:
            log.debug(
                "%s: connection closed mid-frame (got %d of %d expected bytes)",
                peer, len(exc.partial), exc.expected,
            )
        except ConnectionError as exc:
            log.debug("%s: connection error: %s", peer, exc)
        finally:
            log.info("client disconnected: %s", peer)
            self._all_clients.discard(writer)
            self._drop_registrations(writer)
            writer.close()

    def _drop_registrations(self, writer: asyncio.StreamWriter) -> None:
        affected_ports = set()
        for key in [k for k, w in self._registrations.items() if w is writer]:
            log.info("unregistering %s on port %s (client disconnected)", key[1], key[0])
            del self._registrations[key]
            affected_ports.add(key[0])
        for port in affected_ports:
            asyncio.create_task(self._push_registered_calls(port))

    def _calls_on_port(self, port: int) -> list[str]:
        return [call for (p, call) in self._registrations if p == port]

    async def _push_registered_calls(self, port: int) -> None:
        modem = self.modems.get(port)
        if modem is not None:
            await modem.update_registered_calls(self._calls_on_port(port))

    async def _reply(self, writer: asyncio.StreamWriter, frame: AgwFrame) -> None:
        log.debug("-> %s: %s", writer.get_extra_info("peername"), _describe(frame))
        writer.write(frame.encode())
        await writer.drain()

    async def _handle_frame(self, frame: AgwFrame, writer: asyncio.StreamWriter) -> None:
        if frame.data_kind == KIND_PORT_INFO:
            await self._reply(writer, self._port_info_frame())
            return

        modem = self.modems.get(frame.port)
        if modem is None:
            log.warning("client referenced unknown AGWPE port %s", frame.port)
            return

        if frame.data_kind == KIND_VERSION:
            reply = AgwFrame(port=frame.port, data_kind=b"R", data=b"\x02\x01\x00\x00")
            await self._reply(writer, reply)
            return

        if frame.data_kind == KIND_REGISTER:
            callsign = frame.call_from.upper()
            self._registrations[(frame.port, callsign)] = writer
            log.info("registered %s on port %s", callsign, frame.port)
            await self._push_registered_calls(frame.port)
            return

        if frame.data_kind == KIND_UNREGISTER:
            self._registrations.pop((frame.port, frame.call_from.upper()), None)
            await self._push_registered_calls(frame.port)
            return

        if frame.data_kind in (KIND_DATA, KIND_UNPROTO):
            try:
                await modem.send_data(frame.call_from, frame.call_to, frame.data)
            except ChannelBusyError as exc:
                log.warning("dropping frame, %s", exc)
            return

        if frame.data_kind in (KIND_CONNECT, KIND_DISCONNECT):
            # Client-initiated outbound connect/disconnect: pass straight
            # through to the backend modem.
            await modem.send_control(frame)
            return

        log.debug("unhandled frame kind %r on port %s", frame.data_kind, frame.port)

    def _port_info_frame(self) -> AgwFrame:
        """Build the 'G' (port information) reply listing configured ports.

        Standard AGWPE wire format: an ASCII, semicolon-separated,
        null-terminated string "<count>;Port1 <name>;Port2 <name>;...;",
        as produced by real AGWPE engines (direwolf, UZ7HO) for this
        request.
        """
        ports = sorted(self.modems)
        parts = [str(len(ports))]
        parts.extend(f"Port{i + 1} {self.modems[p].name}" for i, p in enumerate(ports))
        data = (";".join(parts) + ";").encode("ascii", "ignore") + b"\x00"
        return AgwFrame(port=0, data_kind=KIND_PORT_INFO, data=data)

    def _on_backend_frame(self, port: int, frame: AgwFrame) -> None:
        """Route a frame arriving from a backend modem to the right client(s).

        Unproto/UI traffic (APRS et al.) has no per-callsign owner, so
        it's broadcast to every connected client with the correct port
        set in the frame, same as real AGWPE monitor delivery -- clients
        already filter by port themselves.

        Connected-mode frames (connect request, data, disconnect) are
        routed to whichever client registered the destination callsign
        (frame.call_to -- AGWPE always puts "the local side" of the
        link there for frames delivered to a client) on that port.
        """
        # Backends build frames with port=0 as a placeholder (they don't
        # know the client-facing AGWPE port); stamp the real one here.
        outbound = AgwFrame(
            port=port,
            data_kind=frame.data_kind,
            pid=frame.pid,
            call_from=frame.call_from,
            call_to=frame.call_to,
            data=frame.data,
        )
        encoded = outbound.encode()
        log.debug("<- backend port %s: %s", port, _describe(outbound))

        if frame.data_kind == KIND_UNPROTO:
            for client in self._all_clients:
                log.debug("-> %s: %s", client.get_extra_info("peername"), _describe(outbound))
                client.write(encoded)
                asyncio.create_task(client.drain())
            return

        target = self._registrations.get((port, frame.call_to.upper()))
        if target is None:
            log.warning(
                "no client registered for %s on port %s; dropping %s frame",
                frame.call_to, port, frame.data_kind,
            )
            return
        log.debug("-> %s: %s", target.get_extra_info("peername"), _describe(outbound))
        target.write(encoded)
        asyncio.create_task(target.drain())
