from __future__ import annotations

from ai_quant.orderbook.book import LocalOrderBook, OrderBookState
from tests.market_fixtures import snapshot, update


def test_buffer_overflow_requires_a_fresh_snapshot_cycle() -> None:
    book = LocalOrderBook("BTCUSDT", buffer_limit=1)
    book.start_buffering("connection-1")

    book.ingest(update(101, 101, 100))
    book.ingest(update(102, 102, 101))

    assert book.state is OrderBookState.GAP
    assert not book.valid


def test_out_of_order_event_invalidates_book() -> None:
    book = LocalOrderBook("BTCUSDT")
    book.start_buffering("connection-1")
    book.ingest(update(101, 101, 100))
    book.load_snapshot(snapshot())

    book.ingest(update(99, 100, 99, seconds=2))

    assert not book.valid
    assert book.stats.out_of_order_count == 1
