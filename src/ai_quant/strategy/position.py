"""Deterministic management of an existing one-way position episode."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from ai_quant.features.price_action import Direction


class OrderFlowState(StrEnum):
    SUPPORTS_POSITION = "SUPPORTS_POSITION"
    EXHAUSTED = "EXHAUSTED"
    REVERSED = "REVERSED"
    UNKNOWN = "UNKNOWN"


class PositionAction(StrEnum):
    HOLD = "HOLD"
    EXIT_FULL = "EXIT_FULL"


class ExitExecution(StrEnum):
    NONE = "NONE"
    TAKER_REDUCE_ONLY = "TAKER_REDUCE_ONLY"


@dataclass(frozen=True, slots=True)
class PositionEpisode:
    position_id: str
    direction: Direction
    quantity: Decimal
    entry_price: Decimal
    stop_trigger: Decimal
    target_trigger: Decimal
    first_fill_at: datetime
    maximum_holding_seconds: int
    strategy_version: str

    def __post_init__(self) -> None:
        _require_utc(self.first_fill_at)
        if not self.position_id or not self.strategy_version:
            raise ValueError("position identity and strategy version are required")
        if self.direction not in {Direction.LONG, Direction.SHORT}:
            raise ValueError("position direction must be LONG or SHORT")
        if self.quantity <= 0:
            raise ValueError("position quantity must be positive")
        if min(self.entry_price, self.stop_trigger, self.target_trigger) <= 0:
            raise ValueError("position prices must be positive")
        if not 30 <= self.maximum_holding_seconds <= 900:
            raise ValueError("maximum holding time must be within [30,900]")
        if self.direction is Direction.LONG:
            structure_valid = self.stop_trigger < self.entry_price < self.target_trigger
        else:
            structure_valid = self.target_trigger < self.entry_price < self.stop_trigger
        if not structure_valid:
            raise ValueError("position stop/entry/target structure is invalid")


@dataclass(frozen=True, slots=True)
class PositionObservation:
    observed_at: datetime
    mark_price: Decimal
    order_flow_state: OrderFlowState
    structure_valid: bool = True
    data_healthy: bool = True
    protection_healthy: bool = True
    account_consistent: bool = True
    kill_switch_active: bool = False
    hard_risk_limit_breached: bool = False

    def __post_init__(self) -> None:
        _require_utc(self.observed_at)
        if self.mark_price <= 0:
            raise ValueError("mark price must be positive")


@dataclass(frozen=True, slots=True)
class PositionDecision:
    action: PositionAction
    execution: ExitExecution
    close_quantity: Decimal
    reduce_only: bool
    priority: int | None
    reason_codes: tuple[str, ...]


def manage_position(
    episode: PositionEpisode,
    observation: PositionObservation,
) -> PositionDecision:
    """Apply the frozen exit precedence without increasing absolute exposure."""
    if observation.observed_at < episode.first_fill_at:
        raise ValueError("position observation precedes the first fill")

    if observation.kill_switch_active:
        return _exit(episode, 1, "RISK_KILL_SWITCH_ACTIVE")
    if not observation.account_consistent:
        return _exit(episode, 1, "RISK_ACCOUNT_STATE_MISMATCH")
    if not observation.protection_healthy:
        return _exit(episode, 1, "RISK_PROTECTION_UNAVAILABLE")

    stop_reached = (
        observation.mark_price <= episode.stop_trigger
        if episode.direction is Direction.LONG
        else observation.mark_price >= episode.stop_trigger
    )
    if stop_reached:
        return _exit(episode, 2, "RISK_HARD_STOP_REACHED")
    if observation.hard_risk_limit_breached:
        return _exit(episode, 2, "RISK_HARD_LIMIT_BREACHED")

    if not observation.structure_valid:
        return _exit(episode, 3, "PA_STRUCTURE_INVALIDATED")

    elapsed_seconds = (observation.observed_at - episode.first_fill_at).total_seconds()
    if elapsed_seconds >= episode.maximum_holding_seconds:
        return _exit(episode, 4, "STRATEGY_MAX_HOLDING_TIME")

    if observation.order_flow_state is OrderFlowState.REVERSED:
        return _exit(episode, 5, "OF_REVERSE_ABSORPTION")
    if observation.order_flow_state is OrderFlowState.EXHAUSTED:
        return _exit(episode, 5, "OF_EXHAUSTED")

    target_reached = (
        observation.mark_price >= episode.target_trigger
        if episode.direction is Direction.LONG
        else observation.mark_price <= episode.target_trigger
    )
    if target_reached:
        return _exit(episode, 6, "STRATEGY_TARGET_REACHED")

    hold_reason = (
        "DATA_UNHEALTHY_NATIVE_PROTECTION_HELD"
        if not observation.data_healthy
        or observation.order_flow_state is OrderFlowState.UNKNOWN
        else "POSITION_MANAGEMENT_HOLD"
    )
    return PositionDecision(
        action=PositionAction.HOLD,
        execution=ExitExecution.NONE,
        close_quantity=Decimal(0),
        reduce_only=True,
        priority=None,
        reason_codes=(hold_reason,),
    )


def _exit(episode: PositionEpisode, priority: int, reason: str) -> PositionDecision:
    return PositionDecision(
        action=PositionAction.EXIT_FULL,
        execution=ExitExecution.TAKER_REDUCE_ONLY,
        close_quantity=episode.quantity,
        reduce_only=True,
        priority=priority,
        reason_codes=(reason,),
    )


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("position timestamp must be timezone-aware UTC")
