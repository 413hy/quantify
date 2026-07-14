"""PA/OF fusion: ambiguity, disagreement, staleness, or bad structure means no trade."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from ai_quant.features.price_action import Direction


class Setup(StrEnum):
    T1_TREND_PULLBACK_CONTINUATION = "T1_TREND_PULLBACK_CONTINUATION"
    B1_BREAKOUT_FOLLOW_THROUGH = "B1_BREAKOUT_FOLLOW_THROUGH"
    B2_BREAK_RETEST = "B2_BREAK_RETEST"
    R1_RANGE_EDGE_REJECTION = "R1_RANGE_EDGE_REJECTION"


class OrderFlowTrigger(StrEnum):
    OF1_MOMENTUM_SWEEP_CONTINUATION = "OF1_MOMENTUM_SWEEP_CONTINUATION"
    OF2_LIQUIDITY_SWEEP_ABSORPTION_REVERSAL = "OF2_LIQUIDITY_SWEEP_ABSORPTION_REVERSAL"


@dataclass(frozen=True, slots=True)
class PriceActionArm:
    symbol: str
    setup: Setup
    direction: Direction
    armed_at: datetime
    expires_at: datetime
    entry_reference: Decimal
    stop_anchor: Decimal
    target_reference: Decimal
    structure_version: str


@dataclass(frozen=True, slots=True)
class OrderFlowConfirmation:
    direction: Direction
    trigger: OrderFlowTrigger
    confirmed_at: datetime
    valid: bool
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SignalCandidate:
    candidate_id: str
    symbol: str
    setup: Setup
    direction: Direction
    order_flow_trigger: OrderFlowTrigger
    confirmed_at: datetime
    expires_at: datetime
    entry_reference: Decimal
    stop_anchor: Decimal
    target_reference: Decimal
    structure_version: str


@dataclass(frozen=True, slots=True)
class FusionDecision:
    candidate: SignalCandidate | None
    reason_codes: tuple[str, ...]


def fuse_pa_order_flow(
    arms: tuple[PriceActionArm, ...],
    confirmation: OrderFlowConfirmation | None,
    *,
    data_healthy: bool,
    confirmation_window: timedelta = timedelta(milliseconds=2_000),
    signal_ttl: timedelta = timedelta(milliseconds=1_000),
) -> FusionDecision:
    if not data_healthy:
        return FusionDecision(None, ("MARKET_DATA_NOT_HEALTHY",))
    if len(arms) != 1:
        reason = "PA_SETUP_CONFLICT" if arms else "FUSION_NO_CONFIRMATION"
        return FusionDecision(None, (reason,))
    arm = arms[0]
    _require_utc(arm.armed_at)
    _require_utc(arm.expires_at)
    if arm.direction is Direction.NEUTRAL:
        return FusionDecision(None, ("PA_DIRECTION_NEUTRAL",))
    if confirmation is None:
        return FusionDecision(None, ("FUSION_NO_CONFIRMATION",))
    _require_utc(confirmation.confirmed_at)
    if not confirmation.valid:
        return FusionDecision(None, confirmation.reason_codes or ("OF_CONFIRMATION_INVALID",))
    if (
        confirmation.confirmed_at > arm.expires_at
        or confirmation.confirmed_at - arm.armed_at > confirmation_window
    ):
        return FusionDecision(None, ("OF_CONFIRMATION_EXPIRED",))
    if confirmation.confirmed_at < arm.armed_at:
        return FusionDecision(None, ("OF_CONFIRMATION_NOT_CAUSAL",))
    if confirmation.direction is not arm.direction:
        return FusionDecision(None, ("FUSION_DIRECTION_CONFLICT",))
    if arm.direction is Direction.LONG:
        structure_valid = arm.stop_anchor < arm.entry_reference < arm.target_reference
    else:
        structure_valid = arm.target_reference < arm.entry_reference < arm.stop_anchor
    if not structure_valid:
        return FusionDecision(None, ("PA_STRUCTURE_INVALIDATED",))
    identity = "|".join(
        (
            arm.symbol,
            arm.setup,
            arm.direction,
            confirmation.trigger,
            confirmation.confirmed_at.isoformat(),
            arm.structure_version,
        )
    ).encode()
    candidate = SignalCandidate(
        candidate_id=hashlib.sha256(identity).hexdigest(),
        symbol=arm.symbol,
        setup=arm.setup,
        direction=arm.direction,
        order_flow_trigger=confirmation.trigger,
        confirmed_at=confirmation.confirmed_at,
        expires_at=confirmation.confirmed_at + signal_ttl,
        entry_reference=arm.entry_reference,
        stop_anchor=arm.stop_anchor,
        target_reference=arm.target_reference,
        structure_version=arm.structure_version,
    )
    return FusionDecision(candidate, ())


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("strategy timestamp must be timezone-aware UTC")
