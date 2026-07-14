"""Native protection coverage monitor for every non-zero position."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from ai_quant.features.price_action import Direction


class ProtectionRole(StrEnum):
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"


@dataclass(frozen=True, slots=True)
class NativeProtectionOrder:
    role: ProtectionRole
    side: str
    order_type: str
    trigger_price: Decimal
    working_type: str
    price_protect: bool
    close_position: bool = True
    reduce_only: bool = False


@dataclass(frozen=True, slots=True)
class NativeProtectionPlan:
    stop_loss: NativeProtectionOrder
    take_profit: NativeProtectionOrder


def build_native_protection_plan(
    *,
    direction: Direction,
    entry_price: Decimal,
    stop_trigger: Decimal,
    target_trigger: Decimal,
    working_type: str = "MARK_PRICE",
    price_protect: bool = False,
) -> NativeProtectionPlan:
    """Build the close-all Algo TP/SL pair required immediately after a fill."""
    if min(entry_price, stop_trigger, target_trigger) <= 0:
        raise ValueError("protection prices must be positive")
    if direction is Direction.LONG:
        valid_structure = stop_trigger < entry_price < target_trigger
        side = "SELL"
    elif direction is Direction.SHORT:
        valid_structure = target_trigger < entry_price < stop_trigger
        side = "BUY"
    else:
        raise ValueError("protection direction must be LONG or SHORT")
    if not valid_structure:
        raise ValueError("protection trigger structure is invalid")
    if working_type not in {"MARK_PRICE", "CONTRACT_PRICE"}:
        raise ValueError("unsupported protection working type")

    return NativeProtectionPlan(
        stop_loss=NativeProtectionOrder(
            role=ProtectionRole.STOP_LOSS,
            side=side,
            order_type="STOP_MARKET",
            trigger_price=stop_trigger,
            working_type=working_type,
            price_protect=price_protect,
        ),
        take_profit=NativeProtectionOrder(
            role=ProtectionRole.TAKE_PROFIT,
            side=side,
            order_type="TAKE_PROFIT_MARKET",
            trigger_price=target_trigger,
            working_type=working_type,
            price_protect=price_protect,
        ),
    )


@dataclass(frozen=True, slots=True)
class ProtectionStatus:
    healthy: bool
    action: str
    reason_codes: tuple[str, ...]


def evaluate_protection(
    *,
    position_quantity: Decimal,
    protected_quantity: Decimal,
    first_fill_at: datetime,
    now: datetime,
    exchange_confirmed: bool,
    direction_correct: bool,
    reduce_only: bool,
    confirmation_deadline: timedelta = timedelta(milliseconds=1_000),
) -> ProtectionStatus:
    if position_quantity == 0:
        return ProtectionStatus(True, "CANCEL_ORPHAN_PROTECTION", ())
    covered = protected_quantity >= abs(position_quantity)
    if exchange_confirmed and direction_correct and reduce_only and covered:
        return ProtectionStatus(True, "NONE", ())
    if now - first_fill_at <= confirmation_deadline:
        return ProtectionStatus(False, "CREATE_OR_ADJUST_PROTECTION", ("PROTECTION_PENDING",))
    return ProtectionStatus(
        False,
        "CANCEL_ENTRY_RETRY_PROTECTION_THEN_FLATTEN",
        ("RISK_PROTECTION_UNAVAILABLE",),
    )
