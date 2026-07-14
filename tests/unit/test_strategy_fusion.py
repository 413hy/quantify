from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from ai_quant.features.price_action import Direction
from ai_quant.strategy.fusion import (
    OrderFlowConfirmation,
    OrderFlowTrigger,
    PriceActionArm,
    Setup,
    fuse_pa_order_flow,
)
from tests.market_fixtures import BASE_TIME


def arm(direction: Direction = Direction.LONG) -> PriceActionArm:
    long = direction is Direction.LONG
    return PriceActionArm(
        symbol="BTCUSDT",
        setup=Setup.T1_TREND_PULLBACK_CONTINUATION,
        direction=direction,
        armed_at=BASE_TIME,
        expires_at=BASE_TIME + timedelta(seconds=2),
        entry_reference=Decimal("100"),
        stop_anchor=Decimal("99") if long else Decimal("101"),
        target_reference=Decimal("101") if long else Decimal("99"),
        structure_version="structure-1",
    )


def confirmation(direction: Direction = Direction.LONG) -> OrderFlowConfirmation:
    return OrderFlowConfirmation(
        direction=direction,
        trigger=OrderFlowTrigger.OF1_MOMENTUM_SWEEP_CONTINUATION,
        confirmed_at=BASE_TIME + timedelta(milliseconds=500),
        valid=True,
    )


def test_matching_pa_and_order_flow_produce_one_second_signal() -> None:
    decision = fuse_pa_order_flow((arm(),), confirmation(), data_healthy=True)

    assert decision.candidate is not None
    assert decision.candidate.expires_at - decision.candidate.confirmed_at == timedelta(seconds=1)


def test_direction_conflict_is_always_no_trade() -> None:
    decision = fuse_pa_order_flow(
        (arm(Direction.LONG),), confirmation(Direction.SHORT), data_healthy=True
    )
    assert decision.candidate is None
    assert decision.reason_codes == ("FUSION_DIRECTION_CONFLICT",)


def test_multiple_arms_are_not_resolved_by_hidden_priority() -> None:
    decision = fuse_pa_order_flow((arm(), arm()), confirmation(), data_healthy=True)
    assert decision.candidate is None
    assert decision.reason_codes == ("PA_SETUP_CONFLICT",)
