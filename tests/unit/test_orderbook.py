from __future__ import annotations

from datetime import timedelta

import pytest

from ai_quant.market_data.models import DataHealthStatus
from ai_quant.market_data.warmup import MarketWarmupGate
from ai_quant.orderbook.book import LocalOrderBook, OrderBookState
from tests.market_fixtures import BASE_TIME, snapshot, update


def synchronized_book() -> LocalOrderBook:
    book = LocalOrderBook("BTCUSDT")
    book.start_buffering("connection-1")
    book.ingest(update(101, 101, 100, bids=(("100", "4"),)))
    book.load_snapshot(snapshot())
    return book


def test_snapshot_and_diff_reconstruct_absolute_book() -> None:
    book = synchronized_book()

    assert book.state is OrderBookState.HEALTHY
    assert book.last_update_id == 101
    assert book.top() == (("100", "4"), ("101", "3"))

    assert book.ingest(update(102, 102, 101, bids=(("100", "0"), ("99", "5"))))
    assert book.top() == (("99", "5"), ("101", "3"))


def test_identical_duplicate_is_counted_but_not_reapplied() -> None:
    book = synchronized_book()
    event = update(102, 102, 101, asks=(("101", "5"),))

    assert book.ingest(event)
    expected_hash = book.book_hash()
    assert not book.ingest(event)

    assert book.book_hash() == expected_hash
    assert book.stats.duplicate_count == 1
    assert book.stats.applied_count == 2


def test_gap_invalidates_and_clears_the_entire_book() -> None:
    book = synchronized_book()

    assert not book.ingest(update(103, 103, 102))
    assert book.state is OrderBookState.GAP
    assert not book.valid
    with pytest.raises(RuntimeError, match="not valid"):
        book.top()
    health = book.health(
        now=BASE_TIME + timedelta(seconds=3),
        warmed_up=False,
        clock_offset_ms=0,
    )
    assert health.status is DataHealthStatus.GAP_DETECTED
    assert health.reason_codes == ("SEQUENCE_GAP",)


def test_crossed_book_is_never_published() -> None:
    book = synchronized_book()

    assert not book.ingest(update(102, 102, 101, bids=(("102", "1"),)))
    assert not book.valid
    assert (
        book.health(
            now=BASE_TIME + timedelta(seconds=2),
            warmed_up=False,
            clock_offset_ms=0,
        ).status
        is DataHealthStatus.INVALID
    )


def test_stale_book_is_cleared() -> None:
    book = synchronized_book()

    assert book.mark_stale(BASE_TIME + timedelta(seconds=10))
    assert book.state is OrderBookState.STALE
    assert not book.valid


def test_warmup_requires_continuity_trade_volume_bars_and_safe_clock() -> None:
    gate = MarketWarmupGate()
    gate.observe_health(healthy=True, observed_at=BASE_TIME)
    gate.record_valid_trades(1_000)
    gate.record_closed_bar("1m")
    gate.record_closed_bar("1m")
    gate.record_closed_bar("5m")

    assert not gate.ready(now=BASE_TIME + timedelta(seconds=119), clock_safe=True)
    assert not gate.ready(now=BASE_TIME + timedelta(seconds=120), clock_safe=False)
    assert gate.ready(now=BASE_TIME + timedelta(seconds=120), clock_safe=True)

    gate.observe_health(healthy=False, observed_at=BASE_TIME + timedelta(seconds=121))
    assert not gate.ready(now=BASE_TIME + timedelta(seconds=300), clock_safe=True)
