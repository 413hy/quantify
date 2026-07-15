from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ai_quant.research.testnet_observation_replay import ReplayParameters, replay_observations


def test_replay_requires_consecutive_activity_confirmations_and_closes_target() -> None:
    start = datetime(2026, 7, 15, tzinfo=UTC)
    documents = [
        _observation(start + timedelta(seconds=index * 10), mid=mid, plan=index in {1, 2})
        for index, mid in enumerate(("100", "100", "100", "100.4"))
    ]

    report = replay_observations(
        documents,
        ReplayParameters(
            "TEST",
            activity_lookback_rounds=3,
            minimum_activity_samples=2,
        ),
        start_at=start,
    )

    assert report["closed_trades"] == 1
    assert report["winning_trades"] == 1
    assert report["target_count"] == 1
    assert Decimal(str(report["net_bps"])) == Decimal("20")


def test_replay_activity_ratio_can_filter_a_low_activity_candidate() -> None:
    start = datetime(2026, 7, 15, tzinfo=UTC)
    documents = [
        _observation(
            start + timedelta(seconds=index * 10),
            mid="100",
            plan=index in {2, 3},
            activity="10" if index >= 2 else "100",
        )
        for index in range(4)
    ]

    report = replay_observations(
        documents,
        ReplayParameters(
            "TEST",
            minimum_activity_ratio=Decimal("2"),
            activity_lookback_rounds=4,
            minimum_activity_samples=2,
        ),
        start_at=start,
    )

    assert report["confirmed_candidate_count"] == 0
    assert report["closed_trades"] == 0


def test_replay_can_counterfactually_expand_the_minimum_target_distance() -> None:
    start = datetime(2026, 7, 15, tzinfo=UTC)
    documents = [
        _observation(start + timedelta(seconds=index * 10), mid=mid, plan=index in {1, 2})
        for index, mid in enumerate(("100", "100", "100", "100.4", "100.5"))
    ]

    report = replay_observations(
        documents,
        ReplayParameters(
            "TEST",
            activity_lookback_rounds=3,
            minimum_activity_samples=2,
            minimum_target_bps=Decimal("50"),
        ),
        start_at=start,
    )

    assert report["closed_trades"] == 1
    assert report["target_count"] == 1
    assert report["trades"][0]["exited_at"] == (start + timedelta(seconds=40)).isoformat()
    assert Decimal(str(report["net_bps"])) == Decimal("40")


def test_replay_can_counterfactually_override_the_target_distance() -> None:
    start = datetime(2026, 7, 15, tzinfo=UTC)
    documents = [
        _observation(start + timedelta(seconds=index * 10), mid=mid, plan=index in {1, 2})
        for index, mid in enumerate(("100", "100", "100", "100.2"))
    ]

    report = replay_observations(
        documents,
        ReplayParameters(
            "TEST",
            activity_lookback_rounds=3,
            minimum_activity_samples=2,
            target_bps_override=Decimal("20"),
        ),
        start_at=start,
    )

    assert report["closed_trades"] == 1
    assert report["target_count"] == 1
    assert Decimal(str(report["net_bps"])) == Decimal("10")


def test_replay_rejects_conflicting_target_controls() -> None:
    try:
        ReplayParameters(
            "TEST",
            minimum_target_bps=Decimal("20"),
            target_bps_override=Decimal("20"),
        )
    except ValueError as error:
        assert str(error) == "replay target controls are mutually exclusive"
    else:
        raise AssertionError("conflicting target controls must fail")


def _observation(
    observed_at: datetime,
    *,
    mid: str,
    plan: bool,
    activity: str = "100",
) -> dict[str, object]:
    return {
        "record_type": "SIGNAL_OBSERVATION",
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "symbol": "XRPUSDT",
        "mid_price": mid,
        "spread_bps": "1",
        "order_flow": {"aggressive_notional": activity},
        "testnet_experimental_plan": (
            {
                "symbol": "XRPUSDT",
                "direction": "LONG",
                "entry_reference": "100",
                "stop_anchor": "99.7",
                "target_reference": "100.3",
                "signal_quality_score": "4",
                "observed_spread_bps": "1",
                "pa_alignment_count": 1,
            }
            if plan
            else None
        ),
    }
