"""Run a bounded multi-day Testnet PA/OF observation and micro-position campaign."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from ai_quant.binance_egress.structural_experiment import run_structural_experiment
from ai_quant.binance_egress.testnet_probe import BinanceTestnetClient, _credential
from ai_quant.binance_egress.testnet_stream import TestnetAggregateTradeStream
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
    trade_cooldown_seconds: int = 300
    maximum_trades_per_day: int = 8
    daily_net_loss_limit: Decimal = Decimal("1.00")
    margin_budget: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if not 60 <= self.duration_seconds <= 604_800:
            raise ValueError("campaign duration must be between 60 seconds and 7 days")
        if not 10 <= self.evaluation_interval_seconds <= 3_600:
            raise ValueError("campaign evaluation interval is invalid")
        if not 60 <= self.trade_cooldown_seconds <= 86_400:
            raise ValueError("campaign trade cooldown is invalid")
        if not 1 <= self.maximum_trades_per_day <= 200:
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
        self.trade_stream = TestnetAggregateTradeStream(symbols)
        self.trade_executor = ThreadPoolExecutor(
            max_workers=min(3, len(symbols)), thread_name_prefix="testnet-experiment"
        )
        self.active_trades: dict[str, Future[dict[str, Any]]] = {}
        self.stop_requested = False

    def request_stop(self) -> None:
        self.stop_requested = True

    def run(self) -> int:
        self.trade_stream.start()
        try:
            return self._run_campaign()
        finally:
            self.trade_stream.stop()
            self.trade_executor.shutdown(wait=True, cancel_futures=False)

    def _run_campaign(self) -> int:
        state = self._load_or_create_state()
        self._notify(
            severity="INFO",
            event_type="测试网实验交易已启动",
            summary=(
                f"候选池: {', '.join(self.symbols)}\n"
                f"计划运行: {self.limits.duration_seconds // 86_400} 天\n"
                f"评估间隔: {self.limits.evaluation_interval_seconds} 秒\n"
                "运行模式: Testnet 实验下单 (最多 3 个币种并行)\n"
                "单笔保证金: 最高 1 USDT; 杠杆: 10 倍\n"
                "退出方式: 原生结构止损/止盈, 不使用持仓时间到期平仓\n"
                "说明: 这是未验证实验策略, 仅用于收集测试网真实成交样本。"
            ),
            key=f"campaign-start-{state['started_at']}",
        )
        consecutive_errors = 0
        result_code = 0
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
                        event_type="测试网策略评估异常",
                        summary=(
                            "本轮评估已跳过。已有仓位仍由交易所原生止盈止损保护。"
                            f"异常类型: {reason}"
                        ),
                        key=f"campaign-error-{now:%Y%m%d%H}",
                    )
                if consecutive_errors >= 10:
                    self._notify(
                        severity="ERROR",
                        event_type="测试网策略评估已暂停",
                        summary=(
                            "连续 10 轮评估异常, 服务退出并等待自动重启; "
                            "已有仓位会先执行人工停止平仓。"
                        ),
                        key=f"campaign-paused-{now:%Y%m%d%H}",
                    )
                    result_code = 2
                    self.stop_requested = True
                    break
            now = datetime.now(UTC)
            if now - last_heartbeat >= timedelta(hours=6):
                self._send_heartbeat(state, now)
                last_heartbeat = now
            remaining = (_parse_time(str(state["ends_at"])) - now).total_seconds()
            if remaining > 0 and not self.stop_requested:
                time.sleep(min(self.limits.evaluation_interval_seconds, remaining))
        stopped_by_operator = self.stop_requested
        self.stop_requested = True
        while self.active_trades:
            self._reap_completed_trades(state)
            if self.active_trades:
                time.sleep(1)
        state["active_symbols"] = []
        self._notify(
            severity="INFO",
            event_type="测试网实验交易已结束",
            summary=_summary_text(state),
            key=f"campaign-finished-{state['started_at']}",
        )
        state["status"] = "STOPPED" if stopped_by_operator else "COMPLETED"
        state["updated_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        _atomic_write_json(self.state_file, state)
        return result_code

    def _evaluate_once(self, state: dict[str, Any]) -> dict[str, Any]:
        self._reap_completed_trades(state)
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
        self._submit_experiments(state, decisions, latest_observed_at)
        state["active_symbols"] = sorted(self.active_trades)
        state["updated_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        _atomic_write_json(self.state_file, state)
        return state

    def _submit_experiments(
        self,
        state: dict[str, Any],
        decisions: list[TestnetBaselineDecision],
        observed_at: datetime,
    ) -> None:
        available = min(3, len(self.symbols)) - len(self.active_trades)
        if available <= 0:
            return
        plans = sorted(
            (
                decision.experimental_plan
                for decision in decisions
                if decision.experimental_plan is not None
                and decision.symbol not in self.active_trades
            ),
            key=lambda plan: plan.symbol,
        )
        last_by_symbol = cast(dict[str, str], state.setdefault("last_trade_by_symbol", {}))
        for plan in plans:
            if available <= 0:
                break
            last_value = last_by_symbol.get(plan.symbol)
            allowed, reason = campaign_trade_allowed(
                now=observed_at,
                last_trade_at=None if last_value is None else _parse_time(last_value),
                daily_trade_count=int(state["daily_trade_count"]) + len(self.active_trades),
                daily_net_pnl=Decimal(str(state["daily_net_pnl"])),
                limits=self.limits,
            )
            if not allowed:
                event = plan.evidence()
                event.update(
                    {
                        "record_type": "TESTNET_EXPERIMENT_BLOCKED",
                        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
                        "reason_code": str(reason),
                    }
                )
                self._append_event(event)
                continue
            future = self.trade_executor.submit(
                run_structural_experiment,
                api_key_file=self.api_key_file,
                api_secret_file=self.api_secret_file,
                repository_root=self.repository_root,
                plan=plan,
                margin_budget=self.limits.margin_budget,
                stop_requested=lambda: self.stop_requested,
            )
            self.active_trades[plan.symbol] = future
            state["submitted_trade_count"] = int(state.get("submitted_trade_count", 0)) + 1
            timestamp = observed_at.isoformat().replace("+00:00", "Z")
            last_by_symbol[plan.symbol] = timestamp
            state["last_trade_at"] = timestamp
            event = plan.evidence()
            event.update(
                {
                    "record_type": "TESTNET_EXPERIMENT_SUBMITTED",
                    "observed_at": timestamp,
                    "validation_status": "UNVALIDATED_TESTNET_EXPERIMENT",
                }
            )
            self._append_event(event)
            self._notify(
                severity="INFO",
                event_type="测试网开仓信号已提交",
                summary=(
                    f"交易对: {plan.symbol}\n"
                    f"方向: {'做多' if str(plan.direction) == 'LONG' else '做空'}\n"
                    f"参考入场: {plan.entry_reference}\n"
                    f"结构止损: {plan.stop_anchor}\n"
                    f"目标止盈: {plan.target_reference}\n"
                    "仓位状态将以交易所成交回报为准。"
                ),
                key=f"experiment-submit-{plan.symbol}-{observed_at.timestamp()}",
            )
            available -= 1

    def _reap_completed_trades(self, state: dict[str, Any]) -> None:
        for symbol, future in list(self.active_trades.items()):
            if not future.done():
                continue
            del self.active_trades[symbol]
            try:
                result = future.result()
            except Exception as exc:
                occurred_at = datetime.now(UTC)
                self._append_event(
                    {
                        "record_type": "TESTNET_EXPERIMENT_ERROR",
                        "occurred_at": occurred_at.isoformat().replace("+00:00", "Z"),
                        "symbol": symbol,
                        "reason_code": type(exc).__name__,
                        "message": str(exc),
                    }
                )
                self._notify(
                    severity="ERROR",
                    event_type="测试网交易执行失败",
                    summary=f"交易对: {symbol}\n异常: {type(exc).__name__}\n详情: {exc}",
                    key=f"experiment-error-{symbol}-{occurred_at:%Y%m%d%H%M%S}",
                )
                continue
            self._append_event(result)
            net = Decimal(str(result["net_pnl"]))
            state["trade_count"] = int(state["trade_count"]) + 1
            state["daily_trade_count"] = int(state["daily_trade_count"]) + 1
            state["daily_net_pnl"] = format(Decimal(str(state["daily_net_pnl"])) + net, "f")
            state["cumulative_net_pnl"] = format(
                Decimal(str(state["cumulative_net_pnl"])) + net, "f"
            )
            if result["target_achieved"]:
                state["target_hit_count"] = int(state["target_hit_count"]) + 1
            self._notify(
                severity="INFO" if net >= 0 else "WARNING",
                event_type="测试网交易已平仓",
                summary=(
                    f"交易对: {symbol}\n"
                    f"方向: {'做多' if result['direction'] == 'LONG' else '做空'}\n"
                    f"入场价: {result['entry_price']}\n"
                    f"退出原因: {_exit_reason_cn(str(result['exit_reason']))}\n"
                    f"已实现盈亏: {result['realized_pnl']} USDT\n"
                    f"手续费: {result['commission_paid']} USDT\n"
                    f"净结果: {result['net_pnl']} USDT"
                ),
                key=f"experiment-result-{symbol}-{result['completed_at']}",
            )

    def _observe_symbol(self, symbol: str, server_offset_ms: int) -> TestnetBaselineDecision:
        one_minute_klines = self.client.klines(symbol, "1m", limit=120)
        five_minute_klines = self.client.klines(symbol, "5m", limit=120)
        depth = self.client.depth(symbol, limit=20)
        aggregate_trades = self.trade_stream.snapshot(symbol, maximum_age_ms=5_000)
        server_time_ms = int(time.time() * 1_000) + server_offset_ms
        return evaluate_testnet_baseline(
            symbol=symbol,
            server_time_ms=server_time_ms,
            one_minute_klines=one_minute_klines,
            five_minute_klines=five_minute_klines,
            depth=depth,
            aggregate_trades=aggregate_trades,
        )

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
                document["strategy"] = "TESTNET_EXPERIMENT_OF_PA_V1"
                document["validation_status"] = "UNVALIDATED_TESTNET_EXPERIMENT"
                document.setdefault("last_trade_by_symbol", {})
                document.setdefault("active_symbols", [])
                document.setdefault(
                    "submitted_trade_count",
                    int(document.get("trade_count", 0)) + len(document["active_symbols"]),
                )
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
            "strategy": "TESTNET_EXPERIMENT_OF_PA_V1",
            "validation_status": "UNVALIDATED_TESTNET_EXPERIMENT",
            "symbols": list(self.symbols),
            "started_at": now.isoformat().replace("+00:00", "Z"),
            "ends_at": (now + timedelta(seconds=self.limits.duration_seconds))
            .isoformat()
            .replace("+00:00", "Z"),
            "updated_at": now.isoformat().replace("+00:00", "Z"),
            "observation_count": 0,
            "evaluation_round_count": 0,
            "trade_count": 0,
            "submitted_trade_count": 0,
            "target_hit_count": 0,
            "cumulative_net_pnl": "0",
            "last_observed_at": None,
            "last_reason_codes": [],
            "last_trade_at": None,
            "daily_utc_date": now.date().isoformat(),
            "daily_trade_count": 0,
            "daily_net_pnl": "0",
            "last_trade_by_symbol": {},
            "active_symbols": [],
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
            "execution_mode": "TESTNET_EXPERIMENT",
            "maximum_parallel_observations": min(3, len(self.symbols)),
            "maximum_parallel_positions": min(3, len(self.symbols)),
            "maximum_candidates_per_round": min(3, len(self.symbols)),
            "elapsed_time_exit_enabled": False,
        }


def _summary_text(state: dict[str, Any]) -> str:
    observations = int(state["observation_count"])
    trades = int(state["trade_count"])
    submitted = int(state.get("submitted_trade_count", trades))
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
        f"已提交开仓: {submitted} 单\n"
        f"已完成平仓: {trades} 单\n"
        f"达到目标: {hits} 单 ({hit_rate.quantize(Decimal('0.01'))}%)\n"
        f"累计净结果: {state['cumulative_net_pnl']} USDT\n"
        f"最近跳过原因: {reasons or '无'}\n"
        "环境: Binance Testnet (未请求生产接口)"
    )


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


def _exit_reason_cn(reason: str) -> str:
    return {
        "TAKE_PROFIT": "达到止盈目标",
        "STOP_LOSS": "跌破/突破结构止损",
        "OPERATOR_SERVICE_STOP": "服务停止时人工平仓",
        "NATIVE_EXIT_UNCLASSIFIED": "交易所原生保护单平仓",
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
    parser.add_argument("--trade-cooldown-seconds", type=int, default=300)
    parser.add_argument("--maximum-trades-per-day", type=int, default=8)
    parser.add_argument("--daily-net-loss-limit", type=Decimal, default=Decimal("1.00"))
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
