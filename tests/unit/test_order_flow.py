from __future__ import annotations

from decimal import Decimal

from ai_quant.features.order_flow import BookLevel, calculate_order_flow
from tests.market_fixtures import trade


def test_order_flow_uses_normal_quantity_not_raw_quantity() -> None:
    bids = (BookLevel(Decimal("100"), Decimal("3")),)
    asks = (BookLevel(Decimal("101"), Decimal("1")),)
    trades = (
        trade(1, quantity="1000000", normal_quantity="2", buyer_is_maker=False),
        trade(2, quantity="1", normal_quantity="1", buyer_is_maker=True),
    )

    frame = calculate_order_flow(bids, asks, trades, depth_levels=1)

    assert frame.aggressive_notional == Decimal(300)
    assert frame.trade_imbalance == Decimal(1) / Decimal(3)
    assert frame.cvd_notional == Decimal(100)


def test_zero_normal_quantity_is_valid_but_cannot_confirm_flow() -> None:
    frame = calculate_order_flow(
        (BookLevel(Decimal("100"), Decimal("1")),),
        (BookLevel(Decimal("101"), Decimal("1")),),
        (trade(1, normal_quantity="0"),),
        depth_levels=1,
    )
    assert not frame.valid
    assert frame.reason_codes == ("OF_INSUFFICIENT_AGGRESSION",)
