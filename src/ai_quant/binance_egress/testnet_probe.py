"""Bounded Binance USD-M Testnet capability probe owned by the egress package."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Any

from ai_quant.common.private_files import read_private_file

TESTNET_REST_BASE = "https://demo-fapi.binance.com"
TESTNET_STREAM_HOST = "demo-fstream.binance.com"
TESTNET_WS_API_HOST = "testnet.binancefuture.com"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class TestnetProbeError(RuntimeError):
    """A capability probe failed without exposing credential material."""


@dataclass(frozen=True, slots=True)
class HttpResult:
    status: int
    headers: Mapping[str, str]
    body: bytes


Transport = Callable[[str, str, Mapping[str, str], bytes | None], HttpResult]
WebSocketProbe = Callable[[str, str], None]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _urllib_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
) -> HttpResult:
    if not url.startswith(f"{TESTNET_REST_BASE}/fapi/"):
        raise TestnetProbeError("TESTNET_DESTINATION_DENIED")
    request = urllib.request.Request(  # noqa: S310 -- exact HTTPS origin checked above
        url, data=body, headers=dict(headers), method=method
    )
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=10) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise TestnetProbeError("TESTNET_RESPONSE_TOO_LARGE")
            return HttpResult(response.status, dict(response.headers.items()), payload)
    except urllib.error.HTTPError as exc:
        payload = exc.read(MAX_RESPONSE_BYTES + 1)
        if len(payload) > MAX_RESPONSE_BYTES:
            raise TestnetProbeError("TESTNET_RESPONSE_TOO_LARGE") from exc
        return HttpResult(exc.code, dict(exc.headers.items()), payload)
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        raise TestnetProbeError("TESTNET_TRANSPORT_FAILED") from exc


def websocket_upgrade(host: str, path: str) -> None:
    """Perform an exact TLS WebSocket upgrade and immediately close the socket."""
    if host not in {TESTNET_STREAM_HOST, TESTNET_WS_API_HOST}:
        raise TestnetProbeError("TESTNET_WEBSOCKET_HOST_DENIED")
    if not path.startswith("/") or "\r" in path or "\n" in path:
        raise TestnetProbeError("TESTNET_WEBSOCKET_PATH_INVALID")
    nonce = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "Connection: Upgrade\r\n"
        "Upgrade: websocket\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"Sec-WebSocket-Key: {nonce}\r\n"
        "User-Agent: aiq-testnet-probe/1\r\n\r\n"
    ).encode("ascii")
    context = ssl.create_default_context()
    try:
        with socket.create_connection((host, 443), timeout=10) as raw:
            with context.wrap_socket(raw, server_hostname=host) as connection:
                connection.settimeout(10)
                connection.sendall(request)
                response = bytearray()
                while b"\r\n\r\n" not in response and len(response) <= 16_384:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    response.extend(chunk)
    except (OSError, TimeoutError, ssl.SSLError) as exc:
        raise TestnetProbeError("TESTNET_WEBSOCKET_UPGRADE_FAILED") from exc
    status_line = bytes(response).split(b"\r\n", 1)[0]
    if status_line != b"HTTP/1.1 101 Switching Protocols":
        raise TestnetProbeError("TESTNET_WEBSOCKET_UPGRADE_REJECTED")


def _json_object(result: HttpResult, operation: str) -> dict[str, Any]:
    try:
        document = json.loads(result.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TestnetProbeError(f"{operation}_INVALID_JSON") from exc
    if not 200 <= result.status < 300:
        code = document.get("code") if isinstance(document, dict) else None
        raise TestnetProbeError(f"{operation}_HTTP_{result.status}_CODE_{code}")
    if not isinstance(document, dict):
        raise TestnetProbeError(f"{operation}_INVALID_RESPONSE")
    return document


def _json_list(result: HttpResult, operation: str) -> list[dict[str, Any]]:
    try:
        document = json.loads(result.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TestnetProbeError(f"{operation}_INVALID_JSON") from exc
    if not 200 <= result.status < 300:
        code = document.get("code") if isinstance(document, dict) else None
        raise TestnetProbeError(f"{operation}_HTTP_{result.status}_CODE_{code}")
    if not isinstance(document, list) or not all(isinstance(item, dict) for item in document):
        raise TestnetProbeError(f"{operation}_INVALID_RESPONSE")
    return document


class BinanceTestnetClient:
    """Small exact-destination client for an attended, bounded Testnet probe."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        transport: Transport = _urllib_transport,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        if not api_key or not api_secret or any(char.isspace() for char in api_key + api_secret):
            raise TestnetProbeError("TESTNET_CREDENTIAL_FORMAT_INVALID")
        self._api_key = api_key
        self._api_secret = api_secret.encode("ascii")
        self._transport = transport
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._server_offset_ms = 0

    def _call(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        signed: bool = False,
        api_key_required: bool = False,
    ) -> HttpResult:
        if not path.startswith("/fapi/") or ".." in path:
            raise TestnetProbeError("TESTNET_PATH_DENIED")
        values = dict(params or {})
        if signed:
            values["timestamp"] = self._clock_ms() + self._server_offset_ms
            values["recvWindow"] = 5000
        query = urllib.parse.urlencode(values)
        if signed:
            signature = hmac.new(
                self._api_secret, query.encode("ascii"), hashlib.sha256
            ).hexdigest()
            query = f"{query}&signature={signature}"
        url = f"{TESTNET_REST_BASE}{path}"
        if query:
            url = f"{url}?{query}"
        headers = {"Accept": "application/json", "User-Agent": "aiq-testnet-probe/1"}
        if signed or api_key_required:
            headers["X-MBX-APIKEY"] = self._api_key
        return self._transport(method, url, headers, b"" if method in {"POST", "PUT"} else None)

    def synchronize_time(self) -> tuple[int, int]:
        before = self._clock_ms()
        document = _json_object(self._call("GET", "/fapi/v1/time"), "SERVER_TIME")
        after = self._clock_ms()
        server_time = document.get("serverTime")
        if not isinstance(server_time, int):
            raise TestnetProbeError("SERVER_TIME_INVALID_RESPONSE")
        midpoint = before + ((after - before) // 2)
        self._server_offset_ms = server_time - midpoint
        return server_time, self._server_offset_ms

    def exchange_info(self) -> dict[str, Any]:
        return _json_object(self._call("GET", "/fapi/v1/exchangeInfo"), "EXCHANGE_INFO")

    def book_ticker(self, symbol: str) -> dict[str, Any]:
        return _json_object(
            self._call("GET", "/fapi/v1/ticker/bookTicker", params={"symbol": symbol}),
            "BOOK_TICKER",
        )

    def position_mode(self) -> dict[str, Any]:
        return _json_object(
            self._call("GET", "/fapi/v1/positionSide/dual", signed=True),
            "POSITION_MODE",
        )

    def symbol_config(self, symbol: str) -> list[dict[str, Any]]:
        return _json_list(
            self._call("GET", "/fapi/v1/symbolConfig", params={"symbol": symbol}, signed=True),
            "SYMBOL_CONFIG",
        )

    def open_orders(self, symbol: str) -> list[dict[str, Any]]:
        return _json_list(
            self._call("GET", "/fapi/v1/openOrders", params={"symbol": symbol}, signed=True),
            "OPEN_ORDERS",
        )

    def position_risk(self, symbol: str) -> list[dict[str, Any]]:
        return _json_list(
            self._call("GET", "/fapi/v3/positionRisk", params={"symbol": symbol}, signed=True),
            "POSITION_RISK",
        )

    def create_listen_key(self) -> str:
        document = _json_object(
            self._call("POST", "/fapi/v1/listenKey", api_key_required=True),
            "LISTEN_KEY_CREATE",
        )
        listen_key = document.get("listenKey")
        if not isinstance(listen_key, str) or len(listen_key) < 20:
            raise TestnetProbeError("LISTEN_KEY_CREATE_INVALID_RESPONSE")
        return listen_key

    def close_listen_key(self, listen_key: str) -> None:
        _json_object(
            self._call(
                "DELETE",
                "/fapi/v1/listenKey",
                params={"listenKey": listen_key},
                api_key_required=True,
            ),
            "LISTEN_KEY_CLOSE",
        )

    def test_order(self, params: Mapping[str, str]) -> None:
        _json_object(
            self._call("POST", "/fapi/v1/order/test", params=params, signed=True),
            "TEST_ORDER",
        )


def _decimal_step(value: Decimal, step: Decimal, rounding: str) -> Decimal:
    return (value / step).to_integral_value(rounding=rounding) * step


def _order_test_parameters(
    exchange_info: Mapping[str, Any],
    book_ticker: Mapping[str, Any],
    symbol: str,
) -> dict[str, str]:
    symbols = exchange_info.get("symbols")
    if not isinstance(symbols, list):
        raise TestnetProbeError("EXCHANGE_INFO_INVALID_RESPONSE")
    symbol_info = next(
        (item for item in symbols if isinstance(item, dict) and item.get("symbol") == symbol),
        None,
    )
    if not isinstance(symbol_info, dict) or symbol_info.get("status") != "TRADING":
        raise TestnetProbeError("TEST_SYMBOL_NOT_TRADING")
    filters = symbol_info.get("filters")
    if not isinstance(filters, list):
        raise TestnetProbeError("TEST_SYMBOL_FILTERS_INVALID")
    by_type = {
        item.get("filterType"): item for item in filters if isinstance(item, dict)
    }
    try:
        tick_size = Decimal(str(by_type["PRICE_FILTER"]["tickSize"]))
        lot = by_type["LOT_SIZE"]
        step_size = Decimal(str(lot["stepSize"]))
        minimum_quantity = Decimal(str(lot["minQty"]))
        minimum_notional = Decimal(str(by_type.get("MIN_NOTIONAL", {}).get("notional", "0")))
        bid_price = Decimal(str(book_ticker["bidPrice"]))
    except (KeyError, ArithmeticError) as exc:
        raise TestnetProbeError("TEST_SYMBOL_FILTERS_INVALID") from exc
    price = _decimal_step(bid_price * Decimal("0.90"), tick_size, ROUND_FLOOR)
    quantity = minimum_quantity
    if minimum_notional > 0:
        quantity = max(
            quantity,
            _decimal_step(minimum_notional / price, step_size, ROUND_CEILING),
        )
    return {
        "symbol": symbol,
        "side": "BUY",
        "type": "LIMIT",
        "timeInForce": "GTX",
        "quantity": format(quantity, "f"),
        "price": format(price, "f"),
        "newClientOrderId": f"aq-t-probe-{secrets.token_hex(6)}",
    }


def _credential(path: Path, repository_root: Path) -> str:
    raw = read_private_file(
        path,
        forbidden_repository_root=repository_root,
        maximum_bytes=512,
        unsafe_reason="TESTNET_SECRET_FILE_UNSAFE",
    )
    try:
        return raw.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise TestnetProbeError("TESTNET_CREDENTIAL_FORMAT_INVALID") from exc


def run_safe_testnet_probe(
    *,
    api_key_file: Path,
    api_secret_file: Path,
    repository_root: Path,
    symbol: str = "BTCUSDT",
    transport: Transport = _urllib_transport,
    websocket_probe: WebSocketProbe = websocket_upgrade,
) -> dict[str, Any]:
    """Validate connectivity, credentials, streams and a non-matching-engine test order."""
    started_at = datetime.now(UTC)
    api_key = _credential(api_key_file, repository_root)
    api_secret = _credential(api_secret_file, repository_root)
    client = BinanceTestnetClient(api_key, api_secret, transport=transport)
    server_time, offset = client.synchronize_time()
    exchange_info = client.exchange_info()
    ticker = client.book_ticker(symbol)
    position_mode = client.position_mode()
    symbol_config = client.symbol_config(symbol)
    open_orders = client.open_orders(symbol)
    positions = client.position_risk(symbol)
    order_params = _order_test_parameters(exchange_info, ticker, symbol)
    client.test_order(order_params)

    listen_key = client.create_listen_key()
    private_stream_verified = False
    try:
        websocket_probe(TESTNET_STREAM_HOST, f"/private/ws/{listen_key}")
        private_stream_verified = True
    finally:
        client.close_listen_key(listen_key)
    websocket_probe(TESTNET_STREAM_HOST, f"/public/ws/{symbol.lower()}@bookTicker")
    websocket_probe(TESTNET_STREAM_HOST, f"/market/ws/{symbol.lower()}@markPrice@1s")
    websocket_probe(TESTNET_WS_API_HOST, "/ws-fapi/v1")

    dual_side = position_mode.get("dualSidePosition")
    if dual_side is not False:
        raise TestnetProbeError("ACCOUNT_POSITION_MODE_NOT_ONE_WAY")
    margin_types = {
        str(item.get("marginType")) for item in symbol_config if item.get("symbol") == symbol
    }
    if margin_types and margin_types != {"CROSSED"}:
        raise TestnetProbeError("ACCOUNT_MARGIN_MODE_NOT_CROSSED")
    non_flat = [
        item
        for item in positions
        if Decimal(str(item.get("positionAmt", "0"))) != Decimal("0")
    ]
    if open_orders or non_flat:
        raise TestnetProbeError("TESTNET_ACCOUNT_NOT_CLEAN")

    symbols = exchange_info.get("symbols")
    symbol_count = len(symbols) if isinstance(symbols, list) else 0
    completed_at = datetime.now(UTC)
    return {
        "schema_version": "1.0.0",
        "probe": "BINANCE_USDS_M_FUTURES_TESTNET_SAFE_CAPABILITY",
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "completed_at": completed_at.isoformat().replace("+00:00", "Z"),
        "result": "PASS",
        "production_endpoint_requests": 0,
        "matching_engine_orders_created": 0,
        "test_order_validated": True,
        "credential_fingerprint": hashlib.sha256(api_key.encode("ascii")).hexdigest()[:16],
        "server_time_ms": server_time,
        "clock_offset_ms": offset,
        "exchange_info_sha256": hashlib.sha256(
            json.dumps(exchange_info, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "testnet_symbol_count": symbol_count,
        "symbol": symbol,
        "account_position_mode": "ONE_WAY",
        "symbol_margin_mode": next(iter(margin_types), "UNREPORTED_NO_POSITION_OR_CONFIG"),
        "account_open_order_count": 0,
        "account_non_flat_position_count": 0,
        "listen_key_lifecycle": "CREATE_PRIVATE_WS_UPGRADE_CLOSE_PASS",
        "private_stream_verified": private_stream_verified,
        "websocket_routes": {
            "public": "PASS",
            "market": "PASS",
            "private": "PASS",
            "websocket_api": "PASS",
        },
        "endpoints": {
            "rest": TESTNET_REST_BASE,
            "streams": f"wss://{TESTNET_STREAM_HOST}",
            "websocket_api": f"wss://{TESTNET_WS_API_HOST}/ws-fapi/v1",
        },
    }
