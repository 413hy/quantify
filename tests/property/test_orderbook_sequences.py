from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from ai_quant.orderbook.book import LocalOrderBook, OrderBookState
from tests.market_fixtures import snapshot, update


@given(quantities=st.lists(st.integers(min_value=1, max_value=1_000), min_size=1, max_size=40))
def test_every_contiguous_sequence_has_a_deterministic_hash(quantities: list[int]) -> None:
    def replay() -> LocalOrderBook:
        book = LocalOrderBook("BTCUSDT")
        book.start_buffering("connection-1")
        previous = 100
        for index, quantity in enumerate(quantities, start=1):
            final = previous + 1
            book.ingest(
                update(
                    final,
                    final,
                    previous,
                    seconds=index,
                    bids=(("100", str(quantity)),),
                )
            )
            previous = final
        book.load_snapshot(snapshot())
        return book

    first = replay()
    second = replay()
    assert first.state is OrderBookState.HEALTHY
    assert second.state is OrderBookState.HEALTHY
    assert first.book_hash() == second.book_hash()


@given(gap=st.integers(min_value=1, max_value=10_000))
def test_every_missing_previous_id_fails_closed(gap: int) -> None:
    book = LocalOrderBook("BTCUSDT")
    book.start_buffering("connection-1")
    book.ingest(update(101, 101, 100))
    book.load_snapshot(snapshot())

    assert not book.ingest(update(102 + gap, 102 + gap, 101 + gap))
    assert book.state is OrderBookState.GAP
    assert not book.valid
