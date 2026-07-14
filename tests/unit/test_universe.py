from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from ai_quant.universe.membership import MembershipController
from ai_quant.universe.ranking import UniverseInput, UniverseRank, UniverseSnapshot, rank_universe
from tests.market_fixtures import BASE_TIME


def item(symbol: str, value: int) -> UniverseInput:
    decimal = Decimal(value)
    return UniverseInput(
        symbol=symbol,
        quote_notional_15m=decimal * 1_000,
        twap_bid_depth_10bps=decimal * 100,
        twap_ask_depth_10bps=decimal * 100,
        median_spread_bps=Decimal(100 - value),
        trade_count_15m=value * 10,
        input_completeness_pct=Decimal(value),
    )


def snapshot(scores: list[tuple[str, str]]) -> UniverseSnapshot:
    return UniverseSnapshot(
        ranking=tuple(UniverseRank(symbol, Decimal(score), {}) for symbol, score in scores),
        eligible_count=len(scores),
        reduced_pool_alert=len(scores) < 10,
    )


def test_ranking_uses_percent_scores_and_preserves_component_evidence() -> None:
    ranked = rank_universe([item("AAAUSDT", 10), item("BBBUSDT", 50), item("CCCUSDT", 90)])

    assert [rank.symbol for rank in ranked.ranking] == ["CCCUSDT", "BBBUSDT", "AAAUSDT"]
    assert ranked.ranking[0].score == Decimal(100)
    assert ranked.ranking[1].score == Decimal(50)
    assert ranked.ranking[2].score == Decimal(0)
    assert ranked.ranking[0].components["liquidity"].q05 > 0


def test_tied_raw_values_receive_the_same_midrank() -> None:
    first = item("AAAUSDT", 50)
    second = item("BBBUSDT", 50)
    ranked = rank_universe([second, first])

    assert ranked.ranking[0].score == ranked.ranking[1].score == Decimal(50)
    assert [rank.symbol for rank in ranked.ranking] == ["AAAUSDT", "BBBUSDT"]


def test_membership_requires_two_confirmations_and_respects_residence() -> None:
    controller = MembershipController(size=2)
    first = snapshot([("AAA", "90"), ("BBB", "80"), ("CCC", "70")])

    assert not controller.apply(first, computed_at=BASE_TIME).active
    admitted = controller.apply(first, computed_at=BASE_TIME + timedelta(minutes=15))
    assert admitted.active == ("AAA", "BBB")

    challenge = snapshot([("CCC", "100"), ("AAA", "90"), ("BBB", "80")])
    controller.apply(challenge, computed_at=BASE_TIME + timedelta(minutes=30))
    still_resident = controller.apply(challenge, computed_at=BASE_TIME + timedelta(minutes=45))
    assert still_resident.active == ("AAA", "BBB")

    replaced = controller.apply(challenge, computed_at=BASE_TIME + timedelta(minutes=75))
    assert replaced.active == ("CCC", "AAA")


def test_ineligible_member_leaves_immediately_but_remains_managed() -> None:
    controller = MembershipController(size=1)
    ranked = snapshot([("AAA", "90"), ("BBB", "80")])
    controller.apply(ranked, computed_at=BASE_TIME)
    controller.apply(ranked, computed_at=BASE_TIME + timedelta(minutes=15))

    view = controller.apply(
        ranked,
        computed_at=BASE_TIME + timedelta(minutes=30),
        immediately_ineligible={"AAA"},
        managed_positions={"AAA"},
    )
    assert "AAA" not in view.active
    assert view.managed_positions == ("AAA",)
