"""Unvalidated Testnet-only PA/OF baseline used to collect forward observations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from ai_quant.features.order_flow import BookLevel, OrderFlowFrame, calculate_order_flow
from ai_quant.features.price_action import (
    ClosedBar,
    Direction,
    PriceActionFrame,
    analyze_price_action,
)
from ai_quant.market_data.models import AggregateTrade


@dataclass(frozen=True, slots=True)
class TestnetBaselineDecision:
    eligible: bool
    observed_at: datetime
    symbol: str
    direction: Direction
    pa_1m: PriceActionFrame
    pa_5m: PriceActionFrame
    order_flow: OrderFlowFrame
    spread_bps: Decimal
    reason_codes: tuple[str, ...]

    @property
    def execution_ready(self) -> bool:
        """The diagnostic baseline does not produce a document-complete TradePlan."""
        return False

    def evidence(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "strategy": "TESTNET_UNVALIDATED_PA_OF_BASELINE_V1",
            "eligible": self.eligible,
            "execution_ready": self.execution_ready,
            "entry_verdict": "REJECT",
            "execution_block_reason_codes": [
                "PA_SETUP_STATE_INCOMPLETE",
                "NET_EDGE_EVIDENCE_INCOMPLETE",
                "STRATEGY_EXIT_PLAN_INCOMPLETE",
            ],
            "observed_at": self.observed_at.isoformat().replace("+00:00", "Z"),
            "symbol": self.symbol,
            "direction": self.direction,
            "pa_1m": {
                "regime": self.pa_1m.regime,
                "structure": self.pa_1m.structure,
                "direction": self.pa_1m.direction,
                "atr": _decimal_or_none(self.pa_1m.atr),
                "efficiency_ratio": _decimal_or_none(self.pa_1m.efficiency_ratio),
            },
            "pa_5m": {
                "regime": self.pa_5m.regime,
                "structure": self.pa_5m.structure,
                "direction": self.pa_5m.direction,
                "atr": _decimal_or_none(self.pa_5m.atr),
                "efficiency_ratio": _decimal_or_none(self.pa_5m.efficiency_ratio),
            },
            "order_flow": {
                "book_imbalance": format(self.order_flow.book_imbalance, "f"),
                "microprice_mid_bps": format(self.order_flow.microprice_mid_bps, "f"),
                "trade_imbalance": format(self.order_flow.trade_imbalance, "f"),
                "aggressive_notional": format(self.order_flow.aggressive_notional, "f"),
                "cvd_notional": format(self.order_flow.cvd_notional, "f"),
            },
            "spread_bps": format(self.spread_bps, "f"),
            "reason_codes": list(self.reason_codes),
            "validation_status": "UNVALIDATED_TESTNET_BASELINE",
        }


def evaluate_testnet_baseline(
    *,
    symbol: str,
    server_time_ms: int,
    one_minute_klines: list[Any],
    five_minute_klines: list[Any],
    depth: dict[str, Any],
    aggregate_trades: list[dict[str, Any]],
) -> TestnetBaselineDecision:
    """Apply the checked-in PA baseline and conservative long OF confirmation."""
    observed_at = _utc_from_milliseconds(server_time_ms)
    bars_1m = _closed_bars(symbol, "1m", one_minute_klines, server_time_ms)
    bars_5m = _closed_bars(symbol, "5m", five_minute_klines, server_time_ms)
    if len(bars_1m) < 60 or len(bars_5m) < 48:
        raise ValueError("testnet baseline has insufficient closed bars")
    pa_1m = analyze_price_action(
        bars_1m,
        atr_period=14,
        efficiency_lookback=20,
        efficiency_threshold=Decimal("0.30"),
        slope_lookback=10,
        slope_threshold_atr=Decimal("0.05"),
        swing_left=2,
        swing_right=2,
        required_pairs=2,
        equal_tolerance_atr=Decimal("0.10"),
    )
    pa_5m = analyze_price_action(
        bars_5m,
        atr_period=14,
        efficiency_lookback=12,
        efficiency_threshold=Decimal("0.35"),
        slope_lookback=6,
        slope_threshold_atr=Decimal("0.05"),
        swing_left=2,
        swing_right=2,
        required_pairs=2,
        equal_tolerance_atr=Decimal("0.10"),
    )
    bids, asks = _book_levels(depth)
    trades = _trades(symbol, aggregate_trades, observed_at)
    order_flow = calculate_order_flow(bids, asks, trades, depth_levels=20)
    mid = (bids[0].price + asks[0].price) / Decimal(2)
    spread_bps = (asks[0].price - bids[0].price) / mid * Decimal(10_000)

    reasons: list[str] = []
    if pa_1m.direction is not Direction.LONG:
        reasons.append("PA_1M_NOT_LONG")
    if pa_5m.direction is not Direction.LONG:
        reasons.append("PA_5M_NOT_LONG")
    if not order_flow.valid:
        reasons.extend(order_flow.reason_codes)
    if order_flow.book_imbalance < Decimal("0.15"):
        reasons.append("OF_BOOK_IMBALANCE_INSUFFICIENT")
    if order_flow.microprice_mid_bps < Decimal("0.50"):
        reasons.append("OF_MICROPRICE_CONFIRMATION_INSUFFICIENT")
    if order_flow.trade_imbalance < Decimal("0.20"):
        reasons.append("OF_TRADE_IMBALANCE_INSUFFICIENT")
    if order_flow.cvd_notional <= 0:
        reasons.append("OF_CVD_NOT_POSITIVE")
    # This live Testnet baseline uses the documented 10 bps universe ceiling as a
    # current-snapshot proxy. Formal eligibility still requires the 15-minute median.
    if spread_bps > Decimal("10.00"):
        reasons.append("SPREAD_TOO_WIDE")
    return TestnetBaselineDecision(
        eligible=not reasons,
        observed_at=observed_at,
        symbol=symbol,
        direction=Direction.LONG if not reasons else Direction.NEUTRAL,
        pa_1m=pa_1m,
        pa_5m=pa_5m,
        order_flow=order_flow,
        spread_bps=spread_bps,
        reason_codes=tuple(dict.fromkeys(reasons)),
    )


def _closed_bars(
    symbol: str, timeframe: str, documents: list[Any], server_time_ms: int
) -> list[ClosedBar]:
    bars: list[ClosedBar] = []
    for document in documents:
        if not isinstance(document, list) or len(document) < 7:
            raise ValueError("testnet kline response is invalid")
        try:
            open_ms = int(document[0])
            close_ms = int(document[6])
            if close_ms > server_time_ms:
                continue
            bars.append(
                ClosedBar(
                    symbol=symbol,
                    timeframe=timeframe,
                    open_time=_utc_from_milliseconds(open_ms),
                    close_time=_utc_from_milliseconds(close_ms + 1),
                    open=Decimal(str(document[1])),
                    high=Decimal(str(document[2])),
                    low=Decimal(str(document[3])),
                    close=Decimal(str(document[4])),
                    volume=Decimal(str(document[5])),
                )
            )
        except (ArithmeticError, TypeError, ValueError) as exc:
            raise ValueError("testnet kline response is invalid") from exc
    return bars


def _book_levels(
    document: dict[str, Any],
) -> tuple[tuple[BookLevel, ...], tuple[BookLevel, ...]]:
    try:
        bids = tuple(BookLevel(Decimal(str(p)), Decimal(str(q))) for p, q in document["bids"])
        asks = tuple(BookLevel(Decimal(str(p)), Decimal(str(q))) for p, q in document["asks"])
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise ValueError("testnet depth response is invalid") from exc
    if len(bids) < 20 or len(asks) < 20:
        raise ValueError("testnet depth response has insufficient levels")
    return bids, asks


def _trades(
    symbol: str, documents: list[dict[str, Any]], received_at: datetime
) -> tuple[AggregateTrade, ...]:
    if not documents:
        raise ValueError("testnet aggregate trade response is empty")
    result: list[AggregateTrade] = []
    for document in documents:
        raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        try:
            trade_time = _utc_from_milliseconds(int(document["T"]))
            age = received_at - trade_time
            if not timedelta(0) <= age <= timedelta(milliseconds=500):
                continue
            result.append(
                AggregateTrade(
                    environment="testnet",
                    symbol=symbol,
                    connection_id="testnet-baseline-rest",
                    event_time=trade_time,
                    received_at=received_at,
                    aggregate_trade_id=int(document["a"]),
                    first_trade_id=int(document["f"]),
                    last_trade_id=int(document["l"]),
                    price=str(document["p"]),
                    quantity=str(document["q"]),
                    notional_quantity=str(document.get("nq", document["q"])),
                    settlement_time=trade_time,
                    buyer_is_maker=bool(document["m"]),
                    raw_hash=hashlib.sha256(raw).hexdigest(),
                    route_role="TESTNET_BASELINE_REST",
                    route_base_hash=hashlib.sha256(b"demo-fapi.binance.com").hexdigest(),
                )
            )
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            raise ValueError("testnet aggregate trade response is invalid") from exc
    return tuple(result)


def _utc_from_milliseconds(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1_000, tz=UTC)


def _decimal_or_none(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")
