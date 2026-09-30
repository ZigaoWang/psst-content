"""The database over HTTPS, for machines that can't use SSH (Claude Code cloud sessions, whose only way
out is a web proxy).

The server side runs on the VPS behind nginx at /tunnel: it accepts a WebSocket carrying a secret token
and relays its bytes to Postgres on 127.0.0.1:5432. The client side listens on a local port and relays
each connection over its own WebSocket, so psycopg connects to 127.0.0.1 as if the database were local.
Postgres still checks its own login (the limited `psst_agent` role), and TLS protects everything in between.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import socket
import threading

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import connect as connect_sync

DATABASE = ("127.0.0.1", 5432)
MAX_CONNECTIONS = 12
CHUNK = 65536

log = logging.getLogger("psst.tunnel")


async def _pump_socket_to_ws(reader: asyncio.StreamReader, ws) -> None:
    while data := await reader.read(CHUNK):
        await ws.send(data)
    await ws.close()


async def _pump_ws_to_socket(ws, writer: asyncio.StreamWriter) -> None:
    try:
        async for message in ws:
            writer.write(message if isinstance(message, bytes) else message.encode())
            await writer.drain()
    except ConnectionClosed:
        pass
    finally:
        writer.close()


async def _relay(ws, reader, writer) -> None:
    tasks = [asyncio.create_task(_pump_socket_to_ws(reader, ws)), asyncio.create_task(_pump_ws_to_socket(ws, writer))]
    _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    writer.close()
    await ws.close()


# Server (on the VPS) ------------------------------------------------------------------------------------

def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def serve_forever(port: int, token_sha256: str) -> None:
    slots = asyncio.Semaphore(MAX_CONNECTIONS)

    def check(connection, request):
        given = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not given or not hmac.compare_digest(token_hash(given), token_sha256):
            return connection.respond(401, "Unauthorized\n")
        if slots.locked():
            return connection.respond(503, "Too many connections\n")
        return None

    async def handler(ws) -> None:
        async with slots:
            try:
                reader, writer = await asyncio.open_connection(*DATABASE)
            except OSError:
                await ws.close(1011, "database unavailable")
                return
            await _relay(ws, reader, writer)

    # Only nginx can reach this port; it adds TLS and the public address.
    async with serve(handler, "127.0.0.1", port, process_request=check, max_size=None, compression=None,
                     ping_interval=30, server_header=None):
        log.info("Tunnel listening on 127.0.0.1:%s", port)
        await asyncio.Future()


# Client (on the cloud machine) ---------------------------------------------------------------------------

_client_port: int | None = None
_client_lock = threading.Lock()


def open_local(url: str, token: str) -> int:
    """Start relaying 127.0.0.1:<port> to the database through `url`, once per process. Returns the port."""
    global _client_port
    with _client_lock:
        if _client_port:
            return _client_port
        check(url, token)
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        port = listener.getsockname()[1]
        ready = threading.Event()
        thread = threading.Thread(target=lambda: asyncio.run(_client(listener, url, token, ready)), daemon=True,
                                  name="psst-tunnel")
        thread.start()
        if not ready.wait(10):
            raise RuntimeError("The database tunnel didn't start")
        _client_port = port
        return port


def check(url: str, token: str) -> None:
    """Fail early and clearly when the tunnel can't be reached or refuses the token."""
    try:
        with connect_sync(url, additional_headers={"Authorization": f"Bearer {token}"}, open_timeout=30,
                          user_agent_header="PsstContent/1.0"):
            pass
    except InvalidStatus as exc:
        status = exc.response.status_code
        reason = {401: "it refused the token (check PSST_TUNNEL_TOKEN)", 503: "it's busy; try again in a minute"}
        raise RuntimeError(f"Database tunnel {url}: {reason.get(status, f'it answered {status}')}") from None
    except (OSError, TimeoutError) as exc:
        raise RuntimeError(f"Can't reach the database tunnel {url}: {exc}. In a cloud session, allow "
                           "psst.zigao.wang in the environment's network access.") from None


async def _client(listener: socket.socket, url: str, token: str, ready: threading.Event) -> None:
    async def handle(reader, writer) -> None:
        try:
            async with connect(url, additional_headers={"Authorization": f"Bearer {token}"}, max_size=None,
                               compression=None, open_timeout=30, ping_interval=30,
                               user_agent_header="PsstContent/1.0") as ws:
                await _relay(ws, reader, writer)
        except Exception as exc:  # any failure to reach the tunnel closes this one connection
            log.warning("Tunnel connection failed: %s", exc)
            writer.close()

    server = await asyncio.start_server(handle, sock=listener)
    ready.set()
    async with server:
        await server.serve_forever()
