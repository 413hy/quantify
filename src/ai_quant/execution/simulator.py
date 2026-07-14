"""Conservative replay fill model: maker fills require explicit queue consumption."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class SimulatedOrderType(StrEnum):
    MAKER = "MAKER"
    TAKER = "TAKER"


@dataclass(frozen=True, slots=True)
class FillResult:
    filled_quantity: Decimal
    average_price: Decimal | None
    remaining_quantity: Decimal


def simulate_fill(
    *,
    side: str,
    quantity: Decimal,
    order_type: SimulatedOrderType,
    limit_price: Decimal | None,
    visible_levels: tuple[tuple[Decimal, Decimal], ...],
    traded_at_limit_after_order: Decimal = Decimal(0),
    queue_ahead: Decimal | None = None,
) -> FillResult:
    if quantity <= 0 or side not in {"BUY", "SELL"}:
        raise ValueError("invalid simulated order")
    if order_type is SimulatedOrderType.MAKER:
        if limit_price is None or queue_ahead is None:
            return FillResult(Decimal(0), None, quantity)
        provable = max(Decimal(0), traded_at_limit_after_order - queue_ahead)
        filled = min(quantity, provable)
        return FillResult(filled, limit_price if filled else None, quantity - filled)
    remaining = quantity
    notional = Decimal(0)
    filled = Decimal(0)
    for price, available in visible_levels:
        take = min(remaining, available)
        if take <= 0:
            continue
        notional += take * price
        filled += take
        remaining -= take
        if remaining == 0:
            break
    return FillResult(filled, notional / filled if filled else None, remaining)
