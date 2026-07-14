from __future__ import annotations

import json
import urllib.parse
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ai_quant.binance_egress.testnet_probe import HttpResult, run_safe_testnet_probe


def _result(document: Any) -> HttpResult:
    return HttpResult(200, {}, json.dumps(document).encode())


def test_safe_probe_never_uses_production_and_redacts_ephemeral_values(tmp_path: Path) -> None:
    key = tmp_path / "key"
    secret = tmp_path / "secret"
    key.write_text("test-key", encoding="ascii")
    secret.write_text("test-secret", encoding="ascii")
    key.chmod(0o400)
    secret.chmod(0o400)
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    seen_urls: list[str] = []
    websocket_paths: list[tuple[str, str]] = []

    def transport(
        method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> HttpResult:
        del method, headers, body
        seen_urls.append(url)
        parsed = urllib.parse.urlparse(url)
        if parsed.path == "/fapi/v1/time":
            return _result({"serverTime": 1_800_000_000_000})
        if parsed.path == "/fapi/v1/exchangeInfo":
            return _result(
                {
                    "symbols": [
                        {
                            "symbol": "BTCUSDT",
                            "status": "TRADING",
                            "filters": [
                                {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                                {
                                    "filterType": "LOT_SIZE",
                                    "minQty": "0.001",
                                    "stepSize": "0.001",
                                },
                                {"filterType": "MIN_NOTIONAL", "notional": "5"},
                            ],
                        }
                    ]
                }
            )
        if parsed.path == "/fapi/v1/ticker/bookTicker":
            return _result({"bidPrice": "100000.0", "askPrice": "100000.1"})
        if parsed.path == "/fapi/v1/positionSide/dual":
            return _result({"dualSidePosition": False})
        if parsed.path == "/fapi/v1/symbolConfig":
            return _result([{"symbol": "BTCUSDT", "marginType": "CROSSED"}])
        if parsed.path in {"/fapi/v1/openOrders", "/fapi/v3/positionRisk"}:
            return _result([])
        if parsed.path == "/fapi/v1/order/test":
            return _result({})
        if parsed.path == "/fapi/v1/listenKey":
            return _result({"listenKey": "private-listen-key-value"})
        raise AssertionError(parsed.path)

    evidence = run_safe_testnet_probe(
        api_key_file=key,
        api_secret_file=secret,
        repository_root=repository_root,
        transport=transport,
        websocket_probe=lambda host, path: websocket_paths.append((host, path)),
    )

    assert evidence["result"] == "PASS"
    assert evidence["matching_engine_orders_created"] == 0
    assert all(url.startswith("https://demo-fapi.binance.com/") for url in seen_urls)
    assert "test-secret" not in json.dumps(evidence)
    assert "private-listen-key-value" not in json.dumps(evidence)
    assert any(path.startswith("/private/ws/") for _, path in websocket_paths)
