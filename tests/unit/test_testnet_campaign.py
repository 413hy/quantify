from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

import ai_quant.strategy.testnet_baseline as baseline
from ai_quant.features.order_flow import OrderFlowFrame
from ai_quant.features.price_action import Direction, PriceActionFrame, Regime, Structure
from ai_quant.services.testnet_campaign import (
    CampaignLimits,
    _select_candidate,
    _summary_text,
    campaign_trade_allowed,
)
from ai_quant.strategy.testnet_baseline import evaluate_testnet_baseline


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
        last_trade_at=now - timedelta(seconds=299),
        daily_trade_count=0,
        daily_net_pnl=Decimal("0"),
        limits=limits,
    ) == (False, "TRADE_COOLDOWN_ACTIVE")
    assert campaign_trade_allowed(
        now=now,
        last_trade_at=None,
        daily_trade_count=8,
        daily_net_pnl=Decimal("0"),
        limits=limits,
    ) == (False, "DAILY_TRADE_LIMIT_REACHED")
    assert campaign_trade_allowed(
        now=now,
        last_trade_at=None,
        daily_trade_count=1,
        daily_net_pnl=Decimal("-0.30"),
        limits=limits,
    ) == (False, "DAILY_LOSS_LIMIT_REACHED")


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
