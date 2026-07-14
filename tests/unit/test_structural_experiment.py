from decimal import Decimal

import pytest

from ai_quant.binance_egress.structural_experiment import (
    plan_market_quantity,
    quantize_protection,
    risk_adjusted_margin_budget,
)
from ai_quant.binance_egress.testnet_probe import TestnetProbeError as ProbeError
from ai_quant.features.price_action import Direction
from ai_quant.strategy.testnet_baseline import TestnetExperimentalPlan as ExperimentalPlan


def _exchange_info() -> dict[str, object]:
    return {
        "symbols": [
            {
                "symbol": "SOLUSDT",
                "status": "TRADING",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                    {
                        "filterType": "MARKET_LOT_SIZE",
                        "stepSize": "0.01",
                        "minQty": "0.01",
                    },
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                ],
            }
        ]
    }


def test_market_quantity_stays_inside_one_usdt_margin_budget() -> None:
    quantity = plan_market_quantity(
        _exchange_info(),
        symbol="SOLUSDT",
        reference_price=Decimal("150"),
        margin_budget=Decimal("1"),
        leverage=10,
    )

    assert quantity == Decimal("0.06")
    assert quantity * Decimal("150") / Decimal(10) <= Decimal("1")


def test_market_quantity_rejects_exchange_minimum_above_budget() -> None:
    with pytest.raises(
        ProbeError,
        match="EXPERIMENT_EXCHANGE_MINIMUM_EXCEEDS_MARGIN_BUDGET",
    ):
        plan_market_quantity(
            _exchange_info(),
            symbol="SOLUSDT",
            reference_price=Decimal("1000"),
            margin_budget=Decimal("1"),
            leverage=10,
        )


def test_long_protection_keeps_structural_stop_and_small_target() -> None:
    plan = ExperimentalPlan(
        symbol="SOLUSDT",
        direction=Direction.LONG,
        entry_reference=Decimal("100"),
        stop_anchor=Decimal("99.50"),
        target_reference=Decimal("100.25"),
    )

    stop, target = quantize_protection(
        plan,
        actual_entry=Decimal("100.03"),
        tick_size=Decimal("0.01"),
    )

    assert stop == Decimal("99.50")
    assert target == Decimal("100.29")
    assert stop < Decimal("100.03") < target


def test_short_protection_rounds_away_from_entry() -> None:
    plan = ExperimentalPlan(
        symbol="SOLUSDT",
        direction=Direction.SHORT,
        entry_reference=Decimal("100"),
        stop_anchor=Decimal("100.501"),
        target_reference=Decimal("99.75"),
    )

    stop, target = quantize_protection(
        plan,
        actual_entry=Decimal("99.98"),
        tick_size=Decimal("0.01"),
    )

    assert stop == Decimal("100.51")
    assert target == Decimal("99.73")
    assert target < Decimal("99.98") < stop


def test_margin_expands_but_stop_loss_budget_caps_effective_size() -> None:
    plan = ExperimentalPlan(
        symbol="SOLUSDT",
        direction=Direction.LONG,
        entry_reference=Decimal("100"),
        stop_anchor=Decimal("99.70"),
        target_reference=Decimal("100.20"),
    )

    margin = risk_adjusted_margin_budget(
        plan,
        margin_ceiling=Decimal("10"),
        leverage=10,
        maximum_net_loss=Decimal("0.35"),
        taker_fee_rate=Decimal("0.0004"),
    )

    assert margin == Decimal("8.75")
    assert margin * Decimal(10) * Decimal("0.004") == Decimal("0.35000")
