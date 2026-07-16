from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

import ai_quant.strategy.testnet_baseline as baseline
from ai_quant.features.order_flow import OrderFlowFrame
from ai_quant.features.price_action import ClosedBar, Direction, PriceActionFrame, Regime, Structure
from ai_quant.services.testnet_campaign import (
    CampaignLimits,
    _apply_market_impulse_plans,
    _claim_signal_episode,
    _experimental_candidate_rank,
    _money,
    _position_invalidation_plans,
    _recovery_algo_pair,
    _select_candidate,
    _summary_text,
    _trend_continuation_cooldown_waived,
    _update_pending_signals,
    campaign_trade_allowed,
)
from ai_quant.services.testnet_campaign import (
    TestnetCampaign as Campaign,
)
from ai_quant.strategy.testnet_baseline import (
    TestnetSignalParameters as SignalParameters,
)
from ai_quant.strategy.testnet_baseline import (
    evaluate_testnet_baseline,
    gross_target_bps_for_symbol,
    historical_target_feasibility_rate,
    predictive_average_10m_before_after,
)


def test_predictive_average_combines_ten_observed_and_ten_forecast_closes() -> None:
    start = datetime(2026, 7, 14, 12, tzinfo=UTC)
    bars = [
        ClosedBar(
            symbol="BTCUSDT",
            timeframe="1m",
            open_time=start + timedelta(minutes=index),
            close_time=start + timedelta(minutes=index + 1),
            open=Decimal(100 + index),
            high=Decimal(100 + index),
            low=Decimal(100 + index),
            close=Decimal(100 + index),
            volume=Decimal(1),
        )
        for index in range(10)
    ]

    assert predictive_average_10m_before_after(bars) == Decimal("109.5")


def test_predictive_average_requires_ten_closed_minutes() -> None:
    with pytest.raises(ValueError, match="requires ten closed"):
        predictive_average_10m_before_after([])


def test_historical_target_feasibility_uses_only_closed_forward_excursions() -> None:
    start = datetime(2026, 7, 14, 12, tzinfo=UTC)
    bars = [
        ClosedBar(
            symbol="BTCUSDT",
            timeframe="1m",
            open_time=start + timedelta(minutes=index),
            close_time=start + timedelta(minutes=index + 1),
            open=Decimal("100"),
            high=Decimal("102"),
            low=Decimal("98"),
            close=Decimal("100"),
            volume=Decimal(1),
        )
        for index in range(60)
    ]

    assert historical_target_feasibility_rate(
        bars,
        direction=Direction.LONG,
        target_bps=Decimal("100"),
        horizon_bars=5,
        lookback_bars=40,
    ) == Decimal(1)
    assert historical_target_feasibility_rate(
        bars,
        direction=Direction.SHORT,
        target_bps=Decimal("100"),
        horizon_bars=5,
        lookback_bars=40,
    ) == Decimal(1)


def test_testnet_baseline_rejects_neutral_price_action_and_unconfirmed_book() -> None:
    server_time_ms = int(datetime(2026, 7, 14, 12, tzinfo=UTC).timestamp() * 1_000)
    decision = evaluate_testnet_baseline(
        symbol="SOLUSDT",
        server_time_ms=server_time_ms,
        one_minute_klines=_klines(server_time_ms, interval_ms=60_000),
        five_minute_klines=_klines(server_time_ms, interval_ms=300_000),
        depth={
            "bids": [[f"{76 - level / 100:.2f}", "100"] for level in range(20)],
            "asks": [[f"{76.01 + level / 100:.2f}", "100"] for level in range(20)],
        },
        aggregate_trades=[
            {
                "a": index,
                "p": "76.01",
                "q": "1",
                "nq": "1",
                "f": index,
                "l": index,
                "T": server_time_ms - 1_000 + index,
                "m": False,
            }
            for index in range(10)
        ],
    )

    assert not decision.eligible
    assert "PA_1M_NOT_LONG" in decision.reason_codes
    assert "PA_5M_NOT_LONG" in decision.reason_codes
    assert "OF_BOOK_IMBALANCE_INSUFFICIENT" in decision.reason_codes
    assert decision.evidence()["validation_status"] == "UNVALIDATED_TESTNET_BASELINE"


def test_campaign_limits_allow_all_five_confirmed_symbols_without_time_cooldown() -> None:
    limits = CampaignLimits()
    assert limits.maximum_parallel_positions == 5
    assert limits.evaluation_interval_seconds == 10
    assert limits.maximum_candidates_per_round == 5
    assert limits.trade_cooldown_seconds == 0
    assert limits.same_direction_scale_enabled is False
    assert limits.automatic_reversal_entry_enabled is True
    assert limits.signal_confirmation_rounds == 3
    assert limits.minimum_directional_forecast_bps == Decimal("2.00")
    assert limits.impulse_minimum_directional_forecast_bps == Decimal("0.10")
    assert limits.continuation_minimum_directional_forecast_bps == Decimal("2.00")
    assert limits.structure_substitute_minimum_directional_forecast_bps == Decimal("3.00")
    assert limits.minimum_target_feasibility_rate_15m == Decimal("0.20")
    assert limits.impulse_minimum_target_feasibility_rate_15m == Decimal("0.02")
    assert limits.minimum_net_reward_risk_ratio == Decimal("0.50")
    assert limits.impulse_minimum_net_reward_risk_ratio == Decimal("0.15")
    assert limits.activity_filter_enabled is False
    assert limits.impulse_activity_filter_enabled is False
    assert limits.impulse_minimum_activity_ratio == Decimal("0.10")
    assert limits.impulse_maximum_activity_ratio == Decimal("10.00")
    assert limits.impulse_maximum_momentum_bps == Decimal("8.00")
    assert limits.impulse_lookback_rounds == 10
    assert limits.sustained_lookback_rounds == 22
    assert limits.signal_evidence_window_rounds == 6
    assert limits.continuation_confirmation_rounds == 4
    now = datetime(2026, 7, 14, 12, tzinfo=UTC)
    assert campaign_trade_allowed(
        now=now,
        last_trade_at=None,
        daily_trade_count=0,
        daily_net_pnl=Decimal("0"),
        limits=limits,
    ) == (True, None)
    assert campaign_trade_allowed(
        now=now,
        last_trade_at=now - timedelta(seconds=59),
        daily_trade_count=0,
        daily_net_pnl=Decimal("0"),
        limits=limits,
    ) == (True, None)
    assert campaign_trade_allowed(
        now=now,
        last_trade_at=None,
        daily_trade_count=100,
        daily_net_pnl=Decimal("0"),
        limits=limits,
    ) == (False, "DAILY_TRADE_LIMIT_REACHED")
    assert campaign_trade_allowed(
        now=now,
        last_trade_at=None,
        daily_trade_count=1,
        daily_net_pnl=Decimal("-1.00"),
        limits=limits,
    ) == (False, "DAILY_LOSS_LIMIT_REACHED")


def test_one_minute_runtime_limits_preserve_shortest_supported_signal_windows() -> None:
    limits = CampaignLimits(
        evaluation_interval_seconds=60,
        aggressive_notional_lookback_rounds=6,
        minimum_aggressive_notional_samples=3,
        impulse_lookback_rounds=4,
        sustained_lookback_rounds=5,
        pullback_setup_maximum_rounds=10,
        signal_evidence_window_rounds=2,
        continuation_confirmation_rounds=1,
        position_opposition_confirmation_rounds=1,
    )

    assert limits.evaluation_interval_seconds == 60
    assert limits.impulse_lookback_rounds == 4
    assert limits.sustained_lookback_rounds == 5
    assert limits.continuation_confirmation_rounds == 1
    assert limits.position_opposition_confirmation_rounds == 1


def test_signal_episode_is_claimed_once_without_suppressing_a_new_signal() -> None:
    state: dict[str, Any] = {}

    assert _claim_signal_episode(state, "BTCUSDT", "LONG:CONTINUATION:10") is True
    assert _claim_signal_episode(state, "BTCUSDT", "LONG:CONTINUATION:10") is False
    assert _claim_signal_episode(state, "BTCUSDT", "LONG:PULLBACK:20") is True
    assert _claim_signal_episode(state, "ETHUSDT", "LONG:CONTINUATION:10") is True

    assert state["consumed_signal_episodes_by_symbol"] == {
        "BTCUSDT": "LONG:PULLBACK:20",
        "ETHUSDT": "LONG:CONTINUATION:10",
    }


def test_all_five_confirmed_symbols_launch_once_without_round_or_episode_quota() -> None:
    campaign = object.__new__(Campaign)
    campaign.symbols = baseline.TESTNET_EXPERIMENT_SYMBOLS
    campaign.limits = CampaignLimits(
        signal_confirmation_rounds=1,
        minimum_directional_forecast_bps=Decimal(0),
    )
    campaign.active_trades = {}
    campaign.protected_symbols = set()
    campaign.position_controls = {}
    campaign.position_directions = {}
    campaign.reversal_plans = {}
    launches: list[tuple[str, str]] = []
    campaign._dispatch_position_invalidations = lambda *_: None
    campaign._submit_reversal_plans = lambda *_: None
    campaign._append_event = lambda *_: None
    campaign._launch_experiment = (
        lambda _state, plan, _observed_at, *, submission_reason: launches.append(
            (plan.symbol, submission_reason)
        )
    )
    state: dict[str, Any] = {
        "evaluation_round_count": 1,
        "pending_signals": {},
        "daily_trade_count": 0,
        "daily_net_pnl": "0",
    }
    decisions = [
        _decision_for_rank(symbol, Direction.SHORT)
        for symbol in baseline.TESTNET_EXPERIMENT_SYMBOLS
    ]

    campaign._submit_experiments(state, decisions, decisions[0].observed_at)
    assert {symbol for symbol, _reason in launches} == set(baseline.TESTNET_EXPERIMENT_SYMBOLS)
    assert len(launches) == 5

    state["evaluation_round_count"] = 2
    campaign._submit_experiments(state, decisions, decisions[0].observed_at)
    assert len(launches) == 5


def test_campaign_can_run_continuously_and_rejects_short_nonzero_duration(
    tmp_path: Any,
) -> None:
    limits = CampaignLimits(duration_seconds=0)
    assert limits.duration_seconds == 0
    with pytest.raises(ValueError, match=r"zero \(continuous\)"):
        CampaignLimits(duration_seconds=1)

    campaign = object.__new__(Campaign)
    campaign.state_file = tmp_path / "state.json"
    campaign.symbols = baseline.TESTNET_EXPERIMENT_SYMBOLS
    campaign.limits = limits
    state = campaign._load_or_create_state()

    assert state["status"] == "RUNNING"
    assert state["ends_at"] is None
    assert state["limits"]["continuous_operation"] is True
    assert state["limits"]["duration_seconds"] == 0


def test_restart_recovery_requires_one_native_stop_and_target() -> None:
    stop = {
        "algoId": 10,
        "clientAlgoId": "aqa-t-exp-sl-stop",
        "algoStatus": "NEW",
        "closePosition": True,
    }
    target = {
        "algoId": 11,
        "clientAlgoId": "aqa-t-exp-tp-target",
        "algoStatus": "NEW",
        "closePosition": True,
    }

    assert _recovery_algo_pair([target, stop]) == (stop, target)
    assert _recovery_algo_pair([stop, dict(stop)]) == (None, None)
    assert _recovery_algo_pair([{**stop, "clientAlgoId": "external-sl", "algoStatus": "NEW"}]) == (
        None,
        None,
    )


def test_v4_campaign_rejects_a_different_symbol_universe() -> None:
    campaign = object.__new__(Campaign)
    with pytest.raises(ValueError, match="fixed V4 universe"):
        Campaign.__init__(
            campaign,
            api_key_file=None,  # type: ignore[arg-type]
            api_secret_file=None,  # type: ignore[arg-type]
            repository_root=None,  # type: ignore[arg-type]
            token_file=None,  # type: ignore[arg-type]
            chat_ids_file=None,  # type: ignore[arg-type]
            evidence_directory=None,  # type: ignore[arg-type]
            state_file=None,  # type: ignore[arg-type]
            symbols=("DOGEUSDT",),
            limits=CampaignLimits(),
        )


def test_testnet_baseline_accepts_only_fully_confirmed_long(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_time_ms = int(datetime(2026, 7, 14, 12, tzinfo=UTC).timestamp() * 1_000)
    long_frame = PriceActionFrame(
        as_of=datetime(2026, 7, 14, 12, tzinfo=UTC),
        regime=Regime.TREND_UP,
        structure=Structure.HH_HL,
        direction=Direction.LONG,
        atr=Decimal("0.2"),
        efficiency_ratio=Decimal("0.5"),
        reason_codes=(),
    )
    confirmed_flow = OrderFlowFrame(
        book_imbalance=Decimal("0.2"),
        microprice=Decimal("76.01"),
        microprice_mid_bps=Decimal("0.6"),
        trade_imbalance=Decimal("0.3"),
        aggressive_notional=Decimal("1000"),
        cvd_notional=Decimal("300"),
        valid=True,
        reason_codes=(),
    )
    monkeypatch.setattr(baseline, "analyze_price_action", lambda *args, **kwargs: long_frame)
    monkeypatch.setattr(baseline, "calculate_order_flow", lambda *args, **kwargs: confirmed_flow)

    decision = evaluate_testnet_baseline(
        symbol="SOLUSDT",
        server_time_ms=server_time_ms,
        one_minute_klines=_klines(server_time_ms, interval_ms=60_000),
        five_minute_klines=_klines(server_time_ms, interval_ms=300_000),
        depth={
            "bids": [[f"{76 - level / 100:.2f}", "100"] for level in range(20)],
            "asks": [[f"{76.01 + level / 100:.2f}", "100"] for level in range(20)],
        },
        aggregate_trades=[
            {
                "a": 1,
                "p": "76.01",
                "q": "1",
                "nq": "1",
                "f": 1,
                "l": 1,
                "T": server_time_ms - 100,
                "m": False,
            }
        ],
        signal_parameters=SignalParameters(minimum_target_feasibility_rate_15m=Decimal(0)),
    )

    assert decision.eligible
    assert not decision.execution_ready
    assert decision.direction is Direction.LONG
    assert decision.reason_codes == ()
    assert Decimal(str(decision.evidence()["mid_price"])) > 0
    assert Decimal(str(decision.evidence()["microprice"])) == confirmed_flow.microprice
    assert decision.evidence()["entry_verdict"] == "REJECT"
    assert decision.evidence()["execution_block_reason_codes"] == [
        "PA_SETUP_STATE_INCOMPLETE",
        "NET_EDGE_EVIDENCE_INCOMPLETE",
        "STRATEGY_EXIT_PLAN_INCOMPLETE",
    ]
    assert _select_candidate([decision]) is decision


def test_testnet_experiment_builds_structural_stop_without_time_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_time_ms = int(datetime(2026, 7, 14, 12, tzinfo=UTC).timestamp() * 1_000)
    long_frame = PriceActionFrame(
        as_of=datetime(2026, 7, 14, 12, tzinfo=UTC),
        regime=Regime.TREND_UP,
        structure=Structure.HH_HL,
        direction=Direction.LONG,
        atr=Decimal("0.2"),
        efficiency_ratio=Decimal("0.5"),
        reason_codes=(),
    )
    flow = OrderFlowFrame(
        book_imbalance=Decimal("0.2"),
        microprice=Decimal("76.005"),
        microprice_mid_bps=Decimal("0.6"),
        trade_imbalance=Decimal("0.4"),
        aggressive_notional=Decimal("1000"),
        cvd_notional=Decimal("400"),
        valid=True,
        reason_codes=(),
    )
    monkeypatch.setattr(baseline, "analyze_price_action", lambda *args, **kwargs: long_frame)
    monkeypatch.setattr(baseline, "calculate_order_flow", lambda *args, **kwargs: flow)
    one_minute = _klines(server_time_ms, interval_ms=60_000)
    for bar in one_minute[-6:]:
        bar[3] = "75.80"
    decision = evaluate_testnet_baseline(
        symbol="SOLUSDT",
        server_time_ms=server_time_ms,
        one_minute_klines=one_minute,
        five_minute_klines=_klines(server_time_ms, interval_ms=300_000),
        depth={
            "bids": [[f"{76 - level / 100:.2f}", "100"] for level in range(20)],
            "asks": [[f"{76.01 + level / 100:.2f}", "100"] for level in range(20)],
        },
        aggregate_trades=[
            {
                "a": 1,
                "p": "76.01",
                "q": "1",
                "nq": "1",
                "f": 1,
                "l": 1,
                "T": server_time_ms - 100,
                "m": False,
            }
        ],
        signal_parameters=SignalParameters(minimum_target_feasibility_rate_15m=Decimal(0)),
    )

    plan = decision.experimental_plan
    assert plan is not None
    assert plan.stop_anchor == Decimal("75.780")
    assert (plan.target_reference - plan.entry_reference) / plan.entry_reference * Decimal(
        10_000
    ) == Decimal("25")
    assert plan.strategy_version == "TESTNET_EXPERIMENT_OF_PA_V5_6"
    assert "maximum_holding" not in str(plan.evidence()).lower()


def test_v5_5_target_matches_fixed_symbol_execution_economics() -> None:
    assert gross_target_bps_for_symbol("BTCUSDT") == Decimal("22")
    assert gross_target_bps_for_symbol("ETHUSDT") == Decimal("22")
    assert gross_target_bps_for_symbol("BNBUSDT") == Decimal("25")
    assert gross_target_bps_for_symbol("SOLUSDT") == Decimal("25")
    assert gross_target_bps_for_symbol("XRPUSDT") == Decimal("25")
    with pytest.raises(ValueError, match="outside the fixed universe"):
        gross_target_bps_for_symbol("ADAUSDT")

    round_trip_fee_and_slippage_bps = Decimal("10")
    minimum_stop_bps = Decimal("60")
    minimum_reward_risk = Decimal("0.15")
    assert (gross_target_bps_for_symbol("BTCUSDT") - round_trip_fee_and_slippage_bps) / (
        minimum_stop_bps + round_trip_fee_and_slippage_bps
    ) >= minimum_reward_risk


def test_experimental_candidates_prefer_price_action_alignment() -> None:
    aligned = _decision_for_rank("BNBUSDT", Direction.SHORT)
    neutral = _decision_for_rank("XRPUSDT", Direction.NEUTRAL)

    assert _experimental_candidate_rank(aligned) < _experimental_candidate_rank(neutral)
    assert _money("0.08470919") == "0.084709"


def test_candidate_requires_pa_alignment_and_explicit_flow_thresholds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_time_ms = int(datetime(2026, 7, 14, 12, tzinfo=UTC).timestamp() * 1_000)
    neutral = PriceActionFrame(
        as_of=datetime(2026, 7, 14, 12, tzinfo=UTC),
        regime=Regime.TRANSITION,
        structure=Structure.UNCONFIRMED,
        direction=Direction.NEUTRAL,
        atr=Decimal("0.2"),
        efficiency_ratio=Decimal("0.5"),
        reason_codes=(),
    )
    flow = OrderFlowFrame(
        book_imbalance=Decimal("0.2"),
        microprice=Decimal("76.005"),
        microprice_mid_bps=Decimal("0.6"),
        trade_imbalance=Decimal("0.8"),
        aggressive_notional=Decimal("1000"),
        cvd_notional=Decimal("800"),
        valid=True,
        reason_codes=(),
    )
    monkeypatch.setattr(baseline, "analyze_price_action", lambda *args, **kwargs: neutral)
    monkeypatch.setattr(baseline, "calculate_order_flow", lambda *args, **kwargs: flow)

    decision = evaluate_testnet_baseline(
        symbol="SOLUSDT",
        server_time_ms=server_time_ms,
        one_minute_klines=_klines(server_time_ms, interval_ms=60_000),
        five_minute_klines=_klines(server_time_ms, interval_ms=300_000),
        depth={
            "bids": [[f"{76 - level / 100:.2f}", "100"] for level in range(20)],
            "asks": [[f"{76.01 + level / 100:.2f}", "100"] for level in range(20)],
        },
        aggregate_trades=[
            {
                "a": 1,
                "p": "76.01",
                "q": "1",
                "nq": "1",
                "f": 1,
                "l": 1,
                "T": server_time_ms - 100,
                "m": False,
            }
        ],
        signal_parameters=SignalParameters(),
    )

    assert decision.experimental_plan is None


def test_candidate_rejects_material_book_microstructure_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_time_ms = int(datetime(2026, 7, 14, 12, tzinfo=UTC).timestamp() * 1_000)
    short_frame = PriceActionFrame(
        as_of=datetime(2026, 7, 14, 12, tzinfo=UTC),
        regime=Regime.TREND_DOWN,
        structure=Structure.LH_LL,
        direction=Direction.SHORT,
        atr=Decimal("0.2"),
        efficiency_ratio=Decimal("0.5"),
        reason_codes=(),
    )
    conflicting_flow = OrderFlowFrame(
        book_imbalance=Decimal("0.16"),
        microprice=Decimal("76.005"),
        microprice_mid_bps=Decimal("-0.85"),
        trade_imbalance=Decimal("-1"),
        aggressive_notional=Decimal("1000"),
        cvd_notional=Decimal("-1000"),
        valid=True,
        reason_codes=(),
    )
    monkeypatch.setattr(baseline, "analyze_price_action", lambda *args, **kwargs: short_frame)
    monkeypatch.setattr(baseline, "calculate_order_flow", lambda *args, **kwargs: conflicting_flow)

    decision = evaluate_testnet_baseline(
        symbol="XRPUSDT",
        server_time_ms=server_time_ms,
        one_minute_klines=_klines(server_time_ms, interval_ms=60_000),
        five_minute_klines=_klines(server_time_ms, interval_ms=300_000),
        depth={
            "bids": [[f"{76 - level / 100:.2f}", "100"] for level in range(20)],
            "asks": [[f"{76.01 + level / 100:.2f}", "100"] for level in range(20)],
        },
        aggregate_trades=[
            {
                "a": 1,
                "p": "76.01",
                "q": "1",
                "nq": "1",
                "f": 1,
                "l": 1,
                "T": server_time_ms - 100,
                "m": True,
            }
        ],
    )

    assert decision.experimental_plan is None


def test_pending_signal_requires_two_consecutive_rounds_and_does_not_fill_slots() -> None:
    state: dict[str, Any] = {}
    decision = _decision_for_rank("BNBUSDT", Direction.SHORT)

    assert (
        _update_pending_signals(
            state,
            [decision],
            active_symbols=set(),
            evaluation_round=1,
            required_rounds=2,
            minimum_quality_score=Decimal("2"),
            activity_lookback_rounds=12,
            minimum_activity_samples=1,
            minimum_activity_ratio=Decimal("0.5"),
        )
        == []
    )
    assert _update_pending_signals(
        state,
        [decision],
        active_symbols=set(),
        evaluation_round=2,
        required_rounds=2,
        minimum_quality_score=Decimal("2"),
        activity_lookback_rounds=12,
        minimum_activity_samples=1,
        minimum_activity_ratio=Decimal("0.5"),
    ) == [decision]
    assert len(state["pending_signals"]) == 1


def test_confirmed_signal_can_reach_the_single_owner_of_a_protected_position() -> None:
    state: dict[str, Any] = {}
    decision = _decision_for_rank("BNBUSDT", Direction.SHORT)

    confirmed = _update_pending_signals(
        state,
        [decision],
        active_symbols={"BNBUSDT"},
        controllable_symbols={"BNBUSDT"},
        evaluation_round=1,
        required_rounds=1,
        minimum_quality_score=Decimal("2"),
        activity_lookback_rounds=12,
        minimum_activity_samples=1,
        minimum_activity_ratio=Decimal("0.5"),
    )

    assert confirmed == [decision]
    assert (
        state["last_confirmation_diagnostics"]["symbols"]["BNBUSDT"]["gate_result"] == "CONFIRMED"
    )


def test_pending_entry_remains_blocked_until_it_becomes_protected() -> None:
    state: dict[str, Any] = {}
    decision = _decision_for_rank("BNBUSDT", Direction.SHORT)

    confirmed = _update_pending_signals(
        state,
        [decision],
        active_symbols={"BNBUSDT"},
        controllable_symbols=set(),
        evaluation_round=1,
        required_rounds=1,
        minimum_quality_score=Decimal("2"),
        activity_lookback_rounds=12,
        minimum_activity_samples=1,
        minimum_activity_ratio=Decimal("0.5"),
    )

    assert confirmed == []
    assert (
        state["last_confirmation_diagnostics"]["symbols"]["BNBUSDT"]["gate_result"]
        == "ALREADY_IN_FLIGHT"
    )


@pytest.mark.parametrize(
    ("predictive_average", "expected_gate"),
    [
        ("100.10", "FORECAST_DIRECTION_CONFLICT"),
        ("99.98", "FORECAST_EDGE_INSUFFICIENT"),
    ],
)
def test_pending_signal_requires_a_directionally_aligned_forecast_edge(
    predictive_average: str, expected_gate: str
) -> None:
    state: dict[str, Any] = {}
    decision = _decision_for_rank("BNBUSDT", Direction.SHORT)
    assert decision.experimental_plan is not None
    plan = replace(
        decision.experimental_plan,
        predictive_average_20m=Decimal(predictive_average),
    )

    confirmed = _update_pending_signals(
        state,
        [replace(decision, experimental_plan=plan)],
        active_symbols=set(),
        evaluation_round=1,
        required_rounds=1,
        minimum_quality_score=Decimal("2"),
        minimum_directional_forecast_bps=Decimal("6"),
        activity_lookback_rounds=12,
        minimum_activity_samples=1,
        minimum_activity_ratio=Decimal("0.5"),
    )

    assert confirmed == []
    assert (
        state["last_confirmation_diagnostics"]["symbols"]["BNBUSDT"]["gate_result"] == expected_gate
    )


def test_pending_signal_rejects_activity_far_below_recent_median() -> None:
    state: dict[str, Any] = {
        "aggressive_notional_history": {"BNBUSDT": ["1000", "1200", "900", "1100", "950"]}
    }
    decision = _decision_for_rank("BNBUSDT", Direction.SHORT)
    low_flow = OrderFlowFrame(
        book_imbalance=decision.order_flow.book_imbalance,
        microprice=decision.order_flow.microprice,
        microprice_mid_bps=decision.order_flow.microprice_mid_bps,
        trade_imbalance=decision.order_flow.trade_imbalance,
        aggressive_notional=Decimal("25"),
        cvd_notional=Decimal("-25"),
        valid=True,
        reason_codes=(),
    )
    decision = baseline.TestnetBaselineDecision(
        eligible=decision.eligible,
        observed_at=decision.observed_at,
        symbol=decision.symbol,
        direction=decision.direction,
        pa_1m=decision.pa_1m,
        pa_5m=decision.pa_5m,
        order_flow=low_flow,
        spread_bps=decision.spread_bps,
        reason_codes=decision.reason_codes,
        experimental_plan=decision.experimental_plan,
    )

    assert (
        _update_pending_signals(
            state,
            [decision],
            active_symbols=set(),
            evaluation_round=1,
            required_rounds=2,
            minimum_quality_score=Decimal("2"),
            activity_lookback_rounds=12,
            minimum_activity_samples=6,
            minimum_activity_ratio=Decimal("0.5"),
        )
        == []
    )
    assert state["pending_signals"] == {}


def test_three_symbol_fast_breadth_arms_predictive_context_in_v5_5() -> None:
    state: dict[str, Any] = {
        "mid_price_history": {
            symbol: [{"evaluation_round": index, "mid_price": "100"} for index in range(1, 23)]
            for symbol in baseline.TESTNET_EXPERIMENT_SYMBOLS
        }
    }
    decisions = [
        _neutral_impulse_decision("BTCUSDT", "100.08"),
        _neutral_impulse_decision("ETHUSDT", "100.06"),
        _neutral_impulse_decision("BNBUSDT", "100.04"),
        _neutral_impulse_decision("SOLUSDT", "100.01"),
        _neutral_impulse_decision("XRPUSDT", "99.99"),
    ]

    promoted = _apply_market_impulse_plans(
        state, decisions, limits=CampaignLimits(), evaluation_round=23
    )

    plans = {
        decision.symbol: decision.experimental_plan
        for decision in promoted
        if decision.experimental_plan is not None
    }
    assert plans == {}
    assert state["last_signal_diagnostics"]["selected_setup"] == "MARKET_BREADTH_IMPULSE_FAST"
    assert state["last_signal_diagnostics"]["fast_long_breadth"] == 3
    assert set(state["pullback_setups"]) == {"BTCUSDT", "ETHUSDT", "BNBUSDT"}
    assert state["last_signal_diagnostics"]["required_context_breadth_count"] == 3


def test_three_symbol_fast_breadth_builds_continuation_after_confirmation() -> None:
    state: dict[str, Any] = {
        "mid_price_history": {
            symbol: [{"evaluation_round": index, "mid_price": "100"} for index in range(1, 23)]
            for symbol in baseline.TESTNET_EXPERIMENT_SYMBOLS
        }
    }
    promoted: list[baseline.TestnetBaselineDecision] = []
    for evaluation_round, price in enumerate(("100.04", "100.05", "100.06", "100.07"), 23):
        promoted = _apply_market_impulse_plans(
            state,
            [
                _neutral_impulse_decision(symbol, price if index < 3 else "100")
                for index, symbol in enumerate(baseline.TESTNET_EXPERIMENT_SYMBOLS)
            ],
            limits=CampaignLimits(),
            evaluation_round=evaluation_round,
        )

    plans = {
        decision.symbol: decision.experimental_plan
        for decision in promoted
        if decision.experimental_plan is not None
    }
    assert set(plans) == {"BTCUSDT", "ETHUSDT", "BNBUSDT"}
    assert all(plan.market_breadth_count == 3 for plan in plans.values())
    assert state["last_signal_diagnostics"]["continuation_confirmation_count"] == 4


def test_fast_breadth_waits_for_the_full_market_context_window() -> None:
    state: dict[str, Any] = {
        "mid_price_history": {
            symbol: [{"evaluation_round": index, "mid_price": "100"} for index in range(1, 5)]
            for symbol in baseline.TESTNET_EXPERIMENT_SYMBOLS
        }
    }
    decisions = [
        _neutral_impulse_decision("BTCUSDT", "100.08"),
        _neutral_impulse_decision("ETHUSDT", "100.06"),
        _neutral_impulse_decision("BNBUSDT", "100.04"),
        _neutral_impulse_decision("SOLUSDT", "100.01"),
        _neutral_impulse_decision("XRPUSDT", "99.99"),
    ]

    promoted = _apply_market_impulse_plans(
        state, decisions, limits=CampaignLimits(), evaluation_round=5
    )

    assert all(decision.experimental_plan is None for decision in promoted)
    assert state["last_signal_diagnostics"]["history_fully_warmed"] is False
    assert state["signal_gate_counts"]["INSUFFICIENT_HISTORY"] == 5


def test_four_symbol_fast_breadth_arms_without_chasing_the_impulse() -> None:
    state: dict[str, Any] = {
        "mid_price_history": {
            symbol: [{"evaluation_round": index, "mid_price": "100"} for index in range(1, 23)]
            for symbol in baseline.TESTNET_EXPERIMENT_SYMBOLS
        }
    }
    decisions = [
        _neutral_impulse_decision("BTCUSDT", "100.08"),
        _neutral_impulse_decision("ETHUSDT", "100.06"),
        _neutral_impulse_decision("BNBUSDT", "100.04"),
        _neutral_impulse_decision("SOLUSDT", "100.03"),
        _neutral_impulse_decision("XRPUSDT", "99.99"),
    ]

    promoted = _apply_market_impulse_plans(
        state, decisions, limits=CampaignLimits(), evaluation_round=23
    )
    plans = {
        decision.symbol: decision.experimental_plan
        for decision in promoted
        if decision.experimental_plan is not None
    }

    assert plans == {}
    assert set(state["pullback_setups"]) == {"BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"}
    assert state["last_signal_diagnostics"]["selected_setup"] == "MARKET_BREADTH_IMPULSE_FAST"
    assert all(
        state["last_signal_diagnostics"]["symbols"][symbol]["entry_state"] == "ARMED"
        for symbol in state["pullback_setups"]
    )


def test_v5_enters_only_after_pullback_and_price_action_resumption() -> None:
    state: dict[str, Any] = {
        "mid_price_history": {
            symbol: [{"evaluation_round": index, "mid_price": "100"} for index in range(1, 23)]
            for symbol in baseline.TESTNET_EXPERIMENT_SYMBOLS
        }
    }
    limits = CampaignLimits()

    armed = _apply_market_impulse_plans(
        state,
        [
            _neutral_impulse_decision(symbol, "100.04")
            for symbol in baseline.TESTNET_EXPERIMENT_SYMBOLS
        ],
        limits=limits,
        evaluation_round=23,
    )
    assert all(decision.experimental_plan is None for decision in armed)

    pulled_back = _apply_market_impulse_plans(
        state,
        [
            (
                _neutral_impulse_decision(symbol, "100.00")
                if symbol == "BTCUSDT"
                else replace(
                    _neutral_impulse_decision(symbol, "100.00"),
                    order_flow=replace(
                        _neutral_impulse_decision(symbol, "100.00").order_flow,
                        trade_imbalance=Decimal("-0.8"),
                        book_imbalance=Decimal("-0.2"),
                    ),
                )
            )
            for symbol in baseline.TESTNET_EXPERIMENT_SYMBOLS
        ],
        limits=limits,
        evaluation_round=24,
    )
    assert all(decision.experimental_plan is None for decision in pulled_back)
    assert state["pullback_setups"]["BTCUSDT"]["phase"] == "PULLBACK_OBSERVED"

    decisions = [
        _neutral_impulse_decision(symbol, "100.01")
        for symbol in baseline.TESTNET_EXPERIMENT_SYMBOLS
    ]
    decisions[0] = replace(
        decisions[0],
        pa_1m=_directional_frame(Direction.LONG),
    )
    decisions = [
        decision
        if decision.symbol == "BTCUSDT"
        else replace(
            decision,
            order_flow=replace(
                decision.order_flow,
                trade_imbalance=Decimal("-0.8"),
                book_imbalance=Decimal("-0.2"),
            ),
        )
        for decision in decisions
    ]
    resumed = _apply_market_impulse_plans(state, decisions, limits=limits, evaluation_round=25)
    assert all(decision.experimental_plan is None for decision in resumed)
    assert state["last_signal_diagnostics"]["symbols"]["BTCUSDT"]["gate_result"] == (
        "CURRENT_MARKET_CONTEXT_NOT_CONFIRMED"
    )

    decisions = [
        _neutral_impulse_decision(symbol, "100.04")
        for symbol in baseline.TESTNET_EXPERIMENT_SYMBOLS
    ]
    decisions[0] = replace(decisions[0], pa_1m=_directional_frame(Direction.LONG))
    decisions = [
        decision
        if decision.symbol == "BTCUSDT"
        else replace(
            decision,
            order_flow=replace(
                decision.order_flow,
                trade_imbalance=Decimal("-0.8"),
                book_imbalance=Decimal("-0.2"),
            ),
        )
        for decision in decisions
    ]
    resumed = _apply_market_impulse_plans(state, decisions, limits=limits, evaluation_round=26)
    plans = {
        decision.symbol: decision.experimental_plan
        for decision in resumed
        if decision.experimental_plan is not None
    }

    assert set(plans) == {"BTCUSDT"}
    assert plans["BTCUSDT"].setup_type == "MARKET_BREADTH_PULLBACK_RESUMPTION"
    assert plans["BTCUSDT"].market_breadth_count == 5
    assert state["last_signal_diagnostics"]["symbols"]["BTCUSDT"]["gate_result"] == "PLAN_GENERATED"
    assert "BTCUSDT" not in state["pullback_setups"]


def test_v5_2_latches_pullback_evidence_but_requires_current_market_context() -> None:
    state: dict[str, Any] = {
        "mid_price_history": {
            symbol: [{"evaluation_round": index, "mid_price": "100"} for index in range(1, 23)]
            for symbol in baseline.TESTNET_EXPERIMENT_SYMBOLS
        },
        "pullback_setups": {
            "BTCUSDT": {
                "direction": "LONG",
                "started_round": 22,
                "extreme_price": "100.04",
                "previous_mid_price": "100.00",
                "phase": "PULLBACK_OBSERVED",
                "pullback_observed_round": 22,
                "maximum_pullback_bps": "4",
                "market_breadth_count": 5,
                "market_momentum_bps": "4",
            }
        },
    }
    limits = CampaignLimits()

    first = _apply_market_impulse_plans(
        state,
        [
            _neutral_impulse_decision(symbol, "100.01")
            for symbol in baseline.TESTNET_EXPERIMENT_SYMBOLS
        ],
        limits=limits,
        evaluation_round=23,
    )
    assert first[0].experimental_plan is None
    setup = state["pullback_setups"]["BTCUSDT"]
    assert setup["price_confirmation_round"] == 23
    assert setup["flow_confirmation_round"] == 23
    assert "owner_confirmation_round" not in setup

    owner = replace(
        _neutral_impulse_decision("BTCUSDT", "100.04"),
        pa_1m=_directional_frame(Direction.LONG),
    )
    opposing_current_flow = replace(
        owner.order_flow,
        trade_imbalance=Decimal("-0.8"),
        book_imbalance=Decimal("-0.2"),
    )
    second = _apply_market_impulse_plans(
        state,
        [replace(owner, order_flow=opposing_current_flow)]
        + [
            _neutral_impulse_decision(symbol, "100.04")
            for symbol in baseline.TESTNET_EXPERIMENT_SYMBOLS
            if symbol != "BTCUSDT"
        ],
        limits=limits,
        evaluation_round=24,
    )

    assert second[0].experimental_plan is not None
    assert second[0].experimental_plan.setup_type == "MARKET_BREADTH_PULLBACK_RESUMPTION"
    assert second[0].experimental_plan.directional_trade_imbalance == Decimal("0.8")
    assert "BTCUSDT" not in state["pullback_setups"]


def test_v5_1_enters_a_confirmed_monotonic_continuation_without_pullback() -> None:
    state: dict[str, Any] = {
        "mid_price_history": {
            symbol: [{"evaluation_round": index, "mid_price": "100"} for index in range(1, 23)]
            for symbol in baseline.TESTNET_EXPERIMENT_SYMBOLS
        }
    }
    limits = CampaignLimits()
    promoted: list[baseline.TestnetBaselineDecision] = []
    for evaluation_round, price in enumerate(("100.04", "100.05", "100.06", "100.07"), 23):
        promoted = _apply_market_impulse_plans(
            state,
            [
                _neutral_impulse_decision(symbol, price)
                for symbol in baseline.TESTNET_EXPERIMENT_SYMBOLS
            ],
            limits=limits,
            evaluation_round=evaluation_round,
        )

    plans = {
        decision.symbol: decision.experimental_plan
        for decision in promoted
        if decision.experimental_plan is not None
    }
    assert set(plans) == set(baseline.TESTNET_EXPERIMENT_SYMBOLS)
    assert all(plan.setup_type == "MARKET_BREADTH_CONTINUATION" for plan in plans.values())
    diagnostics = state["last_signal_diagnostics"]
    assert diagnostics["continuation_confirmation_count"] == 4
    assert diagnostics["symbols"]["BTCUSDT"]["gate_result"] == ("CONTINUATION_PLAN_GENERATED")

    repeated = _apply_market_impulse_plans(
        state,
        [
            _neutral_impulse_decision(symbol, "100.08")
            for symbol in baseline.TESTNET_EXPERIMENT_SYMBOLS
        ],
        limits=limits,
        evaluation_round=27,
    )
    assert all(decision.experimental_plan is None for decision in repeated)


def test_breadth_diagnostics_identify_low_target_feasibility() -> None:
    state: dict[str, Any] = {
        "mid_price_history": {
            symbol: [{"evaluation_round": index, "mid_price": "100"} for index in range(1, 23)]
            for symbol in baseline.TESTNET_EXPERIMENT_SYMBOLS
        },
        "pullback_setups": {
            symbol: {
                "direction": "LONG",
                "started_round": 10,
                "extreme_price": "100.08",
                "previous_mid_price": "100.03",
                "phase": "PULLBACK_OBSERVED",
                "maximum_pullback_bps": "5",
            }
            for symbol in baseline.TESTNET_EXPERIMENT_SYMBOLS
        },
    }
    decisions = [
        replace(
            _neutral_impulse_decision(symbol, "100.04"),
            long_target_feasibility_rate_15m=Decimal("0.01"),
        )
        for symbol in baseline.TESTNET_EXPERIMENT_SYMBOLS
    ]

    promoted = _apply_market_impulse_plans(
        state, decisions, limits=CampaignLimits(), evaluation_round=23
    )

    assert all(decision.experimental_plan is None for decision in promoted)
    diagnostic = state["last_signal_diagnostics"]["symbols"]["BTCUSDT"]
    assert diagnostic["gate_result"] == "TARGET_FEASIBILITY_INSUFFICIENT"
    assert diagnostic["target_feasibility_rate_15m"] == "0.01"
    assert diagnostic["required_target_feasibility_rate_15m"] == "0.02"


@pytest.mark.parametrize(
    ("book_imbalance", "microprice_bps"),
    [("-0.20", "0.20"), ("0.20", "-0.40")],
)
def test_impulse_plan_rejects_materially_opposing_microstructure(
    book_imbalance: str, microprice_bps: str
) -> None:
    decision = _neutral_impulse_decision("BTCUSDT", "100.08")
    flow = replace(
        decision.order_flow,
        book_imbalance=Decimal(book_imbalance),
        microprice_mid_bps=Decimal(microprice_bps),
    )

    plan = baseline.build_market_impulse_plan(
        replace(decision, order_flow=flow),
        direction=Direction.LONG,
        momentum_bps=Decimal("8"),
        breadth_count=4,
        parameters=SignalParameters(),
    )

    assert plan is not None


def test_sustained_breadth_arms_but_does_not_enter_without_a_pullback() -> None:
    gradual = [
        "100",
        "100.01",
        "100.02",
        "100.03",
        "100.04",
        "100.05",
        "100.06",
        "100.075",
        "100.08",
        "100.085",
        "100.09",
    ]
    state: dict[str, Any] = {
        "mid_price_history": {
            symbol: [
                {"evaluation_round": index + 1, "mid_price": price}
                for index, price in enumerate(gradual)
            ]
            for symbol in baseline.TESTNET_EXPERIMENT_SYMBOLS
        }
    }
    decisions = [
        _neutral_impulse_decision("BTCUSDT", "100.075"),
        _neutral_impulse_decision("ETHUSDT", "100.20"),
        _neutral_impulse_decision("BNBUSDT", "100.075"),
        _neutral_impulse_decision("SOLUSDT", "100.075"),
        _neutral_impulse_decision("XRPUSDT", "100.075"),
    ]

    promoted = _apply_market_impulse_plans(
        state,
        decisions,
        limits=CampaignLimits(impulse_lookback_rounds=5, sustained_lookback_rounds=12),
        evaluation_round=12,
    )
    plans = {
        decision.symbol: decision.experimental_plan
        for decision in promoted
        if decision.experimental_plan is not None
    }

    assert plans == {}
    assert state["last_signal_diagnostics"]["selected_setup"] == "MARKET_BREADTH_TREND"
    assert state["last_signal_diagnostics"]["symbols"]["ETHUSDT"]["gate_result"] == (
        "SETUP_ARMED_WAITING_FOR_PULLBACK"
    )
    assert state["last_signal_diagnostics"]["symbols"]["BTCUSDT"]["gate_result"] == (
        "SETUP_ARMED_WAITING_FOR_PULLBACK"
    )


def test_sustained_breadth_takes_priority_when_it_is_broader_than_fast_move() -> None:
    gradual = [
        "100",
        "100.01",
        "100.02",
        "100.03",
        "100.04",
        "100.05",
        "100.06",
        "100.075",
        "100.08",
        "100.085",
        "100.09",
    ]
    state: dict[str, Any] = {
        "mid_price_history": {
            symbol: [
                {"evaluation_round": index + 1, "mid_price": price}
                for index, price in enumerate(gradual)
            ]
            for symbol in baseline.TESTNET_EXPERIMENT_SYMBOLS
        }
    }
    decisions = [
        _neutral_impulse_decision("BTCUSDT", "100.075"),
        _neutral_impulse_decision("ETHUSDT", "100.20"),
        _neutral_impulse_decision("BNBUSDT", "100.075"),
        _neutral_impulse_decision("SOLUSDT", "100.075"),
        _neutral_impulse_decision("XRPUSDT", "100.075"),
    ]
    decisions = [
        replace(
            decision,
            pa_1m=_directional_frame(Direction.LONG),
            pa_5m=_directional_frame(Direction.LONG),
        )
        for decision in decisions
    ]

    promoted = _apply_market_impulse_plans(
        state,
        decisions,
        limits=CampaignLimits(impulse_lookback_rounds=5, sustained_lookback_rounds=12),
        evaluation_round=12,
    )
    plans = {
        decision.symbol: decision.experimental_plan
        for decision in promoted
        if decision.experimental_plan is not None
    }

    assert state["last_signal_diagnostics"]["selected_setup"] == "MARKET_BREADTH_TREND"
    assert plans == {}
    assert set(state["pullback_setups"]) == set(baseline.TESTNET_EXPERIMENT_SYMBOLS)


def test_established_sustained_breadth_beats_equal_fast_breadth() -> None:
    prices = ["100", "100.02", "100.04", "100.06", "100.08", "100.10", "100.12"]
    state: dict[str, Any] = {
        "mid_price_history": {
            symbol: [
                {"evaluation_round": index + 1, "mid_price": price}
                for index, price in enumerate(prices)
            ]
            for symbol in baseline.TESTNET_EXPERIMENT_SYMBOLS
        }
    }
    decisions = [
        replace(
            _neutral_impulse_decision(symbol, "100.20"),
            pa_1m=_directional_frame(Direction.LONG),
            pa_5m=_directional_frame(Direction.LONG),
        )
        for symbol in baseline.TESTNET_EXPERIMENT_SYMBOLS
    ]

    promoted = _apply_market_impulse_plans(
        state,
        decisions,
        limits=CampaignLimits(impulse_lookback_rounds=4, sustained_lookback_rounds=8),
        evaluation_round=8,
    )
    plans = [decision.experimental_plan for decision in promoted]

    diagnostics = state["last_signal_diagnostics"]
    assert diagnostics["fast_long_breadth"] == 5
    assert diagnostics["sustained_long_breadth"] == 5
    assert diagnostics["sustained_is_established"] is True
    assert diagnostics["selected_setup"] == "MARKET_BREADTH_TREND"
    assert all(plan is None for plan in plans)
    assert set(state["pullback_setups"]) == set(baseline.TESTNET_EXPERIMENT_SYMBOLS)


def test_opposing_market_context_discards_an_existing_plan() -> None:
    state: dict[str, Any] = {
        "mid_price_history": {
            symbol: [{"evaluation_round": index, "mid_price": "100.10"} for index in range(1, 12)]
            for symbol in baseline.TESTNET_EXPERIMENT_SYMBOLS
        }
    }
    decisions = [
        _neutral_impulse_decision(symbol, "100") for symbol in baseline.TESTNET_EXPERIMENT_SYMBOLS
    ]
    long_plan = baseline.TestnetExperimentalPlan(
        symbol="BTCUSDT",
        direction=Direction.LONG,
        entry_reference=Decimal("100"),
        stop_anchor=Decimal("99.5"),
        target_reference=Decimal("100.3"),
        predictive_average_20m=Decimal("100.1"),
        signal_quality_score=Decimal("5"),
    )
    decisions[0] = replace(decisions[0], experimental_plan=long_plan)

    promoted = _apply_market_impulse_plans(
        state, decisions, limits=CampaignLimits(), evaluation_round=12
    )

    btc = next(decision for decision in promoted if decision.symbol == "BTCUSDT")
    assert btc.experimental_plan is None
    diagnostic = state["last_signal_diagnostics"]["symbols"]["BTCUSDT"]
    assert diagnostic["old_direct_plan_disabled"] is True


def test_hourly_opposition_vetoes_a_fast_intraday_entry() -> None:
    decision = _neutral_impulse_decision("BTCUSDT", "100.05")
    decision = replace(decision, pa_1h=_directional_frame(Direction.SHORT))

    plan = baseline.build_market_impulse_plan(
        decision,
        direction=Direction.LONG,
        momentum_bps=Decimal("5"),
        breadth_count=4,
        parameters=SignalParameters(minimum_target_feasibility_rate_15m=Decimal("0.02")),
    )

    assert plan is None


def test_target_hit_strong_trend_can_reenter_without_symbol_cooldown() -> None:
    plan = baseline.TestnetExperimentalPlan(
        symbol="BTCUSDT",
        direction=Direction.LONG,
        entry_reference=Decimal("100"),
        stop_anchor=Decimal("99.5"),
        target_reference=Decimal("100.3"),
        setup_type="MARKET_BREADTH_IMPULSE_FAST",
        market_breadth_count=4,
    )
    state = {
        "last_exit_reason_by_symbol": {"BTCUSDT": "TAKE_PROFIT"},
        "last_completed_direction_by_symbol": {"BTCUSDT": "LONG"},
    }

    assert _trend_continuation_cooldown_waived(state, plan) is True
    assert (
        _trend_continuation_cooldown_waived(state, replace(plan, market_breadth_count=3)) is False
    )
    assert (
        _trend_continuation_cooldown_waived(state, replace(plan, direction=Direction.SHORT))
        is False
    )
    assert (
        _trend_continuation_cooldown_waived(
            {**state, "last_exit_reason_by_symbol": {"BTCUSDT": "STOP_LOSS"}}, plan
        )
        is False
    )


def test_impulse_pending_uses_two_rounds_without_forcing_slot_fill() -> None:
    state: dict[str, Any] = {
        "aggressive_notional_history": {"BTCUSDT": ["500", "500", "500", "500", "500"]}
    }
    decision = _neutral_impulse_decision("BTCUSDT", "100.08")
    plan = baseline.build_market_impulse_plan(
        decision,
        direction=Direction.LONG,
        momentum_bps=Decimal("8"),
        breadth_count=4,
        parameters=SignalParameters(),
    )
    assert plan is not None
    plan = replace(plan, pa_alignment_count=1)
    decision = replace(decision, experimental_plan=plan)

    first = _update_pending_signals(
        state,
        [decision],
        active_symbols=set(),
        evaluation_round=1,
        required_rounds=3,
        minimum_quality_score=Decimal("2"),
        activity_lookback_rounds=12,
        minimum_activity_samples=6,
        minimum_activity_ratio=Decimal("2"),
        impulse_required_rounds=2,
        impulse_minimum_activity_ratio=Decimal("1.25"),
    )
    first_gate = state["last_confirmation_diagnostics"]["symbols"]["BTCUSDT"]["gate_result"]
    second = _update_pending_signals(
        state,
        [decision],
        active_symbols=set(),
        evaluation_round=2,
        required_rounds=3,
        minimum_quality_score=Decimal("2"),
        activity_lookback_rounds=12,
        minimum_activity_samples=6,
        minimum_activity_ratio=Decimal("2"),
        impulse_required_rounds=2,
        impulse_minimum_activity_ratio=Decimal("1.25"),
    )
    assert first == []
    assert first_gate == "WAITING_CONFIRMATION"
    assert len(second) == 1
    second_plan = second[0].experimental_plan
    assert second_plan is not None
    assert second_plan.minimum_directional_forecast_bps == Decimal("1.00")
    assert state["last_confirmation_diagnostics"]["confirmed_count"] == 1
    assert (
        state["last_confirmation_diagnostics"]["symbols"]["BTCUSDT"]["gate_result"] == "CONFIRMED"
    )


def test_v5_4_rejects_weak_forecast_without_price_structure_alignment() -> None:
    decision = _neutral_impulse_decision("BTCUSDT", "100")
    plan = baseline.build_market_impulse_plan(
        decision,
        direction=Direction.LONG,
        momentum_bps=Decimal("6"),
        breadth_count=5,
        parameters=SignalParameters(),
    )
    assert plan is not None
    plan = replace(plan, predictive_average_20m=Decimal("100.01"))
    state: dict[str, Any] = {}

    confirmed = _update_pending_signals(
        state,
        [replace(decision, experimental_plan=plan)],
        active_symbols=set(),
        evaluation_round=1,
        required_rounds=1,
        minimum_quality_score=Decimal("2"),
        impulse_minimum_directional_forecast_bps=Decimal("0.10"),
        activity_lookback_rounds=12,
        minimum_activity_samples=1,
        minimum_activity_ratio=Decimal("0.10"),
        activity_filter_enabled=False,
        impulse_required_rounds=1,
        impulse_activity_filter_enabled=False,
    )

    assert confirmed == []
    assert (
        state["last_confirmation_diagnostics"]["symbols"]["BTCUSDT"]["gate_result"]
        == "PA_ALIGNMENT_INSUFFICIENT"
    )


def test_v5_5_continuation_requires_more_forecast_edge_than_a_pullback() -> None:
    decision = _neutral_impulse_decision("XRPUSDT", "100")
    plan = baseline.build_market_impulse_plan(
        decision,
        direction=Direction.LONG,
        momentum_bps=Decimal("5"),
        breadth_count=4,
        parameters=SignalParameters(),
        setup_type="MARKET_BREADTH_CONTINUATION",
    )
    assert plan is not None
    plan = replace(
        plan,
        predictive_average_20m=Decimal("100.015"),
        pa_alignment_count=1,
    )
    state: dict[str, Any] = {}

    confirmed = _update_pending_signals(
        state,
        [replace(decision, experimental_plan=plan)],
        active_symbols=set(),
        evaluation_round=1,
        required_rounds=1,
        minimum_quality_score=Decimal("2"),
        impulse_minimum_directional_forecast_bps=Decimal("0.10"),
        continuation_minimum_directional_forecast_bps=Decimal("2.00"),
        activity_lookback_rounds=12,
        minimum_activity_samples=1,
        minimum_activity_ratio=Decimal("0.10"),
        activity_filter_enabled=False,
        impulse_required_rounds=1,
        impulse_activity_filter_enabled=False,
    )

    assert confirmed == []
    assert (
        state["last_confirmation_diagnostics"]["symbols"]["XRPUSDT"]["gate_result"]
        == "FORECAST_EDGE_INSUFFICIENT"
    )


def test_v5_5_strong_forecast_and_flow_can_substitute_for_lagging_pa() -> None:
    decision = _neutral_impulse_decision("XRPUSDT", "100")
    plan = baseline.build_market_impulse_plan(
        decision,
        direction=Direction.LONG,
        momentum_bps=Decimal("5"),
        breadth_count=4,
        parameters=SignalParameters(),
        setup_type="MARKET_BREADTH_CONTINUATION",
    )
    assert plan is not None
    plan = replace(plan, predictive_average_20m=Decimal("100.04"), pa_alignment_count=0)
    state: dict[str, Any] = {}

    confirmed = _update_pending_signals(
        state,
        [replace(decision, experimental_plan=plan)],
        active_symbols=set(),
        evaluation_round=1,
        required_rounds=1,
        minimum_quality_score=Decimal("2"),
        impulse_minimum_directional_forecast_bps=Decimal("0.10"),
        continuation_minimum_directional_forecast_bps=Decimal("2.00"),
        structure_substitute_minimum_directional_forecast_bps=Decimal("3.00"),
        structure_substitute_minimum_trade_imbalance=Decimal("0.75"),
        structure_substitute_minimum_secondary_flow=Decimal("0.10"),
        activity_lookback_rounds=12,
        minimum_activity_samples=1,
        minimum_activity_ratio=Decimal("0.10"),
        activity_filter_enabled=False,
        impulse_required_rounds=1,
        impulse_activity_filter_enabled=False,
    )

    assert len(confirmed) == 1
    diagnostic = state["last_confirmation_diagnostics"]["symbols"]["XRPUSDT"]
    assert diagnostic["structure_substitute_qualified"] is True
    assert diagnostic["structure_authority"] == "FORECAST_AND_FLOW_SUBSTITUTE"


def test_v5_5_three_coin_fast_breadth_requires_strong_predictive_flow_authority() -> None:
    decision = _neutral_impulse_decision("XRPUSDT", "100")
    plan = baseline.build_market_impulse_plan(
        decision,
        direction=Direction.LONG,
        momentum_bps=Decimal("5"),
        breadth_count=3,
        parameters=SignalParameters(),
        setup_type="MARKET_BREADTH_CONTINUATION",
    )
    assert plan is not None
    plan = replace(
        plan,
        predictive_average_20m=Decimal("100.025"),
        pa_alignment_count=1,
    )
    state: dict[str, Any] = {}

    confirmed = _update_pending_signals(
        state,
        [replace(decision, experimental_plan=plan)],
        active_symbols=set(),
        evaluation_round=1,
        required_rounds=1,
        minimum_quality_score=Decimal("2"),
        impulse_minimum_directional_forecast_bps=Decimal("0.10"),
        continuation_minimum_directional_forecast_bps=Decimal("2.00"),
        activity_lookback_rounds=12,
        minimum_activity_samples=1,
        minimum_activity_ratio=Decimal("0.10"),
        activity_filter_enabled=False,
        impulse_required_rounds=1,
        impulse_activity_filter_enabled=False,
    )

    assert confirmed == []
    diagnostic = state["last_confirmation_diagnostics"]["symbols"]["XRPUSDT"]
    assert diagnostic["gate_result"] == "FAST_BREADTH_AUTHORITY_INSUFFICIENT"
    assert diagnostic["limited_fast_breadth"] is True


def test_v5_5_three_coin_fast_breadth_accepts_strong_predictive_flow_authority() -> None:
    decision = _neutral_impulse_decision("XRPUSDT", "100")
    plan = baseline.build_market_impulse_plan(
        decision,
        direction=Direction.LONG,
        momentum_bps=Decimal("5"),
        breadth_count=3,
        parameters=SignalParameters(),
        setup_type="MARKET_BREADTH_CONTINUATION",
    )
    assert plan is not None
    plan = replace(plan, predictive_average_20m=Decimal("100.04"), pa_alignment_count=0)
    state: dict[str, Any] = {}

    confirmed = _update_pending_signals(
        state,
        [replace(decision, experimental_plan=plan)],
        active_symbols=set(),
        evaluation_round=1,
        required_rounds=1,
        minimum_quality_score=Decimal("2"),
        impulse_minimum_directional_forecast_bps=Decimal("0.10"),
        continuation_minimum_directional_forecast_bps=Decimal("2.00"),
        activity_lookback_rounds=12,
        minimum_activity_samples=1,
        minimum_activity_ratio=Decimal("0.10"),
        activity_filter_enabled=False,
        impulse_required_rounds=1,
        impulse_activity_filter_enabled=False,
    )

    assert len(confirmed) == 1
    diagnostic = state["last_confirmation_diagnostics"]["symbols"]["XRPUSDT"]
    assert diagnostic["structure_substitute_qualified"] is True
    assert diagnostic["structure_authority"] == "FORECAST_AND_FLOW_SUBSTITUTE"


def test_fast_impulse_uses_small_aligned_forecast_and_rejects_activity_tail() -> None:
    decision = _neutral_impulse_decision("ETHUSDT", "100")
    plan = baseline.build_market_impulse_plan(
        decision,
        direction=Direction.LONG,
        momentum_bps=Decimal("3"),
        breadth_count=4,
        parameters=SignalParameters(minimum_target_feasibility_rate_15m=Decimal("0.02")),
    )
    assert plan is not None
    plan = replace(plan, predictive_average_20m=Decimal("100.002"), pa_alignment_count=1)
    decision = replace(decision, experimental_plan=plan)
    state: dict[str, Any] = {
        "aggressive_notional_history": {"ETHUSDT": ["10", "10", "10", "10", "10"]}
    }

    confirmed = _update_pending_signals(
        state,
        [decision],
        active_symbols=set(),
        evaluation_round=1,
        required_rounds=3,
        minimum_quality_score=Decimal("2"),
        minimum_directional_forecast_bps=Decimal("6"),
        impulse_minimum_directional_forecast_bps=Decimal("0.10"),
        activity_lookback_rounds=12,
        minimum_activity_samples=6,
        minimum_activity_ratio=Decimal("2"),
        impulse_required_rounds=1,
        impulse_minimum_activity_ratio=Decimal("0.10"),
        impulse_maximum_activity_ratio=Decimal("10"),
    )

    assert confirmed == []
    diagnostic = state["last_confirmation_diagnostics"]["symbols"]["ETHUSDT"]
    assert diagnostic["directional_forecast_bps"] == "0.20000"
    assert diagnostic["required_directional_forecast_bps"] == "0.10"
    assert diagnostic["gate_result"] == "ACTIVITY_RATIO_EXCESSIVE"


def test_fast_impulse_can_treat_testnet_activity_amount_as_diagnostic_only() -> None:
    decision = _neutral_impulse_decision("ETHUSDT", "100")
    plan = baseline.build_market_impulse_plan(
        decision,
        direction=Direction.LONG,
        momentum_bps=Decimal("7.66"),
        breadth_count=4,
        parameters=SignalParameters(minimum_target_feasibility_rate_15m=Decimal("0.02")),
    )
    assert plan is not None
    plan = replace(plan, predictive_average_20m=Decimal("100.05"), pa_alignment_count=1)
    decision = replace(decision, experimental_plan=plan)
    state: dict[str, Any] = {"aggressive_notional_history": {"ETHUSDT": ["1000000"] * 5}}

    confirmed = _update_pending_signals(
        state,
        [decision],
        active_symbols=set(),
        evaluation_round=1,
        required_rounds=3,
        minimum_quality_score=Decimal("2"),
        minimum_directional_forecast_bps=Decimal("6"),
        impulse_minimum_directional_forecast_bps=Decimal("0.10"),
        activity_lookback_rounds=12,
        minimum_activity_samples=6,
        minimum_activity_ratio=Decimal("2"),
        impulse_required_rounds=1,
        impulse_minimum_activity_ratio=Decimal("0.10"),
        impulse_maximum_activity_ratio=Decimal("10"),
        impulse_activity_filter_enabled=False,
    )

    assert len(confirmed) == 1
    diagnostic = state["last_confirmation_diagnostics"]["symbols"]["ETHUSDT"]
    assert diagnostic["activity_filter_enabled"] is False
    assert Decimal(str(diagnostic["activity_ratio"])) < Decimal("0.01")
    assert diagnostic["gate_result"] == "CONFIRMED"


def test_opposing_breadth_and_local_momentum_invalidate_without_reversing() -> None:
    decision = _neutral_impulse_decision("SOLUSDT", "77.355")
    state: dict[str, Any] = {
        "last_signal_diagnostics": {
            "fast_long_breadth": 4,
            "fast_short_breadth": 0,
            "sustained_long_breadth": 4,
            "sustained_short_breadth": 0,
            "symbols": {
                "SOLUSDT": {
                    "fast_momentum_bps": "5.82",
                    "sustained_momentum_bps": "6.00",
                }
            },
        }
    }

    plans = _position_invalidation_plans(
        state,
        [decision],
        position_directions={"SOLUSDT": Direction.SHORT},
        limits=CampaignLimits(),
    )

    assert len(plans) == 1
    plan = plans[0]
    assert plan.direction is Direction.LONG
    assert plan.exit_only is True
    assert plan.setup_type == "MARKET_BREADTH_TREND_POSITION_INVALIDATION"
    assert plan.market_breadth_count == 4
    assert plan.market_momentum_bps == Decimal("6.00")


def test_five_coin_fast_reversal_exits_after_four_confirmations() -> None:
    decision = _neutral_impulse_decision("SOLUSDT", "77.35")
    decision = replace(
        decision,
        order_flow=replace(decision.order_flow, trade_imbalance=Decimal("-0.8")),
    )
    state: dict[str, Any] = {
        "market_context_confirmation": {
            "direction": "SHORT",
            "consecutive_rounds": 4,
        },
        "last_signal_diagnostics": {
            "fast_long_breadth": 0,
            "fast_short_breadth": 5,
            "sustained_long_breadth": 0,
            "sustained_short_breadth": 2,
            "symbols": {
                "SOLUSDT": {
                    "fast_momentum_bps": "-5.20",
                    "sustained_momentum_bps": "-3.00",
                }
            },
        },
    }

    plans = _position_invalidation_plans(
        state,
        [decision],
        position_directions={"SOLUSDT": Direction.LONG},
        limits=CampaignLimits(),
    )

    assert len(plans) == 1
    assert plans[0].exit_only is True
    assert plans[0].setup_type == "MARKET_BREADTH_FAST_POSITION_INVALIDATION"


def test_failed_followthrough_exits_after_two_opposing_flow_rounds() -> None:
    state: dict[str, Any] = {
        "evaluation_round_count": 10,
        "active_position_metrics": {
            "XRPUSDT": {
                "direction": "SHORT",
                "entry_price": "100",
                "peak_favorable_bps": "0",
                "current_favorable_bps": "0",
                "opposing_flow_rounds": 0,
                "last_evaluation_round": -1,
            }
        },
        "last_signal_diagnostics": {
            "fast_long_breadth": 0,
            "fast_short_breadth": 0,
            "sustained_long_breadth": 0,
            "sustained_short_breadth": 0,
            "symbols": {
                "XRPUSDT": {
                    "fast_momentum_bps": "0",
                    "sustained_momentum_bps": "0",
                }
            },
        },
    }
    limits = CampaignLimits()
    first = _neutral_impulse_decision("XRPUSDT", "99.94")

    assert (
        _position_invalidation_plans(
            state,
            [first],
            position_directions={"XRPUSDT": Direction.SHORT},
            limits=limits,
        )
        == []
    )

    state["evaluation_round_count"] = 11
    second = _neutral_impulse_decision("XRPUSDT", "100.01")
    plans = _position_invalidation_plans(
        state,
        [second],
        position_directions={"XRPUSDT": Direction.SHORT},
        limits=limits,
    )

    assert len(plans) == 1
    assert plans[0].exit_only is True
    assert plans[0].setup_type == "LOCAL_FOLLOWTHROUGH_POSITION_INVALIDATION"
    metric = state["active_position_metrics"]["XRPUSDT"]
    assert Decimal(str(metric["peak_favorable_bps"])) >= Decimal("6")
    assert metric["opposing_flow_rounds"] == 2


def test_confirmed_fast_plan_carries_its_forecast_threshold_into_execution() -> None:
    decision = _neutral_impulse_decision("ETHUSDT", "100")
    plan = baseline.build_market_impulse_plan(
        decision,
        direction=Direction.LONG,
        momentum_bps=Decimal("3"),
        breadth_count=4,
        parameters=SignalParameters(minimum_target_feasibility_rate_15m=Decimal("0.02")),
    )
    assert plan is not None
    plan = replace(plan, predictive_average_20m=Decimal("100.002"), pa_alignment_count=1)
    decision = replace(decision, experimental_plan=plan)
    state: dict[str, Any] = {
        "aggressive_notional_history": {"ETHUSDT": ["1000", "1000", "1000", "1000", "1000"]}
    }

    confirmed = _update_pending_signals(
        state,
        [decision],
        active_symbols=set(),
        evaluation_round=1,
        required_rounds=3,
        minimum_quality_score=Decimal("2"),
        minimum_directional_forecast_bps=Decimal("6"),
        impulse_minimum_directional_forecast_bps=Decimal("0.10"),
        activity_lookback_rounds=12,
        minimum_activity_samples=6,
        minimum_activity_ratio=Decimal("2"),
        impulse_required_rounds=1,
        impulse_minimum_activity_ratio=Decimal("0.10"),
        impulse_maximum_activity_ratio=Decimal("10"),
    )

    assert len(confirmed) == 1
    confirmed_plan = confirmed[0].experimental_plan
    assert confirmed_plan is not None
    assert confirmed_plan.minimum_directional_forecast_bps == Decimal("0.10")


def test_four_coin_breadth_cannot_override_a_regression_direction_conflict() -> None:
    decision = _neutral_impulse_decision("ETHUSDT", "100")
    decision = replace(
        decision,
        order_flow=replace(
            decision.order_flow,
            trade_imbalance=Decimal("-0.8"),
            book_imbalance=Decimal("-0.2"),
            microprice_mid_bps=Decimal("-0.2"),
            cvd_notional=Decimal("-800"),
        ),
    )
    plan = baseline.build_market_impulse_plan(
        decision,
        direction=Direction.SHORT,
        momentum_bps=Decimal("-4.33"),
        breadth_count=4,
        parameters=SignalParameters(minimum_target_feasibility_rate_15m=Decimal("0.02")),
    )
    assert plan is not None
    plan = replace(plan, predictive_average_20m=Decimal("100.05"), pa_alignment_count=1)
    decision = replace(decision, experimental_plan=plan)
    state: dict[str, Any] = {}

    confirmed = _update_pending_signals(
        state,
        [decision],
        active_symbols=set(),
        evaluation_round=1,
        required_rounds=3,
        minimum_quality_score=Decimal("2"),
        minimum_directional_forecast_bps=Decimal("2"),
        impulse_minimum_directional_forecast_bps=Decimal("0.10"),
        activity_lookback_rounds=12,
        minimum_activity_samples=6,
        minimum_activity_ratio=Decimal("2"),
        activity_filter_enabled=False,
        impulse_required_rounds=1,
        impulse_minimum_activity_ratio=Decimal("0.10"),
        impulse_maximum_activity_ratio=Decimal("10"),
        impulse_activity_filter_enabled=False,
    )

    assert confirmed == []
    diagnostic = state["last_confirmation_diagnostics"]["symbols"]["ETHUSDT"]
    assert Decimal(str(diagnostic["directional_forecast_bps"])) < 0
    assert diagnostic["forecast_filter_enabled"] is True
    assert diagnostic["gate_result"] == "FORECAST_DIRECTION_CONFLICT"


def test_campaign_summary_translates_runtime_and_reason_codes_to_chinese() -> None:
    message = _summary_text(
        {
            "status": "RUNNING",
            "observation_count": 3,
            "trade_count": 0,
            "target_hit_count": 0,
            "cumulative_net_pnl": "0",
            "last_reason_codes": ["PA_1M_NOT_LONG", "SPREAD_TOO_WIDE"],
        }
    )

    assert "运行状态: 运行中" in message
    assert "已完成平仓: 0 单" in message
    assert "1 分钟 PA 未形成多头趋势" in message
    assert "买卖点差过宽" in message
    assert "PA_1M_NOT_LONG" not in message


def _klines(server_time_ms: int, *, interval_ms: int) -> list[list[object]]:
    first_open = server_time_ms - interval_ms * 120
    return [
        [
            first_open + index * interval_ms,
            "76",
            "76.10",
            "75.90",
            "76",
            "100",
            first_open + (index + 1) * interval_ms - 1,
            "7600",
            10,
            "50",
            "3800",
            "0",
        ]
        for index in range(120)
    ]


def _decision_for_rank(symbol: str, pa_direction: Direction) -> baseline.TestnetBaselineDecision:
    now = datetime(2026, 7, 14, 12, tzinfo=UTC)
    frame = PriceActionFrame(
        as_of=now,
        regime=Regime.TREND_DOWN if pa_direction is Direction.SHORT else Regime.TRANSITION,
        structure=Structure.LH_LL if pa_direction is Direction.SHORT else Structure.UNCONFIRMED,
        direction=pa_direction,
        atr=Decimal("0.2"),
        efficiency_ratio=Decimal("0.5"),
        reason_codes=(),
    )
    flow = OrderFlowFrame(
        book_imbalance=Decimal("-0.1"),
        microprice=Decimal("100"),
        microprice_mid_bps=Decimal("-0.2"),
        trade_imbalance=Decimal("-0.8"),
        aggressive_notional=Decimal("1000"),
        cvd_notional=Decimal("-800"),
        valid=True,
        reason_codes=(),
    )
    return baseline.TestnetBaselineDecision(
        eligible=False,
        observed_at=now,
        symbol=symbol,
        direction=Direction.NEUTRAL,
        pa_1m=frame,
        pa_5m=frame,
        order_flow=flow,
        spread_bps=Decimal("1"),
        reason_codes=(),
        experimental_plan=baseline.TestnetExperimentalPlan(
            symbol=symbol,
            direction=Direction.SHORT,
            entry_reference=Decimal("100"),
            stop_anchor=Decimal("100.3"),
            target_reference=Decimal("99.65"),
            signal_quality_score=(
                Decimal("4") if pa_direction is Direction.SHORT else Decimal("0.5")
            ),
            pa_alignment_count=1 if pa_direction is Direction.SHORT else 0,
            target_feasibility_rate_15m=Decimal(1),
        ),
    )


def _directional_frame(direction: Direction) -> PriceActionFrame:
    now = datetime(2026, 7, 14, 12, tzinfo=UTC)
    return PriceActionFrame(
        as_of=now,
        regime=Regime.TREND_UP if direction is Direction.LONG else Regime.TREND_DOWN,
        structure=Structure.HH_HL if direction is Direction.LONG else Structure.LH_LL,
        direction=direction,
        atr=Decimal("0.2"),
        efficiency_ratio=Decimal("0.5"),
        reason_codes=(),
    )


def _neutral_impulse_decision(symbol: str, mid: str) -> baseline.TestnetBaselineDecision:
    now = datetime(2026, 7, 14, 12, tzinfo=UTC)
    frame = PriceActionFrame(
        as_of=now,
        regime=Regime.TRANSITION,
        structure=Structure.UNCONFIRMED,
        direction=Direction.NEUTRAL,
        atr=Decimal("0.2"),
        efficiency_ratio=Decimal("0.2"),
        reason_codes=("PA_DIRECTION_NEUTRAL",),
    )
    mid_value = Decimal(mid)
    flow = OrderFlowFrame(
        book_imbalance=Decimal("0.2"),
        microprice=mid_value,
        microprice_mid_bps=Decimal("0"),
        trade_imbalance=Decimal("0.8"),
        aggressive_notional=Decimal("1000"),
        cvd_notional=Decimal("800"),
        valid=True,
        reason_codes=(),
    )
    return baseline.TestnetBaselineDecision(
        eligible=False,
        observed_at=now,
        symbol=symbol,
        direction=Direction.NEUTRAL,
        pa_1m=frame,
        pa_5m=frame,
        order_flow=flow,
        spread_bps=Decimal("1"),
        reason_codes=("PA_1M_NOT_LONG", "PA_5M_NOT_LONG"),
        recent_low=mid_value * Decimal("0.999"),
        recent_high=mid_value * Decimal("1.001"),
        predictive_average_20m=mid_value,
        long_target_feasibility_rate_15m=Decimal(1),
        short_target_feasibility_rate_15m=Decimal(1),
    )
