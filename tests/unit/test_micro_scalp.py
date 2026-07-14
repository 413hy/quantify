from __future__ import annotations

from decimal import Decimal

import pytest

from ai_quant.risk.micro_scalp import plan_long_micro_scalp, plan_long_micro_scalp_for_quantity


def test_micro_scalp_caps_margin_and_covers_target_and_stop_after_costs() -> None:
    plan = plan_long_micro_scalp(
        entry_assumption=Decimal("75"),
        margin_budget=Decimal("1"),
        initial_leverage=Decimal("10"),
        step_size=Decimal("0.01"),
        minimum_quantity=Decimal("0.01"),
        minimum_notional=Decimal("5"),
        tick_size=Decimal("0.001"),
        taker_fee_rate=Decimal("0.0004"),
        target_net_profit=Decimal("0.1"),
        maximum_net_loss=Decimal("0.1"),
        adverse_exit_slippage_bps=Decimal("2"),
    )

    assert plan.quantity == Decimal("0.13")
    assert plan.initial_margin <= Decimal("1")
    assert plan.stop_trigger < plan.entry_assumption < plan.target_trigger
    assert plan.estimated_target_net_profit >= Decimal("0.1")
    assert plan.estimated_stop_net_profit >= Decimal("-0.1")


def test_micro_scalp_rejects_budget_or_exchange_minimum_breach() -> None:
    common = {
        "entry_assumption": Decimal("75"),
        "initial_leverage": Decimal("10"),
        "step_size": Decimal("0.01"),
        "minimum_quantity": Decimal("0.01"),
        "minimum_notional": Decimal("5"),
        "tick_size": Decimal("0.001"),
        "taker_fee_rate": Decimal("0.0004"),
        "target_net_profit": Decimal("0.1"),
        "maximum_net_loss": Decimal("0.1"),
        "adverse_exit_slippage_bps": Decimal("2"),
    }
    with pytest.raises(ValueError, match="exceeds 1 USDT"):
        plan_long_micro_scalp(margin_budget=Decimal("1.01"), **common)

    with pytest.raises(ValueError, match="exchange minimums"):
        plan_long_micro_scalp(
            margin_budget=Decimal("0.1"),
            **common,
        )


def test_actual_fill_replan_never_accepts_more_than_budget() -> None:
    with pytest.raises(ValueError, match="actual fill exceeds"):
        plan_long_micro_scalp_for_quantity(
            entry_assumption=Decimal("80"),
            quantity=Decimal("0.13"),
            margin_budget=Decimal("1"),
            initial_leverage=Decimal("10"),
            minimum_quantity=Decimal("0.01"),
            minimum_notional=Decimal("5"),
            tick_size=Decimal("0.001"),
            taker_fee_rate=Decimal("0.0004"),
            target_net_profit=Decimal("0.1"),
            maximum_net_loss=Decimal("0.1"),
            adverse_exit_slippage_bps=Decimal("2"),
        )
