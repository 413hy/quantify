from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ai_quant.services.telegram_dashboard import (
    TelegramDashboard,
    TelegramDashboardClient,
    render_pnl,
    render_positions,
    render_status,
    render_strategy_stats,
)


def test_bot_api_uses_long_poll_commands_and_persistent_reply_keyboard() -> None:
    calls: list[tuple[str, dict[str, object], float]] = []

    def call(path: str, document: dict[str, object], timeout: float) -> object:
        calls.append((path, document, timeout))
        if path.endswith("/getUpdates"):
            return [{"update_id": 10}]
        if path.endswith("/getWebhookInfo"):
            return {"url": ""}
        if path.endswith("/setMyCommands"):
            return True
        return {"message_id": 1}

    client = TelegramDashboardClient("123456789:" + "a" * 32, call=call)
    assert client.webhook_info() == {"url": ""}
    client.set_commands()
    assert client.get_updates(offset=9, timeout_seconds=30) == [{"update_id": 10}]
    client.send_message("123", "dashboard")

    update = next(document for path, document, _ in calls if path.endswith("/getUpdates"))
    assert update == {
        "offset": 9,
        "limit": 100,
        "timeout": 30,
        "allowed_updates": ["message"],
    }
    sent = next(document for path, document, _ in calls if path.endswith("/sendMessage"))
    keyboard = sent["reply_markup"]
    assert isinstance(keyboard, dict)
    assert keyboard["is_persistent"] is True
    assert keyboard["resize_keyboard"] is True
    assert "📊 当前盈亏" in str(keyboard["keyboard"])
    commands = next(document for path, document, _ in calls if path.endswith("/setMyCommands"))
    assert {item["command"] for item in commands["commands"]} == {
        "start",
        "pnl",
        "positions",
        "status",
        "stats",
        "help",
    }


def test_dashboard_renders_fee_adjusted_pnl_positions_and_strategy_stats() -> None:
    campaign = _campaign()
    events = [
        _protected("XRPUSDT"),
        _result("XRPUSDT", "0.12", "0.04", "0.08", True),
        _result("DOGEUSDT", "-0.15", "0.04", "-0.19", False),
    ]

    pnl = render_pnl(campaign, events)
    positions = render_positions(campaign, events)
    stats = render_strategy_stats(campaign, events)

    assert "本轮累计: -0.110000 USDT" in pnl
    assert "费用后盈利: 1/2 单" in pnl
    assert "费用后胜率: 50.00%" in pnl
    assert "累计净结果: -0.110000 USDT" in pnl
    assert "等待开仓确认: 1 个" in pnl
    assert "杠杆: 75x" in positions
    assert "预计止盈净额: 0.180000 USDT" in positions
    assert "DOGEUSDT\n仓位正在建立或等待保护确认" in positions
    assert "Profit Factor: 0.42" in stats
    assert "样本不足 (至少需要 30 单)" in stats


def test_dashboard_authorizes_only_configured_chat_ids(tmp_path: Path) -> None:
    _write_dashboard_inputs(tmp_path)
    client = FakeClient()
    dashboard = TelegramDashboard(
        client=client,  # type: ignore[arg-type]
        allowed_chat_ids=("123",),
        campaign_state_file=tmp_path / "campaign.json",
        observations_file=tmp_path / "observations.jsonl",
        user_stream_state_file=tmp_path / "stream.json",
        service_state_file=tmp_path / "dashboard.json",
    )
    state = _dashboard_state()

    dashboard._process_update(
        {"update_id": 1, "message": {"chat": {"id": 999}, "text": "/pnl"}}, state
    )
    dashboard._process_update(
        {"update_id": 2, "message": {"chat": {"id": 123}, "text": "📊 当前盈亏"}},
        state,
    )

    assert state["unauthorized_update_count"] == 1
    assert state["authorized_request_count"] == 1
    assert len(client.messages) == 1
    assert client.messages[0][0] == "123"
    assert "当前盈亏概览" in client.messages[0][1]


def test_status_marks_fresh_independent_rule_runtime_healthy() -> None:
    now = datetime(2026, 7, 14, 18, tzinfo=UTC)
    campaign = _campaign(updated_at="2026-07-14T17:59:30Z")
    stream = {"status": "RUNNING", "updated_at": "2026-07-14T17:59:20Z"}

    rendered = render_status(campaign, stream, now=now)

    assert "交易活动: 🟢 正常" in rendered
    assert "用户数据流: 🟢 正常" in rendered
    assert "依赖 Codex: 否" in rendered
    assert "活动仓位: 2 / 5 (不强制补满)" in rendered
    assert "等待开仓确认: 1 个" in rendered
    assert "确认门槛: 2 轮 / 质量分 2.00 / 预计净目标 0.10 U" in rendered
    assert "生产接口请求: 0" in rendered


class FakeClient:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def send_message(self, chat_id: str, text: str) -> None:
        self.messages.append((chat_id, text))


def _campaign(*, updated_at: str = "2026-07-14T17:59:30Z") -> dict[str, Any]:
    return {
        "status": "RUNNING",
        "strategy": "TESTNET_EXPERIMENT_OF_PA_V2",
        "decision_authority": "TESTNET_DETERMINISTIC_RULE",
        "codex_dependency": False,
        "daily_net_pnl": "-0.11",
        "cumulative_net_pnl": "-0.11",
        "trade_count": 2,
        "submitted_trade_count": 4,
        "active_symbols": ["XRPUSDT", "DOGEUSDT"],
        "pending_entry_symbols": ["BTCUSDT"],
        "pending_signals": {"SOLUSDT": {"consecutive_rounds": 1}},
        "limits": {
            "maximum_parallel_positions": 5,
            "signal_confirmation_rounds": 2,
            "minimum_signal_quality_score": "2.00",
            "minimum_estimated_net_target": "0.10",
        },
        "production_endpoint_requests": 0,
        "updated_at": updated_at,
    }


def _protected(symbol: str) -> dict[str, object]:
    return {
        "record_type": "TESTNET_POSITION_PROTECTED",
        "strategy": "TESTNET_EXPERIMENT_OF_PA_V2",
        "symbol": symbol,
        "direction": "LONG",
        "initial_leverage": 75,
        "actual_initial_margin": "0.95",
        "position_notional": "71.25",
        "entry_price": "1.00",
        "target_trigger": "1.0035",
        "estimated_target_net_pnl": "0.18",
        "stop_trigger": "0.997",
        "estimated_stop_net_loss": "0.28",
    }


def _result(symbol: str, realized: str, fee: str, net: str, target: bool) -> dict[str, object]:
    return {
        "record_type": "TESTNET_EXPERIMENT_RESULT",
        "strategy": "TESTNET_EXPERIMENT_OF_PA_V2",
        "symbol": symbol,
        "exit_reason": "TAKE_PROFIT" if target else "STOP_LOSS",
        "realized_pnl": realized,
        "commission_paid": fee,
        "net_pnl": net,
        "target_achieved": target,
        "production_endpoint_requests": 0,
    }


def _write_dashboard_inputs(path: Path) -> None:
    (path / "campaign.json").write_text(json.dumps(_campaign()), encoding="utf-8")
    (path / "stream.json").write_text(
        json.dumps({"status": "RUNNING", "updated_at": "2026-07-14T17:59:30Z"}),
        encoding="utf-8",
    )
    (path / "observations.jsonl").write_text(
        json.dumps(_result("XRPUSDT", "0.12", "0.04", "0.08", True)) + "\n",
        encoding="utf-8",
    )


def _dashboard_state() -> dict[str, object]:
    return {
        "authorized_request_count": 0,
        "unauthorized_update_count": 0,
        "ignored_update_count": 0,
        "last_authorized_request_at": None,
    }
