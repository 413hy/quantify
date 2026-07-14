"""Order Flow frame calculations that never substitute raw q for normal nq."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ai_quant.market_data.models import AggregateTrade


@dataclass(frozen=True, slots=True)
class BookLevel:
    price: Decimal
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class OrderFlowFrame:
    book_imbalance: Decimal
    microprice: Decimal
    microprice_mid_bps: Decimal
    trade_imbalance: Decimal
    aggressive_notional: Decimal
    cvd_notional: Decimal
    valid: bool
    reason_codes: tuple[str, ...]


def calculate_order_flow(
    bids: tuple[BookLevel, ...],
    asks: tuple[BookLevel, ...],
    trades: tuple[AggregateTrade, ...],
    *,
    depth_levels: int,
) -> OrderFlowFrame:
    selected_bids = bids[:depth_levels]
    selected_asks = asks[:depth_levels]
    if depth_levels < 1 or not selected_bids or not selected_asks:
        raise ValueError("both book sides and a positive depth are required")
    bid_quantity = sum((level.quantity for level in selected_bids), Decimal(0))
    ask_quantity = sum((level.quantity for level in selected_asks), Decimal(0))
    total_depth = bid_quantity + ask_quantity
    top_quantity = selected_bids[0].quantity + selected_asks[0].quantity
    mid = (selected_bids[0].price + selected_asks[0].price) / Decimal(2)
    if total_depth <= 0 or top_quantity <= 0 or mid <= 0:
        raise ValueError("order-flow denominator is non-positive")
    book_imbalance = (bid_quantity - ask_quantity) / total_depth
    microprice = (
        selected_asks[0].price * selected_bids[0].quantity
        + selected_bids[0].price * selected_asks[0].quantity
    ) / top_quantity
    buyer = Decimal(0)
    seller = Decimal(0)
    for trade in trades:
        normal_quantity = Decimal(trade.notional_quantity)
        notional = Decimal(trade.price) * normal_quantity
        if trade.buyer_is_maker:
            seller += notional
        else:
            buyer += notional
    total_aggressive = buyer + seller
    if total_aggressive <= 0:
        return OrderFlowFrame(
            book_imbalance,
            microprice,
            (microprice - mid) / mid * Decimal(10_000),
            Decimal(0),
            Decimal(0),
            Decimal(0),
            False,
            ("OF_INSUFFICIENT_AGGRESSION",),
        )
    imbalance = (buyer - seller) / total_aggressive
    return OrderFlowFrame(
        book_imbalance=book_imbalance,
        microprice=microprice,
        microprice_mid_bps=(microprice - mid) / mid * Decimal(10_000),
        trade_imbalance=imbalance,
        aggressive_notional=total_aggressive,
        cvd_notional=buyer - seller,
        valid=True,
        reason_codes=(),
    )
