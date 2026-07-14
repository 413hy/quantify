"""Native protection coverage monitor for every non-zero position."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal


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
