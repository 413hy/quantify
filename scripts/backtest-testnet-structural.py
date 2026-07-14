#!/usr/bin/env python3
"""Review live observations and replay the T1 structural proxy on Testnet klines."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from ai_quant.binance_egress.testnet_probe import BinanceTestnetClient, _credential
from ai_quant.features.price_action import ClosedBar
from ai_quant.research.structural_backtest import HistoricalKline, run_t1_proxy_backtest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key-file", required=True, type=Path)
    parser.add_argument("--api-secret-file", required=True, type=Path)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--observations", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--symbols", default="SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,ADAUSDT")
    arguments = parser.parse_args()
    symbols = tuple(item.strip() for item in arguments.symbols.split(",") if item.strip())
    if not symbols or len(set(symbols)) != len(symbols):
        raise ValueError("symbols must be a non-empty unique list")
    client = BinanceTestnetClient(
        _credential(arguments.api_key_file, arguments.repository_root),
        _credential(arguments.api_secret_file, arguments.repository_root),
    )
    server_time_ms, _ = client.synchronize_time()
    symbol_results: dict[str, object] = {}
    all_net_pnl = Decimal(0)
    all_closed = 0
    all_wins = 0
    all_open = 0
    window_start: datetime | None = None
    window_end: datetime | None = None
    for symbol in symbols:
        one_documents = client.klines(symbol, "1m", limit=1500)
        five_documents = client.klines(symbol, "5m", limit=500)
        one_minute = tuple(
            item
            for item in (_historical_kline(symbol, document) for document in one_documents)
            if int(item.bar.close_time.timestamp() * 1_000) <= server_time_ms
        )
        five_minute = tuple(
            item
            for item in (_closed_bar(symbol, "5m", document) for document in five_documents)
            if int(item.close_time.timestamp() * 1_000) <= server_time_ms
        )
        commission = client.commission_rate(symbol)
        taker_fee = Decimal(str(commission["takerCommissionRate"]))
        result = run_t1_proxy_backtest(
            one_minute,
            five_minute,
            taker_fee_rate=taker_fee,
            slippage_bps=Decimal(1),
            notional=Decimal(10),
        )
        starts_at = one_minute[0].bar.open_time
        ends_at = one_minute[-1].bar.close_time
        window_start = starts_at if window_start is None else min(window_start, starts_at)
        window_end = ends_at if window_end is None else max(window_end, ends_at)
        all_net_pnl += result.net_pnl
        all_closed += result.closed_trades
        all_wins += result.winning_trades
        open_count = sum(trade.exit == "OPEN" for trade in result.trades)
        all_open += open_count
        symbol_results[symbol] = {
            "one_minute_bars": len(one_minute),
            "five_minute_bars": len(five_minute),
            "taker_fee_rate": format(taker_fee, "f"),
            "closed_trades": result.closed_trades,
            "winning_trades": result.winning_trades,
            "open_positions_at_dataset_end": open_count,
            "net_bps": format(result.net_bps, "f"),
            "net_pnl_at_10_usdt_notional": format(result.net_pnl, "f"),
            "trades": [
                {
                    "direction": trade.plan.direction,
                    "signal_index": trade.plan.signal_index,
                    "entry_price": format(trade.plan.entry_price, "f"),
                    "stop_price": format(trade.plan.stop_price, "f"),
                    "target_price": format(trade.plan.target_price, "f"),
                    "exit": trade.exit,
                    "exit_index": trade.exit_index,
                    "net_bps": None if trade.net_bps is None else format(trade.net_bps, "f"),
                    "net_pnl": None if trade.net_pnl is None else format(trade.net_pnl, "f"),
                }
                for trade in result.trades
            ],
        }
    forward = _forward_observation_summary(arguments.observations)
    win_rate = Decimal(0) if all_closed == 0 else Decimal(all_wins) / Decimal(all_closed)
    reasons: list[str] = []
    if int(forward["eligible_observations"]) == 0:
        reasons.append("FORWARD_SIGNAL_COUNT_ZERO")
    if all_closed < 30:
        reasons.append("BACKTEST_SAMPLE_INSUFFICIENT")
    if all_net_pnl <= 0:
        reasons.append("BACKTEST_NET_PNL_NOT_POSITIVE")
    if win_rate < Decimal("0.55"):
        reasons.append("BACKTEST_WIN_RATE_BELOW_RESEARCH_FLOOR")
    document = {
        "schema_version": "1.0.0",
        "report": "TESTNET_STRUCTURAL_STRATEGY_REVIEW",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "environment": "testnet_public_history_and_forward_observations",
        "production_endpoint_requests": 0,
        "strategy": "UNVALIDATED_T1_STRUCTURAL_PROXY",
        "execution_semantics": {
            "entry": "signal_close_plus_1bps_adverse_slippage",
            "exit": "structure_stop_or_structure_target_only",
            "elapsed_time_exit": False,
            "same_bar_collision": "STOP_FIRST",
            "round_trip_commission": "actual_testnet_taker_rate_per_symbol",
            "notional_per_trade": "10 USDT",
        },
        "limitations": [
            "Historical klines do not contain the required causal L2 book and normal-quantity OF.",
            "Kline taker-buy quote imbalance is a research proxy and cannot qualify production.",
            "The available 1500 one-minute bars are too short for profitability approval.",
        ],
        "window_start": None if window_start is None else window_start.isoformat(),
        "window_end": None if window_end is None else window_end.isoformat(),
        "forward_observations": forward,
        "backtest": {
            "symbols": symbol_results,
            "closed_trades": all_closed,
            "winning_trades": all_wins,
            "open_positions_at_dataset_end": all_open,
            "win_rate": format(win_rate, "f"),
            "net_pnl_at_10_usdt_notional": format(all_net_pnl, "f"),
        },
        "verdict": "PASS_RESEARCH_GATE" if not reasons else "FAIL_RESEARCH_GATE",
        "reason_codes": reasons,
    }
    _atomic_write(arguments.output, document)
    print(json.dumps(document, sort_keys=True, separators=(",", ":")))
    return 0


def _historical_kline(symbol: str, document: Any) -> HistoricalKline:
    bar = _closed_bar(symbol, "1m", document)
    if not isinstance(document, list) or len(document) < 11:
        raise ValueError("historical kline is invalid")
    quote_volume = Decimal(str(document[7]))
    taker_buy_quote = Decimal(str(document[10]))
    imbalance = (
        Decimal(0)
        if quote_volume <= 0
        else (taker_buy_quote * Decimal(2) - quote_volume) / quote_volume
    )
    return HistoricalKline(bar, imbalance)


def _closed_bar(symbol: str, timeframe: str, document: Any) -> ClosedBar:
    if not isinstance(document, list) or len(document) < 7:
        raise ValueError("historical kline is invalid")
    return ClosedBar(
        symbol=symbol,
        timeframe=timeframe,
        open_time=datetime.fromtimestamp(int(document[0]) / 1_000, tz=UTC),
        close_time=datetime.fromtimestamp((int(document[6]) + 1) / 1_000, tz=UTC),
        open=Decimal(str(document[1])),
        high=Decimal(str(document[2])),
        low=Decimal(str(document[3])),
        close=Decimal(str(document[4])),
        volume=Decimal(str(document[5])),
    )


def _forward_observation_summary(path: Path) -> dict[str, object]:
    records: list[dict[str, Any]] = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            document = json.loads(line)
            if isinstance(document, dict) and document.get("record_type") == "SIGNAL_OBSERVATION":
                records.append(document)
    reasons = Counter(str(code) for item in records for code in item.get("reason_codes", []))
    return {
        "observation_count": len(records),
        "eligible_observations": sum(bool(item.get("eligible")) for item in records),
        "execution_ready_observations": sum(
            bool(item.get("execution_ready")) for item in records
        ),
        "observations_with_aggressive_trades": sum(
            Decimal(str(item["order_flow"]["aggressive_notional"])) > 0 for item in records
        ),
        "both_timeframes_long": sum(
            item["pa_1m"]["direction"] == "LONG"
            and item["pa_5m"]["direction"] == "LONG"
            for item in records
        ),
        "top_reason_codes": dict(reasons.most_common(10)),
    }


def _atomic_write(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
