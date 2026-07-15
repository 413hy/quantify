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

TESTNET_EXPERIMENT_STRATEGY_VERSION = "TESTNET_EXPERIMENT_OF_PA_V4_4"
TESTNET_EXPERIMENT_SYMBOLS = (
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
)
TESTNET_IMPULSE_ENTRY_SYMBOLS = TESTNET_EXPERIMENT_SYMBOLS
_GROSS_TARGET_BPS_BY_SYMBOL = {
    "BTCUSDT": Decimal("20"),
    "ETHUSDT": Decimal("22"),
    "BNBUSDT": Decimal("25"),
    "SOLUSDT": Decimal("32"),
    "XRPUSDT": Decimal("25"),
}


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
    experimental_plan: TestnetExperimentalPlan | None = None
    recent_low: Decimal | None = None
    recent_high: Decimal | None = None
    range_midpoint_30m: Decimal | None = None

    @property
    def execution_ready(self) -> bool:
        """The diagnostic baseline does not produce a document-complete TradePlan."""
        return False

    @property
    def mid_price(self) -> Decimal:
        multiplier = Decimal(1) + self.order_flow.microprice_mid_bps / Decimal(10_000)
        if multiplier <= 0:
            raise ValueError("testnet baseline microprice offset is invalid")
        return self.order_flow.microprice / multiplier

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
            "mid_price": format(self.mid_price, "f"),
            "microprice": format(self.order_flow.microprice, "f"),
            "reason_codes": list(self.reason_codes),
            "validation_status": "UNVALIDATED_TESTNET_BASELINE",
            "testnet_experimental_plan": (
                None if self.experimental_plan is None else self.experimental_plan.evidence()
            ),
        }


@dataclass(frozen=True, slots=True)
class TestnetExperimentalPlan:
    symbol: str
    direction: Direction
    entry_reference: Decimal
    stop_anchor: Decimal
    target_reference: Decimal
    range_midpoint_30m: Decimal = Decimal(0)
    signal_quality_score: Decimal = Decimal(0)
    pa_alignment_count: int = 0
    directional_trade_imbalance: Decimal = Decimal(0)
    directional_book_imbalance: Decimal = Decimal(0)
    directional_microprice_bps: Decimal = Decimal(0)
    aggressive_notional: Decimal = Decimal(0)
    aggressive_notional_ratio: Decimal = Decimal(0)
    observed_spread_bps: Decimal = Decimal(0)
    signal_confirmation_rounds: int = 1
    setup_type: str = "TREND_CONFIRMATION"
    market_momentum_bps: Decimal = Decimal(0)
    market_breadth_count: int = 0
    strategy_version: str = TESTNET_EXPERIMENT_STRATEGY_VERSION

    def evidence(self) -> dict[str, str | int]:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_reference": format(self.entry_reference, "f"),
            "stop_anchor": format(self.stop_anchor, "f"),
            "target_reference": format(self.target_reference, "f"),
            "range_midpoint_30m": format(self.range_midpoint_30m, "f"),
            "signal_quality_score": format(self.signal_quality_score, "f"),
            "pa_alignment_count": self.pa_alignment_count,
            "directional_trade_imbalance": format(self.directional_trade_imbalance, "f"),
            "directional_book_imbalance": format(self.directional_book_imbalance, "f"),
            "directional_microprice_bps": format(self.directional_microprice_bps, "f"),
            "aggressive_notional": format(self.aggressive_notional, "f"),
            "aggressive_notional_ratio": format(self.aggressive_notional_ratio, "f"),
            "observed_spread_bps": format(self.observed_spread_bps, "f"),
            "signal_confirmation_rounds": self.signal_confirmation_rounds,
            "setup_type": self.setup_type,
            "market_momentum_bps": format(self.market_momentum_bps, "f"),
            "market_breadth_count": self.market_breadth_count,
            "strategy_version": self.strategy_version,
        }


@dataclass(frozen=True, slots=True)
class TestnetSignalParameters:
    """Explicit Testnet-only thresholds for a candidate signal."""

    maximum_spread_bps: Decimal = Decimal("5.00")
    minimum_trade_imbalance: Decimal = Decimal("0.25")
    minimum_book_imbalance: Decimal = Decimal("0.03")
    minimum_microprice_bps: Decimal = Decimal("0.10")
    maximum_opposing_book_imbalance: Decimal = Decimal("0.05")
    maximum_opposing_microprice_bps: Decimal = Decimal("0.25")
    minimum_pa_alignment_count: int = 1

    def __post_init__(self) -> None:
        if not Decimal(0) < self.maximum_spread_bps <= Decimal(100):
            raise ValueError("testnet maximum spread is invalid")
        for value in (
            self.minimum_trade_imbalance,
            self.minimum_book_imbalance,
            self.maximum_opposing_book_imbalance,
        ):
            if not Decimal(0) < value <= Decimal(1):
                raise ValueError("testnet imbalance threshold is invalid")
        if not Decimal(0) < self.minimum_microprice_bps <= Decimal(100):
            raise ValueError("testnet microprice threshold is invalid")
        if not Decimal(0) < self.maximum_opposing_microprice_bps <= Decimal(100):
            raise ValueError("testnet opposing microprice threshold is invalid")
        if self.minimum_pa_alignment_count not in {1, 2}:
            raise ValueError("testnet PA alignment count is invalid")


def evaluate_testnet_baseline(
    *,
    symbol: str,
    server_time_ms: int,
    one_minute_klines: list[Any],
    five_minute_klines: list[Any],
    depth: dict[str, Any],
    aggregate_trades: list[dict[str, Any]],
    signal_parameters: TestnetSignalParameters | None = None,
) -> TestnetBaselineDecision:
    """Apply the checked-in PA baseline and conservative long OF confirmation."""
    parameters = signal_parameters or TestnetSignalParameters()
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
    experimental_plan = _experimental_plan(
        symbol=symbol,
        bars_1m=bars_1m,
        pa_1m=pa_1m,
        pa_5m=pa_5m,
        order_flow=order_flow,
        bid=bids[0].price,
        ask=asks[0].price,
        spread_bps=spread_bps,
        parameters=parameters,
    )

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
        experimental_plan=experimental_plan,
        recent_low=min(bar.low for bar in bars_1m[-5:]),
        recent_high=max(bar.high for bar in bars_1m[-5:]),
        range_midpoint_30m=(
            max(bar.high for bar in bars_1m[-30:])
            + min(bar.low for bar in bars_1m[-30:])
        )
        / Decimal(2),
    )


def build_market_impulse_plan(
    decision: TestnetBaselineDecision,
    *,
    direction: Direction,
    momentum_bps: Decimal,
    breadth_count: int,
    parameters: TestnetSignalParameters,
    setup_type: str = "MARKET_BREADTH_IMPULSE_FAST",
) -> TestnetExperimentalPlan | None:
    """Build a Testnet-only fast plan when market breadth and local flow agree."""
    if direction not in {Direction.LONG, Direction.SHORT} or breadth_count < 3:
        return None
    if (
        not decision.order_flow.valid
        or decision.pa_1m.atr is None
        or decision.recent_low is None
        or decision.recent_high is None
        or decision.range_midpoint_30m is None
        or decision.spread_bps > parameters.maximum_spread_bps
    ):
        return None
    if direction is Direction.LONG and (
        decision.pa_1m.direction is Direction.SHORT
        or decision.pa_5m.direction is Direction.SHORT
    ):
        return None
    if direction is Direction.SHORT and (
        decision.pa_1m.direction is Direction.LONG
        or decision.pa_5m.direction is Direction.LONG
    ):
        return None
    sign = Decimal(1) if direction is Direction.LONG else Decimal(-1)
    directional_trade = sign * decision.order_flow.trade_imbalance
    directional_book = sign * decision.order_flow.book_imbalance
    directional_microprice = sign * decision.order_flow.microprice_mid_bps
    # Aggressive trades must lead the impulse. Either the book or microprice must
    # agree; one opposing instantaneous book measurement is not a global veto.
    if directional_trade < parameters.minimum_trade_imbalance or not (
        directional_book >= parameters.minimum_book_imbalance
        or directional_microprice >= parameters.minimum_microprice_bps
    ):
        return None
    mid = decision.mid_price
    half_spread = mid * decision.spread_bps / Decimal(20_000)
    entry = mid + half_spread if direction is Direction.LONG else mid - half_spread
    atr = decision.pa_1m.atr
    if direction is Direction.LONG:
        stop = min(decision.recent_low - atr * Decimal("0.10"), entry * Decimal("0.9940"))
        risk = entry - stop
    else:
        stop = max(decision.recent_high + atr * Decimal("0.10"), entry * Decimal("1.0060"))
        risk = stop - entry
    risk_bps = risk / entry * Decimal(10_000)
    if not Decimal(30) <= risk_bps <= Decimal(120):
        return None
    target_bps = gross_target_bps_for_symbol(decision.symbol)
    target_distance = entry * target_bps / Decimal(10_000)
    target = entry + target_distance if direction is Direction.LONG else entry - target_distance
    pa_alignment_count = sum(
        frame.direction is direction for frame in (decision.pa_1m, decision.pa_5m)
    )
    quality_score = (
        Decimal(3)
        + directional_trade
        + max(Decimal(0), directional_book)
        + max(Decimal(0), directional_microprice) / Decimal(10)
        + min(abs(momentum_bps), Decimal(20)) / Decimal(10)
        + Decimal(breadth_count - 3) / Decimal(4)
        - decision.spread_bps / Decimal(10)
    )
    return TestnetExperimentalPlan(
        symbol=decision.symbol,
        direction=direction,
        entry_reference=entry,
        stop_anchor=stop,
        target_reference=target,
        range_midpoint_30m=decision.range_midpoint_30m,
        signal_quality_score=quality_score,
        pa_alignment_count=pa_alignment_count,
        directional_trade_imbalance=directional_trade,
        directional_book_imbalance=directional_book,
        directional_microprice_bps=directional_microprice,
        aggressive_notional=decision.order_flow.aggressive_notional,
        observed_spread_bps=decision.spread_bps,
        setup_type=setup_type,
        market_momentum_bps=momentum_bps,
        market_breadth_count=breadth_count,
    )


def _experimental_plan(
    *,
    symbol: str,
    bars_1m: list[ClosedBar],
    pa_1m: PriceActionFrame,
    pa_5m: PriceActionFrame,
    order_flow: OrderFlowFrame,
    bid: Decimal,
    ask: Decimal,
    spread_bps: Decimal,
    parameters: TestnetSignalParameters,
) -> TestnetExperimentalPlan | None:
    """Create a Testnet-only plan only when PA and directional flow agree."""
    if not order_flow.valid or pa_1m.atr is None or spread_bps > parameters.maximum_spread_bps:
        return None
    long_pa_count = sum(frame.direction is Direction.LONG for frame in (pa_1m, pa_5m))
    short_pa_count = sum(frame.direction is Direction.SHORT for frame in (pa_1m, pa_5m))
    long_flow = (
        long_pa_count >= parameters.minimum_pa_alignment_count
        and order_flow.trade_imbalance >= parameters.minimum_trade_imbalance
        and (
            order_flow.book_imbalance >= parameters.minimum_book_imbalance
            or order_flow.microprice_mid_bps >= parameters.minimum_microprice_bps
        )
        and order_flow.book_imbalance >= -parameters.maximum_opposing_book_imbalance
        and order_flow.microprice_mid_bps >= -parameters.maximum_opposing_microprice_bps
        and pa_5m.direction is not Direction.SHORT
        and pa_1m.direction is not Direction.SHORT
    )
    short_flow = (
        short_pa_count >= parameters.minimum_pa_alignment_count
        and order_flow.trade_imbalance <= -parameters.minimum_trade_imbalance
        and (
            order_flow.book_imbalance <= -parameters.minimum_book_imbalance
            or order_flow.microprice_mid_bps <= -parameters.minimum_microprice_bps
        )
        and order_flow.book_imbalance <= parameters.maximum_opposing_book_imbalance
        and order_flow.microprice_mid_bps <= parameters.maximum_opposing_microprice_bps
        and pa_5m.direction is not Direction.LONG
        and pa_1m.direction is not Direction.LONG
    )
    if long_flow == short_flow:
        return None
    direction = Direction.LONG if long_flow else Direction.SHORT
    entry = ask if long_flow else bid
    atr = pa_1m.atr
    recent = bars_1m[-5:]
    if long_flow:
        stop = min(bar.low for bar in recent) - atr * Decimal("0.10")
        stop = min(stop, entry * Decimal("0.9970"))
        risk = entry - stop
    else:
        stop = max(bar.high for bar in recent) + atr * Decimal("0.10")
        stop = max(stop, entry * Decimal("1.0030"))
        risk = stop - entry
    if min(entry, stop, risk) <= 0:
        return None
    risk_bps = risk / entry * Decimal(10_000)
    if not Decimal(30) <= risk_bps <= Decimal(120):
        return None
    # V4 uses a short gross target sized for each fixed symbol's observed
    # Testnet leverage. Execution still rejects the order unless the actual
    # quantity, fee and adverse-slippage estimate leave at least 0.10 USDT net.
    target_bps = gross_target_bps_for_symbol(symbol)
    target_distance = entry * target_bps / Decimal(10_000)
    target = entry + target_distance if long_flow else entry - target_distance
    sign = Decimal(1) if long_flow else Decimal(-1)
    pa_alignment_count = long_pa_count if long_flow else short_pa_count
    pa_score = Decimal(0)
    if pa_1m.direction is direction:
        pa_score += Decimal(3) + (pa_1m.efficiency_ratio or Decimal(0))
    if pa_5m.direction is direction:
        pa_score += Decimal(2) + (pa_5m.efficiency_ratio or Decimal(0))
    directional_trade = sign * order_flow.trade_imbalance
    directional_book = sign * order_flow.book_imbalance
    directional_microprice = sign * order_flow.microprice_mid_bps
    quality_score = (
        pa_score
        + directional_trade
        + max(Decimal(0), directional_book)
        + max(Decimal(0), directional_microprice) / Decimal(10)
        - spread_bps / Decimal(10)
    )
    return TestnetExperimentalPlan(
        symbol=symbol,
        direction=direction,
        entry_reference=entry,
        stop_anchor=stop,
        target_reference=target,
        range_midpoint_30m=(
            max(bar.high for bar in bars_1m[-30:])
            + min(bar.low for bar in bars_1m[-30:])
        )
        / Decimal(2),
        signal_quality_score=quality_score,
        pa_alignment_count=pa_alignment_count,
        directional_trade_imbalance=directional_trade,
        directional_book_imbalance=directional_book,
        directional_microprice_bps=directional_microprice,
        aggressive_notional=order_flow.aggressive_notional,
        observed_spread_bps=spread_bps,
    )


def gross_target_bps_for_symbol(symbol: str) -> Decimal:
    """Return the reviewed V4 gross target for one fixed Testnet symbol."""
    try:
        return _GROSS_TARGET_BPS_BY_SYMBOL[symbol]
    except KeyError as exc:
        raise ValueError("testnet V4 symbol is outside the fixed universe") from exc


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
        return ()
    result: list[AggregateTrade] = []
    for document in documents:
        raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        try:
            trade_time = _utc_from_milliseconds(int(document["T"]))
            age = received_at - trade_time
            if not timedelta(0) <= age <= timedelta(seconds=5):
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
