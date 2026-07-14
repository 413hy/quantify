"""Run a bounded multi-day Testnet PA/OF observation and micro-position campaign."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from ai_quant.binance_egress.micro_scalp import run_testnet_micro_scalp
from ai_quant.binance_egress.testnet_probe import BinanceTestnetClient, _credential
from ai_quant.notifications import (
    Notification,
    OutboundNotifier,
    TelegramDeliveryError,
    TelegramFileConfig,
    TelegramSender,
)
from ai_quant.strategy.testnet_baseline import (
    TestnetBaselineDecision,
    evaluate_testnet_baseline,
)


@dataclass(frozen=True, slots=True)
class CampaignLimits:
    duration_seconds: int = 259_200
    evaluation_interval_seconds: int = 60
    trade_cooldown_seconds: int = 900
    maximum_trades_per_day: int = 8
    daily_net_loss_limit: Decimal = Decimal("0.30")
    margin_budget: Decimal = Decimal("1")
    target_net_profit: Decimal = Decimal("0.1")
    maximum_net_loss: Decimal = Decimal("0.1")
    maximum_holding_seconds: int = 30

    def __post_init__(self) -> None:
        if not 60 <= self.duration_seconds <= 604_800:
            raise ValueError("campaign duration must be between 60 seconds and 7 days")
        if not 10 <= self.evaluation_interval_seconds <= 3_600:
            raise ValueError("campaign evaluation interval is invalid")
        if not 60 <= self.trade_cooldown_seconds <= 86_400:
            raise ValueError("campaign trade cooldown is invalid")
        if not 1 <= self.maximum_trades_per_day <= 24:
            raise ValueError("campaign daily trade count is invalid")
        if self.daily_net_loss_limit <= 0 or self.daily_net_loss_limit > Decimal("1"):
            raise ValueError("campaign daily loss limit is invalid")


def campaign_trade_allowed(
    *,
    now: datetime,
    last_trade_at: datetime | None,
    daily_trade_count: int,
    daily_net_pnl: Decimal,
    limits: CampaignLimits,
) -> tuple[bool, str | None]:
    if daily_trade_count >= limits.maximum_trades_per_day:
        return False, "DAILY_TRADE_LIMIT_REACHED"
    if daily_net_pnl <= -limits.daily_net_loss_limit:
        return False, "DAILY_LOSS_LIMIT_REACHED"
    if last_trade_at is not None:
        elapsed = (now - last_trade_at).total_seconds()
        if elapsed < limits.trade_cooldown_seconds:
            return False, "TRADE_COOLDOWN_ACTIVE"
    return True, None


class TestnetCampaign:
    def __init__(
        self,
        *,
        api_key_file: Path,
        api_secret_file: Path,
        repository_root: Path,
        token_file: Path,
        chat_ids_file: Path,
        evidence_directory: Path,
        state_file: Path,
        symbols: tuple[str, ...],
        limits: CampaignLimits,
    ) -> None:
        self.api_key_file = api_key_file
        self.api_secret_file = api_secret_file
        self.repository_root = repository_root
        self.evidence_directory = evidence_directory
        self.state_file = state_file
        if not symbols or len(symbols) > 10 or len(set(symbols)) != len(symbols):
            raise ValueError("campaign symbols must contain 1 to 10 unique entries")
        if any(not symbol.isalnum() or symbol != symbol.upper() for symbol in symbols):
            raise ValueError("campaign symbols are invalid")
        self.symbols = symbols
        self.limits = limits
        config = TelegramFileConfig.load(token_file, chat_ids_file)
        self.notifier = OutboundNotifier(TelegramSender(config))
        key = _credential(api_key_file, repository_root)
        secret = _credential(api_secret_file, repository_root)
        self.client = BinanceTestnetClient(key, secret)
        self.stop_requested = False

    def request_stop(self) -> None:
        self.stop_requested = True

    def run(self) -> int:
        state = self._load_or_create_state()
        self._notify(
            severity="INFO",
            event_type="测试网策略观察已启动",
            summary=(
                f"候选池: {', '.join(self.symbols)}\n"
                f"计划运行: {self.limits.duration_seconds // 86_400} 天\n"
                f"评估间隔: {self.limits.evaluation_interval_seconds} 秒\n"
                f"单次保证金上限: {self.limits.margin_budget} USDT\n"
                "仅在 1 分钟与 5 分钟 PA 同向、OF 确认及风险门槛全部通过时下单。"
            ),
            key=f"campaign-start-{state['started_at']}",
        )
        consecutive_errors = 0
        last_heartbeat = datetime.now(UTC)
        while not self.stop_requested:
            now = datetime.now(UTC)
            if now >= _parse_time(str(state["ends_at"])):
                break
            try:
                state = self._evaluate_once(state)
                consecutive_errors = 0
            except Exception as exc:  # fail closed and keep the observation process alive
                consecutive_errors += 1
                reason = type(exc).__name__
                self._append_event(
                    {
                        "record_type": "CAMPAIGN_ERROR",
                        "occurred_at": now.isoformat().replace("+00:00", "Z"),
                        "reason_code": reason,
                    }
                )
                if consecutive_errors == 1:
                    self._notify(
                        severity="WARNING",
                        event_type="测试网策略观察异常",
                        summary=f"本轮评估已安全跳过, 不会下单。异常类型: {reason}",
                        key=f"campaign-error-{now:%Y%m%d%H}",
                    )
                if consecutive_errors >= 10:
                    self._notify(
                        severity="ERROR",
                        event_type="测试网策略观察已暂停",
                        summary="连续 10 轮评估异常, 服务退出并等待自动重启; 当前不会新增仓位。",
                        key=f"campaign-paused-{now:%Y%m%d%H}",
                    )
                    return 2
            now = datetime.now(UTC)
            if now - last_heartbeat >= timedelta(hours=6):
                self._send_heartbeat(state, now)
                last_heartbeat = now
            remaining = (_parse_time(str(state["ends_at"])) - now).total_seconds()
            if remaining > 0 and not self.stop_requested:
                time.sleep(min(self.limits.evaluation_interval_seconds, remaining))
        self._notify(
            severity="INFO",
            event_type="测试网策略观察已结束",
            summary=_summary_text(state),
            key=f"campaign-finished-{state['started_at']}",
        )
        state["status"] = "STOPPED" if self.stop_requested else "COMPLETED"
        state["updated_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        _atomic_write_json(self.state_file, state)
        return 0

    def _evaluate_once(self, state: dict[str, Any]) -> dict[str, Any]:
        _, server_offset_ms = self.client.synchronize_time()
        worker_count = min(3, len(self.symbols))
        with ThreadPoolExecutor(
            max_workers=worker_count, thread_name_prefix="testnet-observation"
        ) as executor:
            futures = {
                symbol: executor.submit(self._observe_symbol, symbol, server_offset_ms)
                for symbol in self.symbols
            }
            decisions = [futures[symbol].result() for symbol in self.symbols]
        reason_codes: dict[str, list[str]] = {}
        for decision in decisions:
            event = decision.evidence()
            event["record_type"] = "SIGNAL_OBSERVATION"
            self._append_event(event)
            state["observation_count"] = int(state["observation_count"]) + 1
            state["last_observed_at"] = event["observed_at"]
            reason_codes[decision.symbol] = list(decision.reason_codes)
        state["evaluation_round_count"] = int(state["evaluation_round_count"]) + 1
        state["last_reason_codes"] = reason_codes
        latest_observed_at = max(decision.observed_at for decision in decisions)
        self._reset_daily_state_if_needed(state, latest_observed_at)
        selected = _select_candidate(decisions)
        if selected is not None:
            last_trade = (
                _parse_time(str(state["last_trade_at"])) if state.get("last_trade_at") else None
            )
            allowed, reason = campaign_trade_allowed(
                now=selected.observed_at,
                last_trade_at=last_trade,
                daily_trade_count=int(state["daily_trade_count"]),
                daily_net_pnl=Decimal(str(state["daily_net_pnl"])),
                limits=self.limits,
            )
            if allowed:
                result = self._execute_trade(selected.symbol)
                state = self._record_trade(state, result, selected.observed_at, selected.symbol)
            else:
                blocked = selected.evidence()
                blocked["record_type"] = "SIGNAL_BLOCKED_BY_CAMPAIGN_LIMIT"
                blocked["campaign_limit_reason"] = reason
                self._append_event(blocked)
        state["updated_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        _atomic_write_json(self.state_file, state)
        return state

    def _observe_symbol(self, symbol: str, server_offset_ms: int) -> TestnetBaselineDecision:
        one_minute_klines = self.client.klines(symbol, "1m", limit=120)
        five_minute_klines = self.client.klines(symbol, "5m", limit=120)
        depth = self.client.depth(symbol, limit=20)
        aggregate_trades = self.client.aggregate_trades(symbol, limit=100)
        server_time_ms = int(time.time() * 1_000) + server_offset_ms
        return evaluate_testnet_baseline(
            symbol=symbol,
            server_time_ms=server_time_ms,
            one_minute_klines=one_minute_klines,
            five_minute_klines=five_minute_klines,
            depth=depth,
            aggregate_trades=aggregate_trades,
        )

    def _execute_trade(self, symbol: str) -> dict[str, Any]:
        result = run_testnet_micro_scalp(
            api_key_file=self.api_key_file,
            api_secret_file=self.api_secret_file,
            repository_root=self.repository_root,
            symbol=symbol,
            margin_budget=self.limits.margin_budget,
            target_net_profit=self.limits.target_net_profit,
            maximum_net_loss=self.limits.maximum_net_loss,
            maximum_holding_seconds=self.limits.maximum_holding_seconds,
        )
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        _atomic_write_json(self.evidence_directory / "trades" / f"{stamp}.json", result)
        return result

    def _record_trade(
        self,
        state: dict[str, Any],
        result: dict[str, Any],
        occurred_at: datetime,
        symbol: str,
    ) -> dict[str, Any]:
        net = Decimal(str(result["net_pnl"]))
        state["trade_count"] = int(state["trade_count"]) + 1
        state["daily_trade_count"] = int(state["daily_trade_count"]) + 1
        state["cumulative_net_pnl"] = format(Decimal(str(state["cumulative_net_pnl"])) + net, "f")
        state["daily_net_pnl"] = format(Decimal(str(state["daily_net_pnl"])) + net, "f")
        state["last_trade_at"] = occurred_at.isoformat().replace("+00:00", "Z")
        if bool(result["target_achieved"]):
            state["target_hit_count"] = int(state["target_hit_count"]) + 1
        target_achieved = "是" if bool(result["target_achieved"]) else "否"
        summary = (
            f"交易对: {symbol}\n"
            "方向: 做多\n"
            "环境: Binance Testnet\n"
            f"数量: {result['quantity']}\n"
            f"退出原因: {_exit_reason_cn(str(result['exit_reason']))}\n"
            f"入场价: {result['entry_price']}\n"
            f"止盈触发价: {result['target_trigger']}\n"
            f"止损触发价: {result['stop_trigger']}\n"
            f"达到目标: {target_achieved}\n"
            f"已实现盈亏: {result['realized_pnl']} USDT\n"
            f"手续费: {result['commission_paid']} USDT\n"
            f"本单净结果: {net} USDT\n"
            f"累计净结果: {state['cumulative_net_pnl']} USDT\n"
            f"保证金: {result['actual_initial_margin']} USDT\n"
            f"止损确认延迟: {result['stop_confirmation_latency_ms']} ms\n"
            f"止盈确认延迟: {result['take_profit_confirmation_latency_ms']} ms\n"
            f"剩余订单: {result['final_open_order_count']}\n"
            f"剩余条件单: {result['final_open_algo_order_count']}\n"
            f"剩余持仓: {result['final_position_quantity']}"
        )
        self._notify(
            severity="INFO" if net >= 0 else "WARNING",
            event_type="测试网策略成交结果",
            summary=summary,
            key=f"campaign-trade-{state['trade_count']}-{occurred_at.isoformat()}",
        )
        return state

    def _reset_daily_state_if_needed(self, state: dict[str, Any], observed_at: datetime) -> None:
        day = observed_at.date().isoformat()
        if state["daily_utc_date"] != day:
            state["daily_utc_date"] = day
            state["daily_trade_count"] = 0
            state["daily_net_pnl"] = "0"

    def _send_heartbeat(self, state: dict[str, Any], now: datetime) -> None:
        self._notify(
            severity="INFO",
            event_type="测试网策略运行简报",
            summary=_summary_text(state),
            key=f"campaign-heartbeat-{now:%Y%m%d%H}",
        )

    def _notify(self, *, severity: str, event_type: str, summary: str, key: str) -> None:
        try:
            self.notifier.notify(
                Notification(
                    severity=severity,
                    event_type=event_type,
                    summary=summary,
                    runbook="docs/testnet-campaign.md",
                    occurred_at=datetime.now(UTC),
                    deduplication_key=key,
                )
            )
        except TelegramDeliveryError:
            # Notification delivery is never allowed to change trading decisions.
            pass

    def _append_event(self, document: dict[str, Any]) -> None:
        path = self.evidence_directory / "observations.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(document, sort_keys=True, separators=(",", ":")))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _load_or_create_state(self) -> dict[str, Any]:
        prior_campaign: dict[str, object] | None = None
        if self.state_file.exists():
            loaded: object = json.loads(self.state_file.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict) or not all(isinstance(key, str) for key in loaded):
                raise ValueError("campaign state document is invalid")
            document = cast(dict[str, Any], loaded)
            if document.get("status") == "RUNNING" and document.get("symbols") == list(
                self.symbols
            ):
                document["limits"] = self._limits_document()
                document["validation_status"] = "UNVALIDATED_TESTNET_BASELINE"
                _atomic_write_json(self.state_file, document)
                return document
            prior_campaign = {
                "started_at": document.get("started_at"),
                "updated_at": document.get("updated_at"),
                "symbol": document.get("symbol"),
                "symbols": document.get("symbols"),
                "observation_count": document.get("observation_count", 0),
                "trade_count": document.get("trade_count", 0),
                "cumulative_net_pnl": document.get("cumulative_net_pnl", "0"),
            }
        now = datetime.now(UTC)
        state: dict[str, Any] = {
            "schema_version": "1.0.0",
            "status": "RUNNING",
            "environment": "testnet",
            "strategy": "TESTNET_UNVALIDATED_PA_OF_BASELINE_V1",
            "validation_status": "UNVALIDATED_TESTNET_BASELINE",
            "symbols": list(self.symbols),
            "started_at": now.isoformat().replace("+00:00", "Z"),
            "ends_at": (now + timedelta(seconds=self.limits.duration_seconds))
            .isoformat()
            .replace("+00:00", "Z"),
            "updated_at": now.isoformat().replace("+00:00", "Z"),
            "observation_count": 0,
            "evaluation_round_count": 0,
            "trade_count": 0,
            "target_hit_count": 0,
            "cumulative_net_pnl": "0",
            "last_observed_at": None,
            "last_reason_codes": [],
            "last_trade_at": None,
            "daily_utc_date": now.date().isoformat(),
            "daily_trade_count": 0,
            "daily_net_pnl": "0",
            "production_endpoint_requests": 0,
            "limits": self._limits_document(),
            "prior_campaign": prior_campaign,
        }
        _atomic_write_json(self.state_file, state)
        return state

    def _limits_document(self) -> dict[str, object]:
        return {
            "evaluation_interval_seconds": self.limits.evaluation_interval_seconds,
            "trade_cooldown_seconds": self.limits.trade_cooldown_seconds,
            "maximum_trades_per_day": self.limits.maximum_trades_per_day,
            "daily_net_loss_limit": format(self.limits.daily_net_loss_limit, "f"),
            "margin_budget": format(self.limits.margin_budget, "f"),
            "target_net_profit": format(self.limits.target_net_profit, "f"),
            "maximum_net_loss": format(self.limits.maximum_net_loss, "f"),
            "maximum_holding_seconds": self.limits.maximum_holding_seconds,
            "maximum_parallel_observations": min(3, len(self.symbols)),
            "maximum_candidates_per_round": 1,
        }


def _summary_text(state: dict[str, Any]) -> str:
    observations = int(state["observation_count"])
    trades = int(state["trade_count"])
    hits = int(state["target_hit_count"])
    hit_rate = Decimal(0) if trades == 0 else Decimal(hits) / Decimal(trades) * Decimal(100)
    status = {
        "RUNNING": "运行中",
        "STOPPED": "已停止",
        "COMPLETED": "已完成",
    }.get(str(state["status"]), str(state["status"]))
    reasons = _reason_summary(state["last_reason_codes"])
    return (
        f"运行状态: {status}\n"
        f"信号评估: {observations} 次\n"
        f"评估轮次: {state.get('evaluation_round_count', observations)} 轮\n"
        f"实际交易: {trades} 单\n"
        f"达到目标: {hits} 单 ({hit_rate.quantize(Decimal('0.01'))}%)\n"
        f"累计净结果: {state['cumulative_net_pnl']} USDT\n"
        f"最近跳过原因: {reasons or '无'}\n"
        "环境: Binance Testnet (未请求生产接口)"
    )


def _exit_reason_cn(reason: str) -> str:
    return {
        "TAKE_PROFIT": "止盈触发",
        "STOP_LOSS": "止损触发",
        "MAX_HOLDING_TIME": "达到最长持仓时间",
        "NATIVE_EXIT_UNCLASSIFIED": "交易所原生保护退出",
    }.get(reason, reason)


def _reason_code_cn(reason: str) -> str:
    return {
        "PA_1M_NOT_LONG": "1 分钟 PA 未形成多头趋势",
        "PA_5M_NOT_LONG": "5 分钟 PA 未形成多头趋势",
        "OF_INSUFFICIENT_AGGRESSION": "近期主动成交不足",
        "OF_BOOK_IMBALANCE_INSUFFICIENT": "盘口买方失衡不足",
        "OF_MICROPRICE_CONFIRMATION_INSUFFICIENT": "微价格确认不足",
        "OF_TRADE_IMBALANCE_INSUFFICIENT": "主动买入成交失衡不足",
        "OF_CVD_NOT_POSITIVE": "成交量差未转正",
        "SPREAD_TOO_WIDE": "买卖点差过宽",
    }.get(reason, reason)


def _reason_summary(value: object) -> str:
    if isinstance(value, dict):
        lines: list[str] = []
        for symbol, codes in value.items():
            if not isinstance(symbol, str) or not isinstance(codes, list):
                continue
            translated = "、".join(_reason_code_cn(str(code)) for code in codes[:3])
            lines.append(f"{symbol}: {translated or '通过'}")
        return "\n".join(lines)
    if isinstance(value, list):
        return "、".join(_reason_code_cn(str(code)) for code in value)
    return ""


def _select_candidate(
    decisions: list[TestnetBaselineDecision],
) -> TestnetBaselineDecision | None:
    eligible = [decision for decision in decisions if decision.eligible]
    if not eligible:
        return None

    def rank(decision: TestnetBaselineDecision) -> tuple[Decimal, str]:
        flow = decision.order_flow
        score = (
            flow.book_imbalance
            + flow.trade_imbalance
            + flow.microprice_mid_bps / Decimal(10)
            - decision.spread_bps / Decimal(10)
        )
        return -score, decision.symbol

    return sorted(eligible, key=rank)[0]


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _atomic_write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key-file", required=True, type=Path)
    parser.add_argument("--api-secret-file", required=True, type=Path)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--telegram-token-file", required=True, type=Path)
    parser.add_argument("--telegram-chat-ids-file", required=True, type=Path)
    parser.add_argument("--evidence-directory", required=True, type=Path)
    parser.add_argument("--state-file", required=True, type=Path)
    parser.add_argument("--lock-file", required=True, type=Path)
    parser.add_argument("--symbols", default="SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,ADAUSDT")
    parser.add_argument("--duration-seconds", type=int, default=259_200)
    parser.add_argument("--evaluation-interval-seconds", type=int, default=60)
    parser.add_argument("--trade-cooldown-seconds", type=int, default=900)
    parser.add_argument("--maximum-trades-per-day", type=int, default=8)
    parser.add_argument("--daily-net-loss-limit", type=Decimal, default=Decimal("0.30"))
    arguments = parser.parse_args()
    limits = CampaignLimits(
        duration_seconds=arguments.duration_seconds,
        evaluation_interval_seconds=arguments.evaluation_interval_seconds,
        trade_cooldown_seconds=arguments.trade_cooldown_seconds,
        maximum_trades_per_day=arguments.maximum_trades_per_day,
        daily_net_loss_limit=arguments.daily_net_loss_limit,
    )
    arguments.lock_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with arguments.lock_file.open("w", encoding="ascii") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("TESTNET_CAMPAIGN_ALREADY_RUNNING")
            return 3
        campaign = TestnetCampaign(
            api_key_file=arguments.api_key_file,
            api_secret_file=arguments.api_secret_file,
            repository_root=arguments.repository_root,
            token_file=arguments.telegram_token_file,
            chat_ids_file=arguments.telegram_chat_ids_file,
            evidence_directory=arguments.evidence_directory,
            state_file=arguments.state_file,
            symbols=tuple(
                symbol.strip() for symbol in arguments.symbols.split(",") if symbol.strip()
            ),
            limits=limits,
        )

        def request_stop(_signal_number: int, _frame: object) -> None:
            campaign.request_stop()

        signal.signal(signal.SIGINT, request_stop)
        signal.signal(signal.SIGTERM, request_stop)
        return campaign.run()


if __name__ == "__main__":
    raise SystemExit(main())
