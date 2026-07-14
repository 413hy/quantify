from __future__ import annotations

from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from ai_quant.universe.ranking import UniverseInput, rank_universe


@given(
    values=st.lists(
        st.integers(min_value=1, max_value=1_000_000), min_size=1, max_size=30, unique=True
    )
)
def test_universe_scores_are_bounded_and_deterministic(values: list[int]) -> None:
    inputs = [
        UniverseInput(
            symbol=f"S{index:02d}USDT",
            quote_notional_15m=Decimal(value),
            twap_bid_depth_10bps=Decimal(value),
            twap_ask_depth_10bps=Decimal(value),
            median_spread_bps=Decimal(1) / Decimal(value),
            trade_count_15m=value,
            input_completeness_pct=Decimal("100"),
        )
        for index, value in enumerate(values)
    ]
    first = rank_universe(inputs)
    second = rank_universe(list(reversed(inputs)))

    assert first == second
    assert all(Decimal(0) <= rank.score <= Decimal(100) for rank in first.ranking)
