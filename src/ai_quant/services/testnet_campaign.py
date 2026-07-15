"""Run a bounded multi-day Testnet PA/OF observation and micro-position campaign."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from queue import Empty, SimpleQueue
from typing import Any, cast

from ai_quant.binance_egress.structural_experiment import run_structural_experiment
from ai_quant.binance_egress.testnet_probe import BinanceTestnetClient, _credential
from ai_quant.binance_egress.testnet_stream import TestnetAggregateTradeStream
from ai_quant.features.price_action import Direction
from ai_quant.notifications import (
    Notification,
    OutboundNotifier,
    TelegramDeliveryError,
    TelegramFileConfig,
    TelegramSender,
)
from ai_quant.strategy.testnet_baseline import (
    TESTNET_EXPERIMENT_STRATEGY_VERSION,
    TESTNET_EXPERIMENT_SYMBOLS,
    TESTNET_IMPULSE_ENTRY_SYMBOLS,
    TestnetBaselineDecision,
    TestnetSignalParameters,
    build_market_impulse_plan,
    evaluate_testnet_baseline,
)


@dataclass(frozen=True, slots=True)
class CampaignLimits:
    duration_seconds: int = 259_200
    evaluation_interval_seconds: int = 10
    trade_cooldown_seconds: int = 60
    maximum_trades_per_day: int = 100
    daily_net_loss_limit: Decimal = Decimal("1.00")
    margin_budget: Decimal = Decimal("1")
    maximum_net_loss_per_trade: Decimal = Decimal("1.00")
    maximum_parallel_positions: int = 5
    maximum_candidates_per_round: int = 2
    signal_confirmation_rounds: int = 3
    impulse_confirmation_rounds: int = 1
    minimum_signal_quality_score: Decimal = Decimal("2.00")
    minimum_estimated_net_target: Decimal = Decimal("0.10")
    risk_sizing_slippage_bps: Decimal = Decimal("12.00")
    maximum_entry_spread_bps: Decimal = Decimal("5.00")
    minimum_trade_imbalance: Decimal = Decimal("0.25")
    minimum_book_imbalance: Decimal = Decimal("0.03")
    minimum_microprice_bps: Decimal = Decimal("0.10")
    maximum_opposing_book_imbalance: Decimal = Decimal("0.05")
    maximum_opposing_microprice_bps: Decimal = Decimal("0.25")
    aggressive_notional_lookback_rounds: int = 12
    minimum_aggressive_notional_samples: int = 6
    minimum_aggressive_notional_ratio: Decimal = Decimal("2.00")
    impulse_minimum_activity_ratio: Decimal = Decimal("1.25")
    impulse_lookback_rounds: int = 5
    impulse_minimum_momentum_bps: Decimal = Decimal("2.00")
    impulse_minimum_breadth_count: int = 3

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
        if self.margin_budget <= 0 or self.margin_budget > Decimal("1"):
            raise ValueError("campaign margin budget is invalid")
        if self.maximum_net_loss_per_trade <= 0 or self.maximum_net_loss_per_trade > Decimal("1"):
            raise ValueError("campaign per-trade loss budget is invalid")
        if not 1 <= self.maximum_parallel_positions <= 10:
            raise ValueError("campaign parallel position limit is invalid")
        if not 1 <= self.maximum_candidates_per_round <= 2:
            raise ValueError("campaign candidate limit must be one or two")
        if not 1 <= self.signal_confirmation_rounds <= 10:
            raise ValueError("campaign signal confirmation count is invalid")
        if not 1 <= self.impulse_confirmation_rounds <= self.signal_confirmation_rounds:
            raise ValueError("campaign impulse confirmation count is invalid")
        if not Decimal(0) <= self.minimum_signal_quality_score <= Decimal(20):
            raise ValueError("campaign signal quality threshold is invalid")
        if not Decimal(0) <= self.minimum_estimated_net_target <= Decimal(1):
            raise ValueError("campaign estimated net target is invalid")
        if not Decimal(2) <= self.risk_sizing_slippage_bps <= Decimal(100):
            raise ValueError("campaign risk sizing slippage is invalid")
        if not 6 <= self.aggressive_notional_lookback_rounds <= 120:
            raise ValueError("campaign activity lookback is invalid")
        if (
            not 3
            <= self.minimum_aggressive_notional_samples
            <= (self.aggressive_notional_lookback_rounds)
        ):
            raise ValueError("campaign activity sample count is invalid")
        if not Decimal(0) < self.minimum_aggressive_notional_ratio <= Decimal(10):
            raise ValueError("campaign activity ratio is invalid")
        if not Decimal(0) < self.impulse_minimum_activity_ratio <= Decimal(10):
            raise ValueError("campaign impulse activity ratio is invalid")
        if not 4 <= self.impulse_lookback_rounds <= 12:
            raise ValueError("campaign impulse lookback is invalid")
        if not Decimal(0) < self.impulse_minimum_momentum_bps <= Decimal(20):
            raise ValueError("campaign impulse momentum threshold is invalid")
        if not 3 <= self.impulse_minimum_breadth_count <= len(TESTNET_EXPERIMENT_SYMBOLS):
            raise ValueError("campaign impulse breadth threshold is invalid")
        TestnetSignalParameters(
            maximum_spread_bps=self.maximum_entry_spread_bps,
            minimum_trade_imbalance=self.minimum_trade_imbalance,
            minimum_book_imbalance=self.minimum_book_imbalance,
            minimum_microprice_bps=self.minimum_microprice_bps,
            maximum_opposing_book_imbalance=self.maximum_opposing_book_imbalance,
            maximum_opposing_microprice_bps=self.maximum_opposing_microprice_bps,
        )


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
        if symbols != TESTNET_EXPERIMENT_SYMBOLS:
            raise ValueError("campaign symbols must match the fixed V4 universe")
        self.symbols = symbols
        self.limits = limits
        config = TelegramFileConfig.load(token_file, chat_ids_file)
        self.notifier = OutboundNotifier(TelegramSender(config))
        key = _credential(api_key_file, repository_root)
        secret = _credential(api_secret_file, repository_root)
        self.client = BinanceTestnetClient(key, secret)
        self.trade_stream = TestnetAggregateTradeStream(symbols)
        self.trade_executor = ThreadPoolExecutor(
            max_workers=min(limits.maximum_parallel_positions, len(symbols)),
            thread_name_prefix="testnet-experiment",
        )
        self.active_trades: dict[str, Future[dict[str, Any]]] = {}
        self.position_events: SimpleQueue[dict[str, Any]] = SimpleQueue()
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
                f"运行模式: Testnet 实验下单 (最多 {self._parallel_limit()} 个币种并行)\n"
                "仓位规则: 0 到上限均正常, 不为补满仓位而开仓\n"
                f"信号确认: 连续 {self.limits.signal_confirmation_rounds} 轮同方向且质量分不低于 "
                f"{self.limits.minimum_signal_quality_score}\n"
                "决策来源: Testnet 确定性规则策略 (不依赖 Codex 在线状态)\n"
                f"单笔保证金: 最高 {self.limits.margin_budget} USDT\n"
                "杠杆: 各币种 Testnet 当前允许的最高初始杠杆\n"
                f"单笔预计净亏损预算: {self.limits.maximum_net_loss_per_trade} USDT\n"
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
            self._drain_position_events()
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
        self._drain_position_events()
        self._reap_completed_trades(state)
        _, server_offset_ms = self.client.synchronize_time()
        worker_count = self._parallel_limit()
        with ThreadPoolExecutor(
            max_workers=worker_count, thread_name_prefix="testnet-observation"
        ) as executor:
            futures = {
                symbol: executor.submit(self._observe_symbol, symbol, server_offset_ms)
                for symbol in self.symbols
            }
            decisions = [futures[symbol].result() for symbol in self.symbols]
        decisions = _apply_market_impulse_plans(
            state,
            decisions,
            limits=self.limits,
            evaluation_round=int(state["evaluation_round_count"]) + 1,
        )
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
        confirmed = _update_pending_signals(
            state,
            decisions,
            active_symbols=set(self.active_trades),
            evaluation_round=int(state["evaluation_round_count"]),
            required_rounds=self.limits.signal_confirmation_rounds,
            minimum_quality_score=self.limits.minimum_signal_quality_score,
            activity_lookback_rounds=self.limits.aggressive_notional_lookback_rounds,
            minimum_activity_samples=self.limits.minimum_aggressive_notional_samples,
            minimum_activity_ratio=self.limits.minimum_aggressive_notional_ratio,
            impulse_required_rounds=self.limits.impulse_confirmation_rounds,
            impulse_minimum_activity_ratio=self.limits.impulse_minimum_activity_ratio,
        )
        available = min(
            self._parallel_limit() - len(self.active_trades),
            self.limits.maximum_candidates_per_round,
        )
        if available <= 0:
            return
        candidates = sorted(
            confirmed,
            key=_experimental_candidate_rank,
        )
        last_by_symbol = cast(dict[str, str], state.setdefault("last_trade_by_symbol", {}))
        for decision in candidates:
            if available <= 0:
                break
            plan = decision.experimental_plan
            if plan is None:
                continue
            pending = cast(dict[str, dict[str, object]], state["pending_signals"])
            signal_state = pending.get(plan.symbol, {})
            plan = replace(
                plan,
                signal_confirmation_rounds=int(str(signal_state.get("consecutive_rounds", 1))),
                aggressive_notional_ratio=Decimal(
                    str(signal_state.get("aggressive_notional_ratio", "0"))
                ),
            )
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
                maximum_net_loss=self.limits.maximum_net_loss_per_trade,
                minimum_estimated_net_target=self.limits.minimum_estimated_net_target,
                risk_sizing_slippage_rate=(self.limits.risk_sizing_slippage_bps / Decimal(10_000)),
                on_position_protected=self.position_events.put,
                stop_requested=lambda: self.stop_requested,
            )
            self.active_trades[plan.symbol] = future
            pending.pop(plan.symbol, None)
            state["submitted_trade_count"] = int(state.get("submitted_trade_count", 0)) + 1
            timestamp = observed_at.isoformat().replace("+00:00", "Z")
            last_by_symbol[plan.symbol] = timestamp
            state["last_trade_at"] = timestamp
            event = plan.evidence()
            event.update(
                {
                    "record_type": "TESTNET_EXPERIMENT_SUBMITTED",
                    "observed_at": timestamp,
                    "decision_authority": "TESTNET_DETERMINISTIC_RULE",
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
                    f"信号质量分: {plan.signal_quality_score.quantize(Decimal('0.01'))}\n"
                    f"连续确认: {plan.signal_confirmation_rounds} 轮\n"
                    f"PA 同向周期: {plan.pa_alignment_count}/2\n"
                    f"信号类型: {_setup_type_cn(plan.setup_type)}\n"
                    f"联动同向币数: {plan.market_breadth_count}\n"
                    f"短周期动量: {plan.market_momentum_bps.quantize(Decimal('0.01'))} bps\n"
                    "主动成交活跃度: "
                    f"{plan.aggressive_notional_ratio.quantize(Decimal('0.01'))}x 近期中位数\n"
                    "决策来源: Testnet 确定性规则策略 (不依赖 Codex)\n"
                    "实际杠杆、数量和保证金将在仓位保护确认后通知。"
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
                    f"杠杆: {result['initial_leverage']}x (交易所当前最大)\n"
                    f"数量: {result['quantity']}\n"
                    f"名义价值: {_money(result['position_notional'])} USDT\n"
                    f"实际初始保证金: {_money(result['actual_initial_margin'])} USDT\n"
                    f"入场价: {result['entry_price']}\n"
                    f"平仓价: {result['exit_price']}\n"
                    f"止盈触发价: {result['target_trigger']}\n"
                    f"止损触发价: {result['stop_trigger']}\n"
                    f"退出原因: {_exit_reason_cn(str(result['exit_reason']))}\n"
                    f"已实现盈亏: {_money(result['realized_pnl'])} USDT\n"
                    f"手续费: {_money(result['commission_paid'])} USDT\n"
                    f"净结果: {_money(result['net_pnl'])} USDT\n"
                    "决策来源: Testnet 确定性规则策略"
                ),
                key=f"experiment-result-{symbol}-{result['completed_at']}",
            )

    def _drain_position_events(self) -> None:
        while True:
            try:
                event = self.position_events.get_nowait()
            except Empty:
                return
            self._append_event(event)
            self._notify(
                severity="INFO",
                event_type="测试网仓位已建立并完成保护",
                summary=(
                    f"交易对: {event['symbol']}\n"
                    f"方向: {'做多' if event['direction'] == 'LONG' else '做空'}\n"
                    f"杠杆: {event['initial_leverage']}x (交易所当前最大)\n"
                    f"数量: {event['quantity']}\n"
                    f"名义价值: {_money(event['position_notional'])} USDT\n"
                    f"实际初始保证金: {_money(event['actual_initial_margin'])} USDT\n"
                    "信号质量分: "
                    f"{Decimal(str(event['signal_quality_score'])).quantize(Decimal('0.01'))}\n"
                    "信号类型: "
                    f"{_setup_type_cn(str(event.get('setup_type', 'TREND_CONFIRMATION')))}\n"
                    f"联动同向币数: {event.get('market_breadth_count', 0)}\n"
                    "短周期动量: "
                    f"{_two_decimals(event.get('market_momentum_bps', 0))} bps\n"
                    f"连续确认: {event['signal_confirmation_rounds']} 轮\n"
                    f"PA 同向周期: {event['pa_alignment_count']}/2\n"
                    "主动成交活跃度: "
                    f"{_two_decimals(event['aggressive_notional_ratio'])}x "
                    "近期中位数\n"
                    f"成交入场价: {event['entry_price']}\n"
                    f"止盈触发价: {event['target_trigger']}\n"
                    f"预计止盈毛收益: {_money(event['estimated_target_gross_pnl'])} USDT\n"
                    f"预计扣费滑点后止盈: {_money(event['estimated_target_net_pnl'])} USDT\n"
                    f"止损触发价: {event['stop_trigger']}\n"
                    f"预计止损净亏损: {_money(event['estimated_stop_net_loss'])} USDT\n"
                    "保护状态: 交易所原生止盈/止损均已确认\n"
                    "触发价格: 合约成交价 (CONTRACT_PRICE)\n"
                    "决策来源: Testnet 确定性规则策略 (不依赖 Codex)"
                ),
                key=f"experiment-protected-{event['symbol']}-{event['protected_at']}",
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
            signal_parameters=TestnetSignalParameters(
                maximum_spread_bps=self.limits.maximum_entry_spread_bps,
                minimum_trade_imbalance=self.limits.minimum_trade_imbalance,
                minimum_book_imbalance=self.limits.minimum_book_imbalance,
                minimum_microprice_bps=self.limits.minimum_microprice_bps,
                maximum_opposing_book_imbalance=(self.limits.maximum_opposing_book_imbalance),
                maximum_opposing_microprice_bps=(self.limits.maximum_opposing_microprice_bps),
            ),
        )

    def _parallel_limit(self) -> int:
        return min(self.limits.maximum_parallel_positions, len(self.symbols))

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
            if (
                document.get("status") == "RUNNING"
                and document.get("symbols") == list(self.symbols)
                and document.get("strategy") == TESTNET_EXPERIMENT_STRATEGY_VERSION
            ):
                document["limits"] = self._limits_document()
                document["strategy"] = TESTNET_EXPERIMENT_STRATEGY_VERSION
                document["validation_status"] = "UNVALIDATED_TESTNET_EXPERIMENT"
                document["decision_authority"] = "TESTNET_DETERMINISTIC_RULE"
                document["codex_dependency"] = False
                document.setdefault("last_trade_by_symbol", {})
                document.setdefault("active_symbols", [])
                document.setdefault("pending_signals", {})
                document.setdefault("aggressive_notional_history", {})
                document.setdefault("mid_price_history", {})
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
            "strategy": TESTNET_EXPERIMENT_STRATEGY_VERSION,
            "validation_status": "UNVALIDATED_TESTNET_EXPERIMENT",
            "decision_authority": "TESTNET_DETERMINISTIC_RULE",
            "codex_dependency": False,
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
            "pending_signals": {},
            "aggressive_notional_history": {},
            "mid_price_history": {},
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
            "maximum_net_loss_per_trade": format(self.limits.maximum_net_loss_per_trade, "f"),
            "execution_mode": "TESTNET_EXPERIMENT",
            "decision_authority": "TESTNET_DETERMINISTIC_RULE",
            "codex_dependency": False,
            "maximum_parallel_observations": self._parallel_limit(),
            "maximum_parallel_positions": self._parallel_limit(),
            "maximum_candidates_per_round": self.limits.maximum_candidates_per_round,
            "position_slots_are_target": False,
            "signal_confirmation_rounds": self.limits.signal_confirmation_rounds,
            "impulse_confirmation_rounds": self.limits.impulse_confirmation_rounds,
            "minimum_signal_quality_score": format(self.limits.minimum_signal_quality_score, "f"),
            "minimum_estimated_net_target": format(self.limits.minimum_estimated_net_target, "f"),
            "risk_sizing_slippage_bps": format(self.limits.risk_sizing_slippage_bps, "f"),
            "maximum_entry_spread_bps": format(self.limits.maximum_entry_spread_bps, "f"),
            "minimum_trade_imbalance": format(self.limits.minimum_trade_imbalance, "f"),
            "minimum_book_imbalance": format(self.limits.minimum_book_imbalance, "f"),
            "minimum_microprice_bps": format(self.limits.minimum_microprice_bps, "f"),
            "maximum_opposing_book_imbalance": format(
                self.limits.maximum_opposing_book_imbalance, "f"
            ),
            "maximum_opposing_microprice_bps": format(
                self.limits.maximum_opposing_microprice_bps, "f"
            ),
            "aggressive_notional_lookback_rounds": (
                self.limits.aggressive_notional_lookback_rounds
            ),
            "minimum_aggressive_notional_samples": (
                self.limits.minimum_aggressive_notional_samples
            ),
            "minimum_aggressive_notional_ratio": format(
                self.limits.minimum_aggressive_notional_ratio, "f"
            ),
            "impulse_minimum_activity_ratio": format(
                self.limits.impulse_minimum_activity_ratio, "f"
            ),
            "impulse_lookback_rounds": self.limits.impulse_lookback_rounds,
            "impulse_minimum_momentum_bps": format(
                self.limits.impulse_minimum_momentum_bps, "f"
            ),
            "impulse_minimum_breadth_count": self.limits.impulse_minimum_breadth_count,
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
        "决策来源: Testnet 确定性规则策略 (不依赖 Codex)\n"
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


def _update_pending_signals(
    state: dict[str, Any],
    decisions: list[TestnetBaselineDecision],
    *,
    active_symbols: set[str],
    evaluation_round: int,
    required_rounds: int,
    minimum_quality_score: Decimal,
    activity_lookback_rounds: int,
    minimum_activity_samples: int,
    minimum_activity_ratio: Decimal,
    impulse_required_rounds: int | None = None,
    impulse_minimum_activity_ratio: Decimal | None = None,
) -> list[TestnetBaselineDecision]:
    """Require consecutive same-direction evidence without treating slots as a target."""
    pending = cast(dict[str, dict[str, object]], state.setdefault("pending_signals", {}))
    activity_history = cast(
        dict[str, list[str]], state.setdefault("aggressive_notional_history", {})
    )
    current_symbols = {decision.symbol for decision in decisions}
    for symbol in list(pending):
        if symbol not in current_symbols or symbol in active_symbols:
            pending.pop(symbol, None)

    confirmed: list[TestnetBaselineDecision] = []
    for decision in decisions:
        plan = decision.experimental_plan
        history = activity_history.setdefault(decision.symbol, [])
        current_activity = decision.order_flow.aggressive_notional
        history.append(format(current_activity, "f"))
        del history[:-activity_lookback_rounds]
        activity_values = sorted(Decimal(value) for value in history)
        activity_median = _median_decimal(activity_values)
        activity_ratio = Decimal(0) if activity_median <= 0 else current_activity / activity_median
        is_impulse = plan is not None and plan.setup_type == "MARKET_BREADTH_IMPULSE"
        required = (
            impulse_required_rounds
            if is_impulse and impulse_required_rounds is not None
            else required_rounds
        )
        required_activity = (
            impulse_minimum_activity_ratio
            if is_impulse and impulse_minimum_activity_ratio is not None
            else minimum_activity_ratio
        )
        if (
            plan is None
            or plan.symbol in active_symbols
            or plan.signal_quality_score < minimum_quality_score
            or len(activity_values) < minimum_activity_samples
            or activity_ratio < required_activity
        ):
            pending.pop(decision.symbol, None)
            continue
        previous = pending.get(plan.symbol)
        consecutive = 1
        if (
            previous is not None
            and previous.get("direction") == str(plan.direction)
            and int(str(previous.get("evaluation_round", -1))) == evaluation_round - 1
        ):
            consecutive = int(str(previous.get("consecutive_rounds", 0))) + 1
        pending[plan.symbol] = {
            "direction": str(plan.direction),
            "consecutive_rounds": consecutive,
            "evaluation_round": evaluation_round,
            "last_observed_at": decision.observed_at.isoformat().replace("+00:00", "Z"),
            "signal_quality_score": format(plan.signal_quality_score, "f"),
            "pa_alignment_count": plan.pa_alignment_count,
            "aggressive_notional": format(current_activity, "f"),
            "aggressive_notional_median": format(activity_median, "f"),
            "aggressive_notional_ratio": format(activity_ratio, "f"),
        }
        if consecutive >= required:
            confirmed.append(decision)
    return confirmed


def _apply_market_impulse_plans(
    state: dict[str, Any],
    decisions: list[TestnetBaselineDecision],
    *,
    limits: CampaignLimits,
    evaluation_round: int,
) -> list[TestnetBaselineDecision]:
    """Promote the strongest breadth-aligned neutral-PA impulses to Testnet plans."""
    histories = cast(
        dict[str, list[dict[str, str | int]]], state.setdefault("mid_price_history", {})
    )
    momentum_by_symbol: dict[str, Decimal] = {}
    for decision in decisions:
        history = histories.setdefault(decision.symbol, [])
        history.append(
            {
                "evaluation_round": evaluation_round,
                "mid_price": format(decision.mid_price, "f"),
            }
        )
        del history[:-limits.impulse_lookback_rounds]
        if len(history) < limits.impulse_lookback_rounds:
            continue
        start = Decimal(str(history[0]["mid_price"]))
        momentum_by_symbol[decision.symbol] = (
            (decision.mid_price / start - Decimal(1)) * Decimal(10_000)
        )
    long_breadth = sum(
        value >= limits.impulse_minimum_momentum_bps for value in momentum_by_symbol.values()
    )
    short_breadth = sum(
        value <= -limits.impulse_minimum_momentum_bps for value in momentum_by_symbol.values()
    )
    if max(long_breadth, short_breadth) < limits.impulse_minimum_breadth_count:
        return decisions
    if long_breadth == short_breadth:
        return decisions
    direction = Direction.LONG if long_breadth > short_breadth else Direction.SHORT
    breadth_count = max(long_breadth, short_breadth)
    parameters = TestnetSignalParameters(
        maximum_spread_bps=limits.maximum_entry_spread_bps,
        minimum_trade_imbalance=limits.minimum_trade_imbalance,
        minimum_book_imbalance=limits.minimum_book_imbalance,
        minimum_microprice_bps=limits.minimum_microprice_bps,
        maximum_opposing_book_imbalance=limits.maximum_opposing_book_imbalance,
        maximum_opposing_microprice_bps=limits.maximum_opposing_microprice_bps,
    )
    promoted: list[TestnetBaselineDecision] = []
    for decision in decisions:
        momentum = momentum_by_symbol.get(decision.symbol, Decimal(0))
        aligned = (
            momentum >= limits.impulse_minimum_momentum_bps
            if direction is Direction.LONG
            else momentum <= -limits.impulse_minimum_momentum_bps
        )
        plan = decision.experimental_plan
        if plan is None and aligned and decision.symbol in TESTNET_IMPULSE_ENTRY_SYMBOLS:
            plan = build_market_impulse_plan(
                decision,
                direction=direction,
                momentum_bps=momentum,
                breadth_count=breadth_count,
                parameters=parameters,
            )
        promoted.append(replace(decision, experimental_plan=plan))
    return promoted


def _median_decimal(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal(0)
    midpoint = len(values) // 2
    if len(values) % 2:
        return values[midpoint]
    return (values[midpoint - 1] + values[midpoint]) / Decimal(2)


def _experimental_candidate_rank(decision: TestnetBaselineDecision) -> tuple[Decimal, str]:
    """Prefer persistent PA alignment and stronger executable order flow."""
    plan = decision.experimental_plan
    if plan is None:
        return Decimal("Infinity"), decision.symbol
    return -plan.signal_quality_score, decision.symbol


def _setup_type_cn(value: str) -> str:
    return {
        "TREND_CONFIRMATION": "趋势确认",
        "MARKET_BREADTH_IMPULSE": "多币联动冲量",
    }.get(value, value)


def _money(value: object) -> str:
    return format(Decimal(str(value)).quantize(Decimal("0.000001")), "f")


def _two_decimals(value: object) -> str:
    return format(Decimal(str(value)).quantize(Decimal("0.01")), "f")


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
    parser.add_argument("--symbols", default=",".join(TESTNET_EXPERIMENT_SYMBOLS))
    parser.add_argument("--duration-seconds", type=int, default=259_200)
    parser.add_argument("--evaluation-interval-seconds", type=int, default=60)
    parser.add_argument("--trade-cooldown-seconds", type=int, default=300)
    parser.add_argument("--maximum-trades-per-day", type=int, default=8)
    parser.add_argument("--daily-net-loss-limit", type=Decimal, default=Decimal("1.00"))
    parser.add_argument("--margin-budget", type=Decimal, default=Decimal("1"))
    parser.add_argument("--maximum-net-loss-per-trade", type=Decimal, default=Decimal("1.00"))
    parser.add_argument("--maximum-parallel-positions", type=int, default=5)
    parser.add_argument("--maximum-candidates-per-round", type=int, default=2)
    parser.add_argument("--signal-confirmation-rounds", type=int, default=2)
    parser.add_argument("--impulse-confirmation-rounds", type=int, default=1)
    parser.add_argument("--minimum-signal-quality-score", type=Decimal, default=Decimal("2.00"))
    parser.add_argument("--minimum-estimated-net-target", type=Decimal, default=Decimal("0.10"))
    parser.add_argument("--risk-sizing-slippage-bps", type=Decimal, default=Decimal("12.00"))
    parser.add_argument("--maximum-entry-spread-bps", type=Decimal, default=Decimal("8.00"))
    parser.add_argument("--minimum-trade-imbalance", type=Decimal, default=Decimal("0.25"))
    parser.add_argument("--minimum-book-imbalance", type=Decimal, default=Decimal("0.03"))
    parser.add_argument("--minimum-microprice-bps", type=Decimal, default=Decimal("0.10"))
    parser.add_argument("--maximum-opposing-book-imbalance", type=Decimal, default=Decimal("0.05"))
    parser.add_argument("--maximum-opposing-microprice-bps", type=Decimal, default=Decimal("0.25"))
    parser.add_argument("--aggressive-notional-lookback-rounds", type=int, default=12)
    parser.add_argument("--minimum-aggressive-notional-samples", type=int, default=6)
    parser.add_argument(
        "--minimum-aggressive-notional-ratio", type=Decimal, default=Decimal("0.50")
    )
    parser.add_argument("--impulse-minimum-activity-ratio", type=Decimal, default=Decimal("1.25"))
    parser.add_argument("--impulse-lookback-rounds", type=int, default=5)
    parser.add_argument("--impulse-minimum-momentum-bps", type=Decimal, default=Decimal("2.00"))
    parser.add_argument("--impulse-minimum-breadth-count", type=int, default=3)
    arguments = parser.parse_args()
    limits = CampaignLimits(
        duration_seconds=arguments.duration_seconds,
        evaluation_interval_seconds=arguments.evaluation_interval_seconds,
        trade_cooldown_seconds=arguments.trade_cooldown_seconds,
        maximum_trades_per_day=arguments.maximum_trades_per_day,
        daily_net_loss_limit=arguments.daily_net_loss_limit,
        margin_budget=arguments.margin_budget,
        maximum_net_loss_per_trade=arguments.maximum_net_loss_per_trade,
        maximum_parallel_positions=arguments.maximum_parallel_positions,
        maximum_candidates_per_round=arguments.maximum_candidates_per_round,
        signal_confirmation_rounds=arguments.signal_confirmation_rounds,
        impulse_confirmation_rounds=arguments.impulse_confirmation_rounds,
        minimum_signal_quality_score=arguments.minimum_signal_quality_score,
        minimum_estimated_net_target=arguments.minimum_estimated_net_target,
        risk_sizing_slippage_bps=arguments.risk_sizing_slippage_bps,
        maximum_entry_spread_bps=arguments.maximum_entry_spread_bps,
        minimum_trade_imbalance=arguments.minimum_trade_imbalance,
        minimum_book_imbalance=arguments.minimum_book_imbalance,
        minimum_microprice_bps=arguments.minimum_microprice_bps,
        maximum_opposing_book_imbalance=arguments.maximum_opposing_book_imbalance,
        maximum_opposing_microprice_bps=arguments.maximum_opposing_microprice_bps,
        aggressive_notional_lookback_rounds=(arguments.aggressive_notional_lookback_rounds),
        minimum_aggressive_notional_samples=arguments.minimum_aggressive_notional_samples,
        minimum_aggressive_notional_ratio=arguments.minimum_aggressive_notional_ratio,
        impulse_minimum_activity_ratio=arguments.impulse_minimum_activity_ratio,
        impulse_lookback_rounds=arguments.impulse_lookback_rounds,
        impulse_minimum_momentum_bps=arguments.impulse_minimum_momentum_bps,
        impulse_minimum_breadth_count=arguments.impulse_minimum_breadth_count,
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
