"""Minimal exact-host Testnet aggregate-trade WebSocket collector."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import socket
import ssl
import struct
import threading
import time
from collections import deque
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any

from ai_quant.binance_egress.testnet_probe import TESTNET_STREAM_HOST, TestnetProbeError

MAX_FRAME_BYTES = 1_048_576


class AggregateTradeWindow:
    """Thread-safe normal-quantity trade window keyed by symbol."""

    def __init__(self, symbols: tuple[str, ...], *, capacity_per_symbol: int = 10_000) -> None:
        if not symbols or capacity_per_symbol < 1:
            raise ValueError("aggregate-trade window configuration is invalid")
        self._symbols = frozenset(symbols)
        self._documents = {
            symbol: deque[dict[str, Any]](maxlen=capacity_per_symbol) for symbol in symbols
        }
        self._lock = threading.Lock()

    def ingest(self, document: dict[str, Any]) -> bool:
        try:
            symbol = str(document["s"])
            trade_time = int(document["T"])
            normal_quantity = Decimal(str(document["nq"]))
            Decimal(str(document["p"]))
            int(document["a"])
            int(document["f"])
            int(document["l"])
            buyer_is_maker = document["m"]
        except (KeyError, TypeError, ValueError, InvalidOperation):
            return False
        if (
            symbol not in self._symbols
            or trade_time < 0
            or not normal_quantity.is_finite()
            or normal_quantity < 0
            or not isinstance(buyer_is_maker, bool)
        ):
            return False
        with self._lock:
            self._documents[symbol].append(dict(document))
        return True

    def snapshot(self, symbol: str, *, now_ms: int, maximum_age_ms: int) -> list[dict[str, Any]]:
        if symbol not in self._symbols or maximum_age_ms < 1:
            raise ValueError("aggregate-trade snapshot request is invalid")
        cutoff = now_ms - maximum_age_ms
        with self._lock:
            return [
                dict(document)
                for document in self._documents[symbol]
                if cutoff <= int(document["T"]) <= now_ms
            ]


class TestnetAggregateTradeStream:
    """Reconnect a combined public stream and retain recent validated trades."""

    def __init__(
        self,
        symbols: tuple[str, ...],
        *,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self.symbols = symbols
        self.window = AggregateTradeWindow(symbols)
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="testnet-aggregate-trades",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=15)

    def snapshot(self, symbol: str, *, maximum_age_ms: int = 2_000) -> list[dict[str, Any]]:
        return self.window.snapshot(
            symbol,
            now_ms=self._clock_ms(),
            maximum_age_ms=maximum_age_ms,
        )

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._consume_connection()
            except (OSError, TimeoutError, ssl.SSLError, TestnetProbeError):
                self._stop.wait(1)

    def _consume_connection(self) -> None:
        streams = "/".join(f"{symbol.lower()}@aggTrade" for symbol in self.symbols)
        path = f"/public/stream?streams={streams}"
        with _open_websocket(TESTNET_STREAM_HOST, path) as connection:
            connection.settimeout(5)
            while not self._stop.is_set():
                try:
                    opcode, payload = _read_frame(connection)
                except TimeoutError:
                    continue
                if opcode == 0x8:
                    return
                if opcode == 0x9:
                    connection.sendall(_masked_frame(0xA, payload))
                    continue
                if opcode != 0x1:
                    continue
                try:
                    decoded = json.loads(payload)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not isinstance(decoded, dict):
                    continue
                document = decoded.get("data", decoded)
                if isinstance(document, dict):
                    self.window.ingest(document)


def _open_websocket(host: str, path: str) -> ssl.SSLSocket:
    if host != TESTNET_STREAM_HOST or not path.startswith("/public/stream?"):
        raise TestnetProbeError("TESTNET_STREAM_DESTINATION_DENIED")
    key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "Connection: Upgrade\r\n"
        "Upgrade: websocket\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "User-Agent: aiq-testnet-stream/1\r\n\r\n"
    ).encode("ascii")
    raw = socket.create_connection((host, 443), timeout=10)
    try:
        connection = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        connection.sendall(request)
        headers = _read_until(connection, b"\r\n\r\n", 16_384)
        lines = headers.split(b"\r\n")
        if not lines or lines[0] != b"HTTP/1.1 101 Switching Protocols":
            raise TestnetProbeError("TESTNET_STREAM_UPGRADE_REJECTED")
        expected = base64.b64encode(
            hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii"),
                usedforsecurity=False,
            ).digest()
        )
        accepts = [
            line.split(b":", 1)[1].strip()
            for line in lines[1:]
            if line.lower().startswith(b"sec-websocket-accept:")
        ]
        if accepts != [expected]:
            raise TestnetProbeError("TESTNET_STREAM_ACCEPT_INVALID")
        return connection
    except Exception:
        raw.close()
        raise


def _read_until(connection: ssl.SSLSocket, marker: bytes, maximum: int) -> bytes:
    payload = bytearray()
    while marker not in payload:
        chunk = connection.recv(4096)
        if not chunk:
            raise TestnetProbeError("TESTNET_STREAM_CLOSED_DURING_UPGRADE")
        payload.extend(chunk)
        if len(payload) > maximum:
            raise TestnetProbeError("TESTNET_STREAM_UPGRADE_TOO_LARGE")
    headers, remainder = bytes(payload).split(marker, 1)
    if remainder:
        raise TestnetProbeError("TESTNET_STREAM_EARLY_FRAME_UNSUPPORTED")
    return headers


def _read_exact(connection: ssl.SSLSocket, size: int) -> bytes:
    payload = bytearray()
    while len(payload) < size:
        try:
            chunk = connection.recv(size - len(payload))
        except TimeoutError as exc:
            raise TimeoutError from exc
        if not chunk:
            raise TestnetProbeError("TESTNET_STREAM_CLOSED")
        payload.extend(chunk)
    return bytes(payload)


def _read_frame(connection: ssl.SSLSocket) -> tuple[int, bytes]:
    header = _read_exact(connection, 2)
    final = bool(header[0] & 0x80)
    opcode = header[0] & 0x0F
    masked = bool(header[1] & 0x80)
    length = header[1] & 0x7F
    if not final or masked:
        raise TestnetProbeError("TESTNET_STREAM_FRAME_INVALID")
    if length == 126:
        length = struct.unpack("!H", _read_exact(connection, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _read_exact(connection, 8))[0]
    if length > MAX_FRAME_BYTES:
        raise TestnetProbeError("TESTNET_STREAM_FRAME_TOO_LARGE")
    return opcode, _read_exact(connection, length)


def _masked_frame(opcode: int, payload: bytes) -> bytes:
    if len(payload) > 125:
        raise TestnetProbeError("TESTNET_STREAM_CONTROL_FRAME_TOO_LARGE")
    mask = secrets.token_bytes(4)
    masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return bytes((0x80 | opcode, 0x80 | len(payload))) + mask + masked
