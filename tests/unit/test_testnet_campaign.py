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
    _experimental_candidate_rank,
    _money,
    _select_candidate,
    _summary_text,
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


def test_campaign_limits_enforce_cooldown_count_and_daily_loss() -> None:
    limits = CampaignLimits()
    assert limits.maximum_parallel_positions == 5
    assert limits.maximum_candidates_per_round == 3
    assert limits.signal_confirmation_rounds == 3
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
    ) == (False, "TRADE_COOLDOWN_ACTIVE")
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
    )

    plan = decision.experimental_plan
    assert plan is not None
    assert plan.stop_anchor == Decimal("75.780")
    assert (plan.target_reference - plan.entry_reference) / plan.entry_reference * Decimal(
        10_000
    ) == Decimal("32")
    assert plan.strategy_version == "TESTNET_EXPERIMENT_OF_PA_V4_8"
    assert "maximum_holding" not in str(plan.evidence()).lower()


def test_v4_target_matches_fixed_symbol_execution_economics() -> None:
    assert gross_target_bps_for_symbol("BTCUSDT") == Decimal("20")
    assert gross_target_bps_for_symbol("ETHUSDT") == Decimal("22")
    assert gross_target_bps_for_symbol("BNBUSDT") == Decimal("25")
    assert gross_target_bps_for_symbol("SOLUSDT") == Decimal("32")
    assert gross_target_bps_for_symbol("XRPUSDT") == Decimal("25")
    with pytest.raises(ValueError, match="outside the fixed universe"):
        gross_target_bps_for_symbol("ADAUSDT")


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
        symbol="DOGEUSDT",
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
    assert state["last_confirmation_diagnostics"]["symbols"]["BNBUSDT"][
        "gate_result"
    ] == "CONFIRMED"


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
    assert state["last_confirmation_diagnostics"]["symbols"]["BNBUSDT"][
        "gate_result"
    ] == "ALREADY_IN_FLIGHT"


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


def test_market_breadth_promotes_all_locally_aligned_pool_symbols() -> None:
    state: dict[str, Any] = {
        "mid_price_history": {
            symbol: [
                {"evaluation_round": index, "mid_price": "100"}
                for index in range(1, 5)
            ]
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

    plans = {
        decision.symbol: decision.experimental_plan
        for decision in promoted
        if decision.experimental_plan is not None
    }
    assert set(plans) == {"BTCUSDT", "ETHUSDT", "BNBUSDT"}
    assert all(plan.setup_type == "MARKET_BREADTH_IMPULSE_FAST" for plan in plans.values())
    assert all(plan.direction is Direction.LONG for plan in plans.values())
    assert all(plan.market_breadth_count == 3 for plan in plans.values())
    assert state["last_signal_diagnostics"]["selected_setup"] == (
        "MARKET_BREADTH_IMPULSE_FAST"
    )
    assert state["signal_gate_counts"]["PLAN_GENERATED"] == 3


def test_sustained_breadth_catches_gradual_move_and_rejects_exhaustion() -> None:
    gradual = [
        "100", "100.01", "100.02", "100.03", "100.04", "100.05",
        "100.06", "100.075", "100.08", "100.085", "100.09",
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
        _neutral_impulse_decision("BTCUSDT", "100.095"),
        _neutral_impulse_decision("ETHUSDT", "100.20"),
        _neutral_impulse_decision("BNBUSDT", "100.095"),
        _neutral_impulse_decision("SOLUSDT", "100.095"),
        _neutral_impulse_decision("XRPUSDT", "100.095"),
    ]

    promoted = _apply_market_impulse_plans(
        state, decisions, limits=CampaignLimits(), evaluation_round=12
    )
    plans = {
        decision.symbol: decision.experimental_plan
        for decision in promoted
        if decision.experimental_plan is not None
    }

    assert set(plans) == {"BTCUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"}
    assert plans["BTCUSDT"].setup_type == "MARKET_BREADTH_TREND"
    assert state["last_signal_diagnostics"]["symbols"]["ETHUSDT"]["gate_result"] == (
        "LOCAL_MOMENTUM_INSUFFICIENT_OR_EXHAUSTED"
    )


def test_impulse_pending_uses_two_rounds_without_forcing_slot_fill() -> None:
    state: dict[str, Any] = {
        "aggressive_notional_history": {
            "BTCUSDT": ["500", "500", "500", "500", "500"]
        }
    }
    decision = _neutral_impulse_decision("BTCUSDT", "100.08")
    plan = baseline.build_market_impulse_plan(
        decision,
        direction=Direction.LONG,
        momentum_bps=Decimal("8"),
        breadth_count=3,
        parameters=SignalParameters(),
    )
    assert plan is not None
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
    first_gate = state["last_confirmation_diagnostics"]["symbols"]["BTCUSDT"][
        "gate_result"
    ]
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
    assert second == [decision]
    assert state["last_confirmation_diagnostics"]["confirmed_count"] == 1
    assert state["last_confirmation_diagnostics"]["symbols"]["BTCUSDT"][
        "gate_result"
    ] == "CONFIRMED"


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
        ),
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
    )
