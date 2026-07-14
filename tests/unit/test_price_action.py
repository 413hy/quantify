from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from ai_quant.features.price_action import (
    ClosedBar,
    ConfirmedSwing,
    Structure,
    SwingKind,
    classify_structure,
    confirmed_swings,
    simple_atr,
)
from tests.market_fixtures import BASE_TIME


def bar(index: int, high: str, low: str, close: str, *, closed: bool = True) -> ClosedBar:
    return ClosedBar(
        symbol="BTCUSDT",
        timeframe="1m",
        open_time=BASE_TIME + timedelta(minutes=index),
        close_time=BASE_TIME + timedelta(minutes=index + 1),
        open=Decimal(close),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(1),
        closed=closed,
    )


def test_unclosed_bar_is_rejected() -> None:
    with pytest.raises(ValueError, match="closed bars"):
        bar(0, "101", "99", "100", closed=False)


def test_swing_is_not_visible_until_right_bar_closes() -> None:
    bars = [
        bar(0, "101", "99", "100"),
        bar(1, "105", "100", "102"),
        bar(2, "103", "99", "101"),
    ]
    atrs = simple_atr(bars, 1)

    assert not confirmed_swings(bars[:2], atrs[:2], left=1, right=1)
    swings = confirmed_swings(bars, atrs, left=1, right=1)
    assert swings[0].kind is SwingKind.HIGH
    assert swings[0].confirm_time == bars[2].close_time


def test_structure_requires_highs_and_lows_to_agree() -> None:
    swings = tuple(
        ConfirmedSwing(
            swing_id=str(index),
            kind=kind,
            price=Decimal(price),
            open_time=BASE_TIME + timedelta(minutes=index),
            confirm_time=BASE_TIME + timedelta(minutes=index + 1),
            atr=Decimal(1),
        )
        for index, (kind, price) in enumerate(
            [
                (SwingKind.LOW, "90"),
                (SwingKind.HIGH, "100"),
                (SwingKind.LOW, "92"),
                (SwingKind.HIGH, "102"),
                (SwingKind.LOW, "94"),
                (SwingKind.HIGH, "104"),
            ]
        )
    )
    assert (
        classify_structure(swings, required_pairs=2, equal_tolerance_atr=Decimal("0.1"))
        is Structure.HH_HL
    )
