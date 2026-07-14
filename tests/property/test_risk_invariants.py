from __future__ import annotations

from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from ai_quant.risk.sizing import RiskSizingInput, size_entry


@given(
    equity=st.integers(min_value=1_000, max_value=10_000_000),
    distance=st.integers(min_value=1, max_value=1_000),
    multiplier=st.sampled_from([Decimal("0.1"), Decimal("0.5"), Decimal("1")]),
)
def test_approved_quantity_never_exceeds_any_risk_budget(
    equity: int, distance: int, multiplier: Decimal
) -> None:
    request = RiskSizingInput(
        equity=Decimal(equity),
        entry_assumption=Decimal("1000"),
        stop_trigger=Decimal(1000 - min(distance, 999)),
        entry_slippage_per_unit=Decimal("0.1"),
        emergency_exit_slippage_per_unit=Decimal("0.1"),
        entry_fee_per_unit=Decimal("0.1"),
        exit_fee_per_unit=Decimal("0.1"),
        funding_buffer_per_unit=Decimal("0.1"),
        reserved_episode_risk=Decimal(0),
        reserved_all_risk=Decimal(0),
        reserved_cluster_risk=Decimal(0),
        current_daily_loss=Decimal(0),
        current_drawdown=Decimal(0),
        current_gross_notional=Decimal(0),
        current_positions=0,
        step_size=Decimal("0.001"),
        minimum_quantity=Decimal("0.001"),
        minimum_notional=Decimal("1"),
        maximum_executable_quantity=Decimal("1000000"),
    )
    decision = size_entry(request, risk_multiplier=multiplier)
    if decision.approved:
        assert decision.reserved_risk <= decision.available_risk
        assert decision.quantity % request.step_size == 0
        assert (
            decision.quantity * request.entry_assumption
            <= request.equity * Decimal(10) * multiplier
        )


def test_risk_multiplier_scales_money_limits_but_not_position_count() -> None:
    base = RiskSizingInput(
        equity=Decimal("10000"),
        entry_assumption=Decimal("100"),
        stop_trigger=Decimal("99"),
        entry_slippage_per_unit=Decimal("0.2"),
        emergency_exit_slippage_per_unit=Decimal("0.2"),
        entry_fee_per_unit=Decimal("0.1"),
        exit_fee_per_unit=Decimal("0.1"),
        funding_buffer_per_unit=Decimal("0.1"),
        reserved_episode_risk=Decimal(0),
        reserved_all_risk=Decimal(0),
        reserved_cluster_risk=Decimal(0),
        current_daily_loss=Decimal(0),
        current_drawdown=Decimal(0),
        current_gross_notional=Decimal(0),
        current_positions=10,
        step_size=Decimal("0.1"),
        minimum_quantity=Decimal("0.1"),
        minimum_notional=Decimal("5"),
        maximum_executable_quantity=Decimal("100"),
    )
    assert not size_entry(base, risk_multiplier=Decimal("0.1")).approved
    assert not size_entry(base, risk_multiplier=Decimal("1")).approved
