from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from ai_quant.features.price_action import ClosedBar, Direction
from ai_quant.research.structural_backtest import (
    StructuralExit,
    StructuralPlan,
    simulate_structural_position,
)
from tests.market_fixtures import BASE_TIME


def bar(low: str, high: str) -> ClosedBar:
    return ClosedBar(
        symbol="SOLUSDT",
        timeframe="1m",
        open_time=BASE_TIME,
        close_time=BASE_TIME + timedelta(minutes=1),
        open=Decimal("100"),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal("100"),
        volume=Decimal(1),
    )


def plan() -> StructuralPlan:
    return StructuralPlan(
        "SOLUSDT",
        Direction.LONG,
        120,
        Decimal("100"),
        Decimal("99"),
        Decimal("101"),
    )


def test_same_bar_collision_uses_stop_first_and_charges_round_trip_fee() -> None:
    result = simulate_structural_position(
        plan(),
        (bar("98", "102"),),
        taker_fee_rate=Decimal("0.0004"),
        exit_slippage_bps=Decimal(1),
        notional=Decimal(10),
    )

    assert result.exit is StructuralExit.STOP
    assert result.net_bps == Decimal("-108.99")
    assert result.net_pnl == Decimal("-0.10899")


def test_elapsed_bars_never_close_a_position_without_strategy_trigger() -> None:
    result = simulate_structural_position(
        plan(),
        (bar("99.5", "100.5"),) * 10_000,
        taker_fee_rate=Decimal("0.0004"),
        exit_slippage_bps=Decimal(1),
        notional=Decimal(10),
    )

    assert result.exit is StructuralExit.OPEN
    assert result.exit_price is None
    assert result.net_pnl is None


def test_target_exit_reports_net_of_fee_and_slippage() -> None:
    result = simulate_structural_position(
        plan(),
        (bar("99.5", "101.5"),),
        taker_fee_rate=Decimal("0.0004"),
        exit_slippage_bps=Decimal(1),
        notional=Decimal(10),
    )

    assert result.exit is StructuralExit.TARGET
    assert result.net_bps == Decimal("90.99")
    assert result.net_pnl == Decimal("0.09099")
