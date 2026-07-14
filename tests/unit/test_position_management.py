from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from ai_quant.execution.protection import ProtectionRole, build_native_protection_plan
from ai_quant.features.price_action import Direction
from ai_quant.strategy.position import (
    ExitExecution,
    OrderFlowState,
    PositionAction,
    PositionEpisode,
    PositionObservation,
    manage_position,
)
from tests.market_fixtures import BASE_TIME


def episode(direction: Direction = Direction.LONG) -> PositionEpisode:
    long = direction is Direction.LONG
    return PositionEpisode(
        position_id="position-1",
        direction=direction,
        quantity=Decimal("0.01"),
        entry_price=Decimal("100"),
        stop_trigger=Decimal("99") if long else Decimal("101"),
        target_trigger=Decimal("101") if long else Decimal("99"),
        first_fill_at=BASE_TIME,
        maximum_holding_seconds=300,
        strategy_version="strategy-1",
    )


def observation(**changes: object) -> PositionObservation:
    base = PositionObservation(
        observed_at=BASE_TIME + timedelta(seconds=1),
        mark_price=Decimal("100.1"),
        order_flow_state=OrderFlowState.SUPPORTS_POSITION,
    )
    return replace(base, **changes)


def test_native_protection_builds_opposite_close_all_stop_and_take_profit() -> None:
    plan = build_native_protection_plan(
        direction=Direction.LONG,
        entry_price=Decimal("100"),
        stop_trigger=Decimal("99"),
        target_trigger=Decimal("101"),
    )

    assert plan.stop_loss.role is ProtectionRole.STOP_LOSS
    assert plan.stop_loss.order_type == "STOP_MARKET"
    assert plan.take_profit.role is ProtectionRole.TAKE_PROFIT
    assert plan.take_profit.order_type == "TAKE_PROFIT_MARKET"
    assert plan.stop_loss.side == plan.take_profit.side == "SELL"
    assert plan.stop_loss.close_position and plan.take_profit.close_position
    assert not plan.stop_loss.reduce_only and not plan.take_profit.reduce_only


def test_short_protection_reverses_side_and_requires_valid_structure() -> None:
    plan = build_native_protection_plan(
        direction=Direction.SHORT,
        entry_price=Decimal("100"),
        stop_trigger=Decimal("101"),
        target_trigger=Decimal("99"),
    )
    assert plan.stop_loss.side == plan.take_profit.side == "BUY"

    with pytest.raises(ValueError, match="structure"):
        build_native_protection_plan(
            direction=Direction.LONG,
            entry_price=Decimal("100"),
            stop_trigger=Decimal("101"),
            target_trigger=Decimal("99"),
        )


@pytest.mark.parametrize(
    ("changes", "priority", "reason"),
    [
        ({"kill_switch_active": True}, 1, "RISK_KILL_SWITCH_ACTIVE"),
        ({"account_consistent": False}, 1, "RISK_ACCOUNT_STATE_MISMATCH"),
        ({"protection_healthy": False}, 1, "RISK_PROTECTION_UNAVAILABLE"),
        ({"mark_price": Decimal("99")}, 2, "RISK_HARD_STOP_REACHED"),
        ({"hard_risk_limit_breached": True}, 2, "RISK_HARD_LIMIT_BREACHED"),
        ({"structure_valid": False}, 3, "PA_STRUCTURE_INVALIDATED"),
        (
            {"observed_at": BASE_TIME + timedelta(seconds=300)},
            4,
            "STRATEGY_MAX_HOLDING_TIME",
        ),
        ({"order_flow_state": OrderFlowState.REVERSED}, 5, "OF_REVERSE_ABSORPTION"),
        ({"order_flow_state": OrderFlowState.EXHAUSTED}, 5, "OF_EXHAUSTED"),
        ({"mark_price": Decimal("101")}, 6, "STRATEGY_TARGET_REACHED"),
    ],
)
def test_exit_conditions_are_full_reduce_only_taker_actions(
    changes: dict[str, object], priority: int, reason: str
) -> None:
    decision = manage_position(episode(), observation(**changes))

    assert decision.action is PositionAction.EXIT_FULL
    assert decision.execution is ExitExecution.TAKER_REDUCE_ONLY
    assert decision.close_quantity == Decimal("0.01")
    assert decision.reduce_only
    assert decision.priority == priority
    assert decision.reason_codes == (reason,)


def test_higher_priority_exit_wins_when_multiple_conditions_are_true() -> None:
    decision = manage_position(
        episode(),
        observation(
            kill_switch_active=True,
            mark_price=Decimal("99"),
            structure_valid=False,
            order_flow_state=OrderFlowState.REVERSED,
        ),
    )
    assert decision.priority == 1
    assert decision.reason_codes == ("RISK_KILL_SWITCH_ACTIVE",)


def test_unhealthy_data_holds_only_while_native_protection_is_healthy() -> None:
    held = manage_position(
        episode(),
        observation(data_healthy=False, order_flow_state=OrderFlowState.UNKNOWN),
    )
    assert held.action is PositionAction.HOLD
    assert held.close_quantity == 0
    assert held.reason_codes == ("DATA_UNHEALTHY_NATIVE_PROTECTION_HELD",)

    exited = manage_position(
        episode(),
        observation(
            data_healthy=False,
            protection_healthy=False,
            order_flow_state=OrderFlowState.UNKNOWN,
        ),
    )
    assert exited.action is PositionAction.EXIT_FULL
    assert exited.reason_codes == ("RISK_PROTECTION_UNAVAILABLE",)


def test_short_stop_and_target_use_inverse_comparisons() -> None:
    stopped = manage_position(episode(Direction.SHORT), observation(mark_price=Decimal("101")))
    targeted = manage_position(episode(Direction.SHORT), observation(mark_price=Decimal("99")))
    assert stopped.reason_codes == ("RISK_HARD_STOP_REACHED",)
    assert targeted.reason_codes == ("STRATEGY_TARGET_REACHED",)
