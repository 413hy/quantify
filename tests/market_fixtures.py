from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from ai_quant.market_data.models import (
    AggregateTrade,
    BookSnapshot,
    DepthLevel,
    DepthUpdate,
)

BASE_TIME = datetime(2026, 7, 14, 10, tzinfo=UTC)


def snapshot(*, last_update_id: int = 100) -> BookSnapshot:
    return BookSnapshot(
        symbol="BTCUSDT",
        connection_id="connection-1",
        received_at=BASE_TIME,
        last_update_id=last_update_id,
        bids=(DepthLevel(price="100", quantity="2"),),
        asks=(DepthLevel(price="101", quantity="3"),),
    )


def update(
    first: int,
    final: int,
    previous: int,
    *,
    seconds: int = 1,
    bids: tuple[tuple[str, str], ...] = (),
    asks: tuple[tuple[str, str], ...] = (),
) -> DepthUpdate:
    identity = f"{first}:{final}:{previous}:{seconds}:{bids}:{asks}".encode()
    return DepthUpdate(
        environment="paper",
        symbol="BTCUSDT",
        connection_id="connection-1",
        subscription_id="subscription-1",
        event_time=BASE_TIME + timedelta(seconds=seconds),
        transaction_time=BASE_TIME + timedelta(seconds=seconds),
        received_at=BASE_TIME + timedelta(seconds=seconds, milliseconds=10),
        U=first,
        u=final,
        pu=previous,
        bids=tuple(DepthLevel(price=price, quantity=qty) for price, qty in bids),
        asks=tuple(DepthLevel(price=price, quantity=qty) for price, qty in asks),
        raw_hash=hashlib.sha256(identity).hexdigest(),
        clock_offset_ms=0.2,
        rest_base="https://fapi.binance.com",
        route_role="MARKET_DATA",
        route_base_hash="a" * 64,
    )


def trade(
    trade_id: int,
    *,
    price: str = "100",
    quantity: str = "99",
    normal_quantity: str = "1",
    buyer_is_maker: bool = False,
) -> AggregateTrade:
    observed_at = BASE_TIME + timedelta(milliseconds=trade_id)
    return AggregateTrade(
        environment="paper",
        symbol="BTCUSDT",
        connection_id="connection-1",
        event_time=observed_at,
        received_at=observed_at + timedelta(milliseconds=10),
        aggregate_trade_id=trade_id,
        first_trade_id=trade_id,
        last_trade_id=trade_id,
        price=price,
        quantity=quantity,
        notional_quantity=normal_quantity,
        settlement_time=observed_at,
        buyer_is_maker=buyer_is_maker,
        raw_hash=hashlib.sha256(f"trade:{trade_id}".encode()).hexdigest(),
        route_role="MARKET_DATA",
        route_base_hash="a" * 64,
    )
