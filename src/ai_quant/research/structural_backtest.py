"""Conservative structural-exit replay for the unvalidated T1 research proxy."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from ai_quant.features.price_action import (
    ClosedBar,
    Direction,
    PriceActionFrame,
    SwingKind,
    analyze_price_action,
    confirmed_swings,
    simple_atr,
)


class StructuralExit(StrEnum):
    STOP = "STOP"
    TARGET = "TARGET"
    OPEN = "OPEN"


@dataclass(frozen=True, slots=True)
class HistoricalKline:
    bar: ClosedBar
    trade_imbalance: Decimal

    def __post_init__(self) -> None:
        if not Decimal(-1) <= self.trade_imbalance <= Decimal(1):
            raise ValueError("trade imbalance must be within [-1,1]")


@dataclass(frozen=True, slots=True)
class StructuralPlan:
    symbol: str
    direction: Direction
    signal_index: int
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal


@dataclass(frozen=True, slots=True)
class StructuralTrade:
    plan: StructuralPlan
    exit: StructuralExit
    exit_index: int | None
    exit_price: Decimal | None
    net_bps: Decimal | None
    net_pnl: Decimal | None


@dataclass(frozen=True, slots=True)
class StructuralBacktestResult:
    trades: tuple[StructuralTrade, ...]
    closed_trades: int
    winning_trades: int
    net_bps: Decimal
    net_pnl: Decimal


def simulate_structural_position(
    plan: StructuralPlan,
    bars: tuple[ClosedBar, ...],
    *,
    taker_fee_rate: Decimal,
    exit_slippage_bps: Decimal,
    notional: Decimal,
) -> StructuralTrade:
    """Replay native stop/target conservatively; elapsed time never exits the position."""
    if min(taker_fee_rate, exit_slippage_bps) < 0 or notional <= 0:
        raise ValueError("costs must be non-negative and notional must be positive")
    if plan.direction not in {Direction.LONG, Direction.SHORT}:
        raise ValueError("structural plan direction is invalid")
    long = plan.direction is Direction.LONG
    valid = (
        plan.stop_price < plan.entry_price < plan.target_price
        if long
        else plan.target_price < plan.entry_price < plan.stop_price
    )
    if not valid:
        raise ValueError("structural plan prices are invalid")
    slippage = exit_slippage_bps / Decimal(10_000)
    for offset, bar in enumerate(bars):
        # Without tick data, a same-bar stop/target collision uses the conservative stop-first path.
        stop_reached = bar.low <= plan.stop_price if long else bar.high >= plan.stop_price
        target_reached = bar.high >= plan.target_price if long else bar.low <= plan.target_price
        if stop_reached:
            exit_price = plan.stop_price * (
                Decimal(1) - slippage if long else Decimal(1) + slippage
            )
            reason = StructuralExit.STOP
        elif target_reached:
            exit_price = plan.target_price * (
                Decimal(1) - slippage if long else Decimal(1) + slippage
            )
            reason = StructuralExit.TARGET
        else:
            continue
        signed_return = (exit_price - plan.entry_price) / plan.entry_price
        if not long:
            signed_return = -signed_return
        net_bps = signed_return * Decimal(10_000) - taker_fee_rate * Decimal(20_000)
        return StructuralTrade(
            plan=plan,
            exit=reason,
            exit_index=plan.signal_index + 1 + offset,
            exit_price=exit_price,
            net_bps=net_bps,
            net_pnl=notional * net_bps / Decimal(10_000),
        )
    return StructuralTrade(plan, StructuralExit.OPEN, None, None, None, None)


def run_t1_proxy_backtest(
    one_minute: tuple[HistoricalKline, ...],
    five_minute: tuple[ClosedBar, ...],
    *,
    taker_fee_rate: Decimal,
    slippage_bps: Decimal = Decimal(1),
    notional: Decimal = Decimal(10),
) -> StructuralBacktestResult:
    """Replay a documented T1 proxy without claiming a full PA/OF implementation."""
    if len(one_minute) < 122 or len(five_minute) < 48:
        raise ValueError("T1 proxy backtest has insufficient history")
    trades: list[StructuralTrade] = []
    next_available_index = 120
    bars_1m = tuple(item.bar for item in one_minute)
    for index in range(120, len(one_minute) - 1):
        if index < next_available_index:
            continue
        window = list(bars_1m[index - 119 : index + 1])
        available_5m = [bar for bar in five_minute if bar.close_time <= window[-1].close_time]
        if len(available_5m) < 48:
            continue
        pa_1m = _price_action(window, five_minute=False)
        pa_5m = _price_action(available_5m[-120:], five_minute=True)
        direction = pa_5m.direction
        if direction is Direction.NEUTRAL or pa_1m.direction not in {
            direction,
            Direction.NEUTRAL,
        }:
            continue
        imbalance = one_minute[index].trade_imbalance
        if direction is Direction.LONG and imbalance < Decimal("0.10"):
            continue
        if direction is Direction.SHORT and imbalance > Decimal("-0.10"):
            continue
        plan = _t1_plan(window, direction, index, slippage_bps, taker_fee_rate)
        if plan is None:
            continue
        trade = simulate_structural_position(
            plan,
            bars_1m[index + 1 :],
            taker_fee_rate=taker_fee_rate,
            exit_slippage_bps=slippage_bps,
            notional=notional,
        )
        trades.append(trade)
        if trade.exit_index is None:
            break
        next_available_index = trade.exit_index + 1
    closed = [trade for trade in trades if trade.net_bps is not None]
    return StructuralBacktestResult(
        trades=tuple(trades),
        closed_trades=len(closed),
        winning_trades=sum(trade.net_bps > 0 for trade in closed if trade.net_bps is not None),
        net_bps=sum((trade.net_bps for trade in closed if trade.net_bps is not None), Decimal(0)),
        net_pnl=sum((trade.net_pnl for trade in closed if trade.net_pnl is not None), Decimal(0)),
    )


def _t1_plan(
    window: list[ClosedBar],
    direction: Direction,
    signal_index: int,
    slippage_bps: Decimal,
    taker_fee_rate: Decimal,
) -> StructuralPlan | None:
    bar = window[-1]
    body = abs(bar.close - bar.open)
    if body <= 0:
        return None
    atr_values = simple_atr(window, 14)
    swings = confirmed_swings(window, atr_values, left=2, right=2)
    long = direction is Direction.LONG
    if long:
        wick = min(bar.open, bar.close) - bar.low
        rejected = wick / body >= Decimal("0.50") and bar.close > bar.open
        stops = [swing.price for swing in swings if swing.kind is SwingKind.LOW]
        targets = [
            swing.price
            for swing in swings
            if swing.kind is SwingKind.HIGH and swing.price > bar.close
        ]
    else:
        wick = bar.high - max(bar.open, bar.close)
        rejected = wick / body >= Decimal("0.50") and bar.close < bar.open
        stops = [swing.price for swing in swings if swing.kind is SwingKind.HIGH]
        targets = [
            swing.price
            for swing in swings
            if swing.kind is SwingKind.LOW and swing.price < bar.close
        ]
    if not rejected or not stops or not targets:
        return None
    entry_slippage = slippage_bps / Decimal(10_000)
    entry = bar.close * (Decimal(1) + entry_slippage if long else Decimal(1) - entry_slippage)
    stop = stops[-1]
    target = targets[-1]
    valid = stop < entry < target if long else target < entry < stop
    if not valid:
        return None
    risk = abs(entry - stop)
    reward = abs(target - entry)
    if risk <= 0 or reward / risk < Decimal(1):
        return None
    gross_target_bps = reward / entry * Decimal(10_000)
    round_trip_cost_bps = taker_fee_rate * Decimal(20_000) + slippage_bps * Decimal(2)
    if gross_target_bps < round_trip_cost_bps * Decimal(2):
        return None
    return StructuralPlan(bar.symbol, direction, signal_index, entry, stop, target)


def _price_action(bars: list[ClosedBar], *, five_minute: bool) -> PriceActionFrame:
    return analyze_price_action(
        bars,
        atr_period=14,
        efficiency_lookback=12 if five_minute else 20,
        efficiency_threshold=Decimal("0.35") if five_minute else Decimal("0.30"),
        slope_lookback=6 if five_minute else 10,
        slope_threshold_atr=Decimal("0.05"),
        swing_left=2,
        swing_right=2,
        required_pairs=2,
        equal_tolerance_atr=Decimal("0.10"),
    )
