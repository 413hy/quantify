"""Run a continuous or bounded Testnet PA/OF observation campaign."""

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

from ai_quant.binance_egress.structural_experiment import (
    PositionSignalControl,
    resume_protected_structural_experiment,
    run_structural_experiment,
)
from ai_quant.binance_egress.testnet_probe import (
    BinanceTestnetClient,
    TestnetProbeError,
    _credential,
    _flatten_position,
)
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
    TestnetExperimentalPlan,
    TestnetSignalParameters,
    build_market_impulse_plan,
    evaluate_testnet_baseline,
)


@dataclass(frozen=True, slots=True)
class CampaignLimits:
    duration_seconds: int = 0
    evaluation_interval_seconds: int = 10
    trade_cooldown_seconds: int = 0
    maximum_trades_per_day: int = 100
    daily_net_loss_limit: Decimal = Decimal("1.00")
    margin_budget: Decimal = Decimal("1")
    maximum_net_loss_per_trade: Decimal = Decimal("1.00")
    maximum_parallel_positions: int = 5
    maximum_candidates_per_round: int = 5
    same_direction_scale_enabled: bool = False
    automatic_reversal_entry_enabled: bool = True
    signal_confirmation_rounds: int = 3
    impulse_confirmation_rounds: int = 1
    minimum_signal_quality_score: Decimal = Decimal("2.00")
    minimum_directional_forecast_bps: Decimal = Decimal("2.00")
    impulse_minimum_directional_forecast_bps: Decimal = Decimal("0.10")
    continuation_minimum_directional_forecast_bps: Decimal = Decimal("2.00")
    structure_substitute_minimum_directional_forecast_bps: Decimal = Decimal("3.00")
    structure_substitute_minimum_trade_imbalance: Decimal = Decimal("0.75")
    structure_substitute_minimum_secondary_flow: Decimal = Decimal("0.10")
    minimum_target_feasibility_rate_15m: Decimal = Decimal("0.20")
    impulse_minimum_target_feasibility_rate_15m: Decimal = Decimal("0.02")
    minimum_estimated_net_target: Decimal = Decimal("0.10")
    minimum_net_reward_risk_ratio: Decimal = Decimal("0.50")
    impulse_minimum_net_reward_risk_ratio: Decimal = Decimal("0.15")
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
    activity_filter_enabled: bool = False
    impulse_activity_filter_enabled: bool = False
    impulse_minimum_activity_ratio: Decimal = Decimal("0.10")
    impulse_maximum_activity_ratio: Decimal = Decimal("10.00")
    impulse_lookback_rounds: int = 10
    impulse_minimum_momentum_bps: Decimal = Decimal("2.00")
    impulse_maximum_momentum_bps: Decimal = Decimal("8.00")
    impulse_minimum_breadth_count: int = 3
    sustained_lookback_rounds: int = 22
    sustained_minimum_momentum_bps: Decimal = Decimal("5.00")
    pullback_minimum_bps: Decimal = Decimal("3.00")
    pullback_resumption_bps: Decimal = Decimal("0.50")
    pullback_maximum_bps: Decimal = Decimal("40.00")
    pullback_setup_maximum_rounds: int = 60
    signal_evidence_window_rounds: int = 6
    continuation_minimum_breadth_count: int = 4
    continuation_confirmation_rounds: int = 4
    continuation_minimum_momentum_bps: Decimal = Decimal("4.00")
    continuation_maximum_momentum_bps: Decimal = Decimal("15.00")
    position_failed_followthrough_peak_bps: Decimal = Decimal("6.00")
    position_adverse_invalidation_bps: Decimal = Decimal("10.00")
    position_profit_protection_peak_bps: Decimal = Decimal("20.00")
    position_profit_giveback_bps: Decimal = Decimal("10.00")
    position_opposition_confirmation_rounds: int = 2

    def __post_init__(self) -> None:
        if self.duration_seconds != 0 and not 60 <= self.duration_seconds <= 604_800:
            raise ValueError(
                "campaign duration must be zero (continuous) or between 60 seconds and 7 days"
            )
        if not 10 <= self.evaluation_interval_seconds <= 3_600:
            raise ValueError("campaign evaluation interval is invalid")
        if not 0 <= self.trade_cooldown_seconds <= 86_400:
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
        if not 1 <= self.maximum_candidates_per_round <= len(TESTNET_EXPERIMENT_SYMBOLS):
            raise ValueError("campaign candidate limit exceeds the fixed symbol universe")
        if not 1 <= self.signal_confirmation_rounds <= 10:
            raise ValueError("campaign signal confirmation count is invalid")
        if not 1 <= self.impulse_confirmation_rounds <= self.signal_confirmation_rounds:
            raise ValueError("campaign impulse confirmation count is invalid")
        if not Decimal(0) <= self.minimum_signal_quality_score <= Decimal(20):
            raise ValueError("campaign signal quality threshold is invalid")
        if not Decimal(0) <= self.minimum_directional_forecast_bps <= Decimal(20):
            raise ValueError("campaign directional forecast threshold is invalid")
        if not Decimal(0) < self.impulse_minimum_directional_forecast_bps <= Decimal(5):
            raise ValueError("campaign impulse forecast threshold is invalid")
        if not Decimal(0) < self.continuation_minimum_directional_forecast_bps <= Decimal(10):
            raise ValueError("campaign continuation forecast threshold is invalid")
        if not (
            self.continuation_minimum_directional_forecast_bps
            < self.structure_substitute_minimum_directional_forecast_bps
            <= Decimal(20)
        ):
            raise ValueError("campaign structure substitute forecast threshold is invalid")
        if not Decimal(0) < self.structure_substitute_minimum_trade_imbalance <= Decimal(1):
            raise ValueError("campaign structure substitute trade threshold is invalid")
        if not Decimal(0) < self.structure_substitute_minimum_secondary_flow <= Decimal(5):
            raise ValueError("campaign structure substitute flow threshold is invalid")
        if not Decimal(0) <= self.minimum_target_feasibility_rate_15m <= Decimal(1):
            raise ValueError("campaign target feasibility threshold is invalid")
        if not Decimal(0) <= self.impulse_minimum_target_feasibility_rate_15m <= Decimal(1):
            raise ValueError("campaign impulse target feasibility threshold is invalid")
        if not Decimal(0) <= self.minimum_estimated_net_target <= Decimal(1):
            raise ValueError("campaign estimated net target is invalid")
        if not Decimal(0) <= self.minimum_net_reward_risk_ratio <= Decimal(1):
            raise ValueError("campaign net reward-risk threshold is invalid")
        if not Decimal(0) <= self.impulse_minimum_net_reward_risk_ratio <= Decimal(1):
            raise ValueError("campaign impulse net reward-risk threshold is invalid")
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
        if (
            not self.impulse_minimum_activity_ratio
            < self.impulse_maximum_activity_ratio
            <= Decimal(100)
        ):
            raise ValueError("campaign impulse activity ceiling is invalid")
        if not 4 <= self.impulse_lookback_rounds <= 12:
            raise ValueError("campaign impulse lookback is invalid")
        if not Decimal(0) < self.impulse_minimum_momentum_bps <= Decimal(20):
            raise ValueError("campaign impulse momentum threshold is invalid")
        if not self.impulse_minimum_momentum_bps < self.impulse_maximum_momentum_bps <= Decimal(30):
            raise ValueError("campaign impulse exhaustion threshold is invalid")
        if not 3 <= self.impulse_minimum_breadth_count <= len(TESTNET_EXPERIMENT_SYMBOLS):
            raise ValueError("campaign impulse breadth threshold is invalid")
        if not self.impulse_lookback_rounds < self.sustained_lookback_rounds <= 30:
            raise ValueError("campaign sustained lookback is invalid")
        if not self.impulse_minimum_momentum_bps < self.sustained_minimum_momentum_bps:
            raise ValueError("campaign sustained momentum threshold is invalid")
        if not Decimal("0.50") <= self.pullback_minimum_bps <= Decimal("20"):
            raise ValueError("campaign pullback threshold is invalid")
        if not Decimal("0.10") <= self.pullback_resumption_bps <= Decimal("10"):
            raise ValueError("campaign pullback resumption threshold is invalid")
        if not self.pullback_minimum_bps < self.pullback_maximum_bps <= Decimal("120"):
            raise ValueError("campaign maximum pullback is invalid")
        if not 4 <= self.pullback_setup_maximum_rounds <= 120:
            raise ValueError("campaign pullback setup lifetime is invalid")
        if not 2 <= self.signal_evidence_window_rounds <= 30:
            raise ValueError("campaign signal evidence window is invalid")
        if not 4 <= self.continuation_minimum_breadth_count <= len(TESTNET_EXPERIMENT_SYMBOLS):
            raise ValueError("campaign continuation breadth threshold is invalid")
        if not 1 <= self.continuation_confirmation_rounds <= 12:
            raise ValueError("campaign continuation confirmation count is invalid")
        if not Decimal(0) < self.continuation_minimum_momentum_bps <= Decimal(20):
            raise ValueError("campaign continuation momentum threshold is invalid")
        if not (
            self.continuation_minimum_momentum_bps
            < self.continuation_maximum_momentum_bps
            <= Decimal(30)
        ):
            raise ValueError("campaign continuation exhaustion threshold is invalid")
        if not Decimal(0) < self.position_failed_followthrough_peak_bps <= Decimal(20):
            raise ValueError("campaign failed-followthrough peak is invalid")
        if not Decimal(0) < self.position_adverse_invalidation_bps <= Decimal(30):
            raise ValueError("campaign adverse invalidation threshold is invalid")
        if not (
            self.position_failed_followthrough_peak_bps
            < self.position_profit_protection_peak_bps
            <= Decimal(50)
        ):
            raise ValueError("campaign profit-protection peak is invalid")
        if not (
            Decimal(0)
            < self.position_profit_giveback_bps
            < self.position_profit_protection_peak_bps
        ):
            raise ValueError("campaign profit giveback threshold is invalid")
        if not 1 <= self.position_opposition_confirmation_rounds <= 6:
            raise ValueError("campaign position opposition confirmation count is invalid")
        TestnetSignalParameters(
            maximum_spread_bps=self.maximum_entry_spread_bps,
            minimum_trade_imbalance=self.minimum_trade_imbalance,
            minimum_book_imbalance=self.minimum_book_imbalance,
            minimum_microprice_bps=self.minimum_microprice_bps,
            maximum_opposing_book_imbalance=self.maximum_opposing_book_imbalance,
            maximum_opposing_microprice_bps=self.maximum_opposing_microprice_bps,
            minimum_target_feasibility_rate_15m=(self.minimum_target_feasibility_rate_15m),
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


def _trend_continuation_cooldown_waived(
    state: dict[str, Any], plan: TestnetExperimentalPlan
) -> bool:
    """Allow an immediate same-direction re-entry only after a native target hit.

    The waiver is deliberately narrow: a stop, invalidation, operator exit, or a
    weak three-symbol impulse still observes the normal per-symbol cooldown.
    """
    last_reasons = state.get("last_exit_reason_by_symbol")
    last_directions = state.get("last_completed_direction_by_symbol")
    if not isinstance(last_reasons, dict) or not isinstance(last_directions, dict):
        return False
    if last_reasons.get(plan.symbol) != "TAKE_PROFIT":
        return False
    if last_directions.get(plan.symbol) != str(plan.direction):
        return False
    return plan.setup_type == "MARKET_BREADTH_TREND" or plan.market_breadth_count >= 4


def _claim_signal_episode(state: dict[str, Any], symbol: str, episode_key: str) -> bool:
    """Atomically consume one independently confirmed signal episode.

    A ten-second evaluation loop may observe the same confirmed setup for many
    rounds.  Capacity and cooldown rules must not be used as a proxy for this
    identity: every distinct signal may trade, but one signal may submit only
    once even across a process restart.
    """
    consumed = cast(
        dict[str, str],
        state.setdefault("consumed_signal_episodes_by_symbol", {}),
    )
    if consumed.get(symbol) == episode_key:
        return False
    consumed[symbol] = episode_key
    return True


def _initialize_position_metric(state: dict[str, Any], event: dict[str, Any]) -> None:
    symbol = str(event["symbol"])
    metrics = cast(
        dict[str, dict[str, object]],
        state.setdefault("active_position_metrics", {}),
    )
    metrics[symbol] = {
        "direction": str(event["direction"]),
        "entry_price": str(event["entry_price"]),
        "peak_favorable_bps": "0",
        "current_favorable_bps": "0",
        "opposing_flow_rounds": 0,
        "last_evaluation_round": -1,
    }


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
        self.position_controls: dict[str, PositionSignalControl] = {}
        self.position_directions: dict[str, Direction] = {}
        self.reversal_plans: dict[str, TestnetExperimentalPlan] = {}
        self.protected_symbols: set[str] = set()
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
        recovered_symbols = self._reconcile_exchange_state(state)
        self._notify(
            severity="INFO",
            event_type="测试网实验交易已启动",
            summary=(
                f"候选池: {', '.join(self.symbols)}\n"
                f"节奏: 每 {self.limits.evaluation_interval_seconds} 秒评估, "
                "五币独立有效信号均可执行\n"
                "入场: 四币以上同向趋势出现后, 等回踩/反抽与再启动确认再市价成交\n"
                f"仓位: 最多 {self._parallel_limit()} 个; 单笔保证金不超过 "
                f"{self.limits.margin_budget} USDT\n"
                "持仓信号: 同一独立信号只执行一次; 不同币可并行, 反向完整信号先平后换向\n"
                "退出: 交易所原生止盈/止损或有效反向信号"
                + (f"\n重启接管: {', '.join(recovered_symbols)}" if recovered_symbols else "")
            ),
            key=f"campaign-start-{state['started_at']}",
        )
        consecutive_errors = 0
        result_code = 0
        last_heartbeat = datetime.now(UTC)
        while not self.stop_requested:
            now = datetime.now(UTC)
            deadline = _optional_time(state.get("ends_at"))
            if deadline is not None and now >= deadline:
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
            deadline = _optional_time(state.get("ends_at"))
            remaining = None if deadline is None else (deadline - now).total_seconds()
            if not self.stop_requested and (remaining is None or remaining > 0):
                time.sleep(
                    self.limits.evaluation_interval_seconds
                    if remaining is None
                    else min(self.limits.evaluation_interval_seconds, remaining)
                )
        stopped_by_operator = self.stop_requested
        self.stop_requested = True
        while self.active_trades:
            self._drain_position_events(state)
            self._reap_completed_trades(state)
            if self.active_trades:
                time.sleep(1)
        state["active_symbols"] = []
        state["pending_entry_symbols"] = []
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
        self._drain_position_events(state)
        self._reap_completed_trades(state)
        _, server_offset_ms = self.client.synchronize_time()
        worker_count = len(self.symbols)
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
        state["active_symbols"] = sorted(self.protected_symbols)
        state["pending_entry_symbols"] = sorted(set(self.active_trades) - self.protected_symbols)
        state["updated_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        _atomic_write_json(self.state_file, state)
        return state

    def _reconcile_exchange_state(self, state: dict[str, Any]) -> list[str]:
        """Adopt native-protected positions and clean stale system orders."""
        unresolved = self._unresolved_position_events()
        self.client.synchronize_time()
        if self.client.position_mode().get("dualSidePosition") is not False:
            raise TestnetProbeError("ACCOUNT_POSITION_MODE_NOT_ONE_WAY")
        recovered: list[str] = []
        for symbol in self.symbols:
            positions = self.client.position_risk(symbol)
            signed_quantity = sum(
                (Decimal(str(item.get("positionAmt", "0"))) for item in positions),
                Decimal(0),
            )
            open_orders = self.client.open_orders(symbol)
            open_algos = self.client.open_algo_orders(symbol)
            stop_algo, target_algo = _recovery_algo_pair(open_algos)
            event = unresolved.get(symbol)

            if signed_quantity == 0 and event is None:
                canceled = self._cancel_stale_system_orders(
                    symbol, open_orders=open_orders, open_algos=open_algos
                )
                if canceled:
                    self._append_event(
                        {
                            "record_type": "TESTNET_RESTART_STALE_ORDER_CLEANUP",
                            "environment": "testnet",
                            "symbol": symbol,
                            "canceled_order_count": canceled,
                            "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                            "production_endpoint_requests": 0,
                        }
                    )
                continue

            if event is None:
                event = self._synthetic_recovery_event(
                    symbol=symbol,
                    signed_quantity=signed_quantity,
                    positions=positions,
                    stop_algo=stop_algo,
                    target_algo=target_algo,
                )
            else:
                event = dict(event)
                _enrich_recovery_event_with_algos(event, stop_algo, target_algo)

            if signed_quantity != 0 and (open_orders or stop_algo is None or target_algo is None):
                closed = _flatten_position(self.client, symbol)
                if closed is None or closed.get("status") != "FILLED":
                    raise TestnetProbeError("TESTNET_RESTART_FAIL_CLOSED_NOT_FILLED")
                canceled = self._cancel_stale_system_orders(
                    symbol, open_orders=open_orders, open_algos=open_algos
                )
                self._append_event(
                    {
                        "record_type": "TESTNET_RESTART_AMBIGUOUS_POSITION_FLATTENED",
                        "environment": "testnet",
                        "symbol": symbol,
                        "signed_quantity": format(signed_quantity, "f"),
                        "canceled_order_count": canceled,
                        "reason_code": "NATIVE_PROTECTION_INCOMPLETE_OR_STANDARD_ORDER_PRESENT",
                        "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                        "production_endpoint_requests": 0,
                    }
                )

            control = PositionSignalControl()
            future = self.trade_executor.submit(
                resume_protected_structural_experiment,
                api_key_file=self.api_key_file,
                api_secret_file=self.api_secret_file,
                repository_root=self.repository_root,
                recovery_event=event,
                position_control=control,
                on_position_control=self.position_events.put,
                stop_requested=lambda: self.stop_requested,
                maximum_net_loss=self.limits.maximum_net_loss_per_trade,
                minimum_estimated_net_target=self.limits.minimum_estimated_net_target,
                risk_sizing_slippage_rate=(self.limits.risk_sizing_slippage_bps / Decimal(10_000)),
            )
            self.active_trades[symbol] = future
            self.position_controls[symbol] = control
            self.position_directions[symbol] = Direction(str(event["direction"]))
            if signed_quantity != 0:
                self.protected_symbols.add(symbol)
                _initialize_position_metric(state, event)
            recovered.append(symbol)
            self._append_event(
                {
                    "record_type": "TESTNET_POSITION_RECOVERY_STARTED",
                    "environment": "testnet",
                    "validation_status": "UNVALIDATED_TESTNET_EXPERIMENT",
                    "strategy": TESTNET_EXPERIMENT_STRATEGY_VERSION,
                    "symbol": symbol,
                    "position_was_open": signed_quantity != 0,
                    "native_stop_present": stop_algo is not None,
                    "native_target_present": target_algo is not None,
                    "recovered_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "production_endpoint_requests": 0,
                }
            )
        state["active_symbols"] = sorted(self.protected_symbols)
        state["pending_entry_symbols"] = sorted(set(self.active_trades) - self.protected_symbols)
        state["restart_recovered_symbols"] = recovered
        state["last_restart_reconciliation_at"] = (
            datetime.now(UTC).isoformat().replace("+00:00", "Z")
        )
        _atomic_write_json(self.state_file, state)
        return recovered

    def _unresolved_position_events(self) -> dict[str, dict[str, Any]]:
        path = self.evidence_directory / "observations.jsonl"
        if not path.exists():
            return {}
        unresolved: dict[str, dict[str, Any]] = {}
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                symbol = event.get("symbol")
                if not isinstance(symbol, str) or symbol not in self.symbols:
                    continue
                record_type = event.get("record_type")
                if record_type == "TESTNET_POSITION_PROTECTED":
                    unresolved[symbol] = event
                elif record_type == "TESTNET_POSITION_SCALED_AND_REPROTECTED":
                    prior = unresolved.get(symbol, {})
                    started_at = prior.get("position_started_at") or prior.get("protected_at")
                    unresolved[symbol] = event
                    if started_at is not None:
                        unresolved[symbol]["position_started_at"] = started_at
                elif record_type == "TESTNET_EXPERIMENT_RESULT":
                    unresolved.pop(symbol, None)
        return unresolved

    def _synthetic_recovery_event(
        self,
        *,
        symbol: str,
        signed_quantity: Decimal,
        positions: list[dict[str, Any]],
        stop_algo: dict[str, Any] | None,
        target_algo: dict[str, Any] | None,
    ) -> dict[str, Any]:
        matching = [item for item in positions if Decimal(str(item.get("positionAmt", "0"))) != 0]
        if len(matching) != 1 or signed_quantity == 0:
            raise TestnetProbeError("TESTNET_RESTART_POSITION_SNAPSHOT_INVALID")
        position = matching[0]
        entry_price = Decimal(str(position.get("entryPrice", "0")))
        if entry_price <= 0:
            raise TestnetProbeError("TESTNET_RESTART_POSITION_SNAPSHOT_INVALID")
        config = self.client.symbol_config(symbol)
        try:
            leverage = int(config[0]["leverage"])
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise TestnetProbeError("TESTNET_RESTART_LEVERAGE_MISSING") from exc
        created_values = [
            int(item["createTime"])
            for item in (stop_algo, target_algo)
            if item is not None and isinstance(item.get("createTime"), int)
        ]
        started_at = datetime.fromtimestamp(
            (min(created_values) / 1_000) if created_values else time.time(),
            tz=UTC,
        ) - timedelta(seconds=5)
        quantity = abs(signed_quantity)
        event: dict[str, Any] = {
            "record_type": "TESTNET_POSITION_PROTECTED",
            "environment": "testnet",
            "validation_status": "UNVALIDATED_TESTNET_EXPERIMENT",
            "strategy": TESTNET_EXPERIMENT_STRATEGY_VERSION,
            "symbol": symbol,
            "direction": (str(Direction.LONG) if signed_quantity > 0 else str(Direction.SHORT)),
            "quantity": format(quantity, "f"),
            "entry_price": format(entry_price, "f"),
            "position_notional": format(quantity * entry_price, "f"),
            "initial_leverage": leverage,
            "actual_initial_margin": format(quantity * entry_price / Decimal(leverage), "f"),
            "effective_margin_budget": "0",
            "entry_reference_price": format(entry_price, "f"),
            "entry_forecast_model": "RESTART_SYNTHETIC_RECOVERY",
            "directional_forecast_bps": "0",
            "signal_quality_score": "0",
            "signal_confirmation_rounds": 1,
            "setup_type": "RESTART_SYNTHETIC_RECOVERY",
            "stop_trigger": "0" if stop_algo is None else str(stop_algo.get("triggerPrice", "0")),
            "target_trigger": (
                "0" if target_algo is None else str(target_algo.get("triggerPrice", "0"))
            ),
            "position_started_at": started_at.isoformat().replace("+00:00", "Z"),
            "protected_at": started_at.isoformat().replace("+00:00", "Z"),
            "production_endpoint_requests": 0,
        }
        _enrich_recovery_event_with_algos(event, stop_algo, target_algo)
        return event

    def _cancel_stale_system_orders(
        self,
        symbol: str,
        *,
        open_orders: list[dict[str, Any]],
        open_algos: list[dict[str, Any]],
    ) -> int:
        canceled = 0
        for order in open_orders:
            client_id = order.get("clientOrderId")
            if isinstance(client_id, str) and client_id.startswith("aq-"):
                self.client.cancel_order(symbol, client_id)
                canceled += 1
        for algo in open_algos:
            client_id = algo.get("clientAlgoId")
            algo_id = algo.get("algoId")
            if (
                isinstance(client_id, str)
                and client_id.startswith("aqa-t-")
                and isinstance(algo_id, int)
            ):
                self.client.cancel_algo_order(algo_id=algo_id)
                canceled += 1
        return canceled

    def _dispatch_position_invalidations(
        self,
        state: dict[str, Any],
        decisions: list[TestnetBaselineDecision],
        observed_at: datetime,
    ) -> None:
        """Exit a contradicted position without weakening opposite entry rules."""
        plans = _position_invalidation_plans(
            state,
            decisions,
            position_directions=self.position_directions,
            limits=self.limits,
        )
        dispatched = cast(dict[str, str], state.setdefault("position_invalidation_dispatches", {}))
        for plan in plans:
            if plan.symbol not in self.protected_symbols or plan.symbol in dispatched:
                continue
            control = self.position_controls.get(plan.symbol)
            if control is None:
                continue
            episode = f"{plan.direction}:{plan.setup_type}:{state['evaluation_round_count']}"
            dispatched[plan.symbol] = episode
            control.submit(plan)
            event = plan.evidence()
            event.update(
                {
                    "record_type": "TESTNET_POSITION_INVALIDATION_DISPATCHED",
                    "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
                    "reason_code": (
                        "LOCAL_FOLLOWTHROUGH_OR_PROFIT_PROTECTION_FAILED"
                        if plan.setup_type == "LOCAL_FOLLOWTHROUGH_POSITION_INVALIDATION"
                        else "OPPOSING_MARKET_BREADTH_AND_LOCAL_MOMENTUM"
                    ),
                    "production_endpoint_requests": 0,
                }
            )
            self._append_event(event)

    def _submit_experiments(
        self,
        state: dict[str, Any],
        decisions: list[TestnetBaselineDecision],
        observed_at: datetime,
    ) -> None:
        self._dispatch_position_invalidations(state, decisions, observed_at)
        confirmed = _update_pending_signals(
            state,
            decisions,
            active_symbols=set(self.active_trades),
            controllable_symbols=set(self.protected_symbols),
            evaluation_round=int(state["evaluation_round_count"]),
            required_rounds=self.limits.signal_confirmation_rounds,
            minimum_quality_score=self.limits.minimum_signal_quality_score,
            minimum_directional_forecast_bps=(self.limits.minimum_directional_forecast_bps),
            impulse_minimum_directional_forecast_bps=(
                self.limits.impulse_minimum_directional_forecast_bps
            ),
            continuation_minimum_directional_forecast_bps=(
                self.limits.continuation_minimum_directional_forecast_bps
            ),
            structure_substitute_minimum_directional_forecast_bps=(
                self.limits.structure_substitute_minimum_directional_forecast_bps
            ),
            structure_substitute_minimum_trade_imbalance=(
                self.limits.structure_substitute_minimum_trade_imbalance
            ),
            structure_substitute_minimum_secondary_flow=(
                self.limits.structure_substitute_minimum_secondary_flow
            ),
            structure_substitute_minimum_breadth_count=(
                self.limits.impulse_minimum_breadth_count
            ),
            activity_lookback_rounds=self.limits.aggressive_notional_lookback_rounds,
            minimum_activity_samples=self.limits.minimum_aggressive_notional_samples,
            minimum_activity_ratio=self.limits.minimum_aggressive_notional_ratio,
            activity_filter_enabled=self.limits.activity_filter_enabled,
            impulse_required_rounds=self.limits.impulse_confirmation_rounds,
            impulse_minimum_activity_ratio=self.limits.impulse_minimum_activity_ratio,
            impulse_maximum_activity_ratio=self.limits.impulse_maximum_activity_ratio,
            impulse_activity_filter_enabled=self.limits.impulse_activity_filter_enabled,
        )
        self._submit_reversal_plans(state, observed_at)
        candidates = sorted(confirmed, key=_experimental_candidate_rank)
        pending = cast(dict[str, dict[str, object]], state["pending_signals"])
        new_position_plans: list[tuple[TestnetExperimentalPlan, str]] = []
        actionable_count = 0
        dispatched = cast(
            dict[str, str], state.setdefault("position_control_dispatch_episodes", {})
        )
        for decision in candidates:
            if actionable_count >= self.limits.maximum_candidates_per_round:
                break
            plan = decision.experimental_plan
            if plan is None:
                continue
            signal_state = pending.get(plan.symbol, {})
            consecutive = int(str(signal_state.get("consecutive_rounds", 1)))
            plan = replace(
                plan,
                signal_confirmation_rounds=consecutive,
                aggressive_notional_ratio=Decimal(
                    str(signal_state.get("aggressive_notional_ratio", "0"))
                ),
            )
            episode_start = int(state["evaluation_round_count"]) - consecutive + 1
            episode_key = f"{plan.direction}:{plan.setup_type}:{episode_start}"
            if plan.symbol in self.active_trades and plan.symbol not in self.protected_symbols:
                continue
            if plan.symbol not in self.protected_symbols:
                new_position_plans.append((plan, episode_key))
                actionable_count += 1
                continue
            if dispatched.get(plan.symbol) == episode_key:
                continue
            control = self.position_controls.get(plan.symbol)
            if control is None:
                continue
            if self.position_directions.get(plan.symbol) is plan.direction:
                if not self.limits.same_direction_scale_enabled:
                    if not _claim_signal_episode(state, plan.symbol, episode_key):
                        continue
                    event = plan.evidence()
                    event.update(
                        {
                            "record_type": "TESTNET_POSITION_SCALE_BLOCKED",
                            "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
                            "reason_code": "V5_5_DUPLICATE_POSITION_SCALE_DISABLED",
                        }
                    )
                    self._append_event(event)
                    dispatched[plan.symbol] = episode_key
                    continue
                control.submit(plan)
                dispatched[plan.symbol] = episode_key
                continue
            selected_plan = (
                plan
                if self.limits.automatic_reversal_entry_enabled
                else replace(
                    plan,
                    setup_type="MARKET_BREADTH_OPPOSING_SIGNAL_EXIT",
                    exit_only=True,
                )
            )
            if not _claim_signal_episode(state, plan.symbol, episode_key):
                continue
            control.submit(selected_plan)
            dispatched[plan.symbol] = episode_key
            actionable_count += 1
            event = selected_plan.evidence()
            event.update(
                {
                    "record_type": (
                        "TESTNET_POSITION_SIGNAL_DISPATCHED"
                        if self.limits.automatic_reversal_entry_enabled
                        else "TESTNET_POSITION_EXIT_SIGNAL_DISPATCHED"
                    ),
                    "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
                    "signal_episode": episode_key,
                    "position_control_policy": (
                        "LATEST_CONFIRMED_SIGNAL_OWNS_POSITION"
                        if self.limits.automatic_reversal_entry_enabled
                        else "OPPOSING_SIGNAL_EXITS_WITHOUT_AUTO_REVERSAL"
                    ),
                }
            )
            self._append_event(event)

        available = min(
            self._parallel_limit() - len(self.active_trades),
            self.limits.maximum_candidates_per_round,
        )
        if available <= 0:
            return
        last_by_symbol = cast(dict[str, str], state.setdefault("last_trade_by_symbol", {}))
        for plan, episode_key in new_position_plans:
            if available <= 0:
                break
            if not _claim_signal_episode(state, plan.symbol, episode_key):
                continue
            last_value = last_by_symbol.get(plan.symbol)
            continuation = _trend_continuation_cooldown_waived(state, plan)
            allowed, reason = campaign_trade_allowed(
                now=observed_at,
                last_trade_at=(
                    None if continuation or last_value is None else _parse_time(last_value)
                ),
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
            self._launch_experiment(
                state,
                plan,
                observed_at,
                submission_reason=(
                    "TREND_CONTINUATION_AFTER_TARGET" if continuation else "NEW_SIGNAL"
                ),
            )
            available -= 1

    def _submit_reversal_plans(self, state: dict[str, Any], observed_at: datetime) -> None:
        for symbol, plan in list(self.reversal_plans.items()):
            if len(self.active_trades) >= self._parallel_limit():
                return
            if symbol in self.active_trades:
                continue
            del self.reversal_plans[symbol]
            allowed, reason = campaign_trade_allowed(
                now=observed_at,
                last_trade_at=None,
                daily_trade_count=int(state["daily_trade_count"]) + len(self.active_trades),
                daily_net_pnl=Decimal(str(state["daily_net_pnl"])),
                limits=self.limits,
            )
            if not allowed:
                event = plan.evidence()
                event.update(
                    {
                        "record_type": "TESTNET_SIGNAL_REVERSAL_BLOCKED",
                        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
                        "reason_code": str(reason),
                    }
                )
                self._append_event(event)
                continue
            self._launch_experiment(state, plan, observed_at, submission_reason="SIGNAL_REVERSAL")

    def _launch_experiment(
        self,
        state: dict[str, Any],
        plan: TestnetExperimentalPlan,
        observed_at: datetime,
        *,
        submission_reason: str,
    ) -> None:
        cast(dict[str, str], state.setdefault("position_invalidation_dispatches", {})).pop(
            plan.symbol, None
        )
        control = PositionSignalControl()
        minimum_net_reward_risk_ratio = (
            self.limits.impulse_minimum_net_reward_risk_ratio
            if plan.setup_type
            in {
                "MARKET_BREADTH_IMPULSE_FAST",
                "MARKET_BREADTH_TREND",
                "MARKET_BREADTH_PULLBACK_RESUMPTION",
                "MARKET_BREADTH_CONTINUATION",
            }
            else self.limits.minimum_net_reward_risk_ratio
        )
        future = self.trade_executor.submit(
            run_structural_experiment,
            api_key_file=self.api_key_file,
            api_secret_file=self.api_secret_file,
            repository_root=self.repository_root,
            plan=plan,
            margin_budget=self.limits.margin_budget,
            maximum_net_loss=self.limits.maximum_net_loss_per_trade,
            minimum_estimated_net_target=self.limits.minimum_estimated_net_target,
            minimum_net_reward_risk_ratio=minimum_net_reward_risk_ratio,
            risk_sizing_slippage_rate=(self.limits.risk_sizing_slippage_bps / Decimal(10_000)),
            on_entry_attempt=self.position_events.put,
            on_position_protected=self.position_events.put,
            on_position_control=self.position_events.put,
            position_control=control,
            stop_requested=lambda: self.stop_requested,
        )
        self.active_trades[plan.symbol] = future
        self.position_controls[plan.symbol] = control
        pending = cast(dict[str, dict[str, object]], state["pending_signals"])
        pending.pop(plan.symbol, None)
        state["submitted_trade_count"] = int(state.get("submitted_trade_count", 0)) + 1
        event = plan.evidence()
        event.update(
            {
                "record_type": "TESTNET_EXPERIMENT_SUBMITTED",
                "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
                "decision_authority": "TESTNET_DETERMINISTIC_RULE",
                "validation_status": "UNVALIDATED_TESTNET_EXPERIMENT",
                "submission_reason": submission_reason,
                "minimum_net_reward_risk_ratio": format(minimum_net_reward_risk_ratio, "f"),
            }
        )
        self._append_event(event)

    def _reap_completed_trades(self, state: dict[str, Any]) -> None:
        for symbol, future in list(self.active_trades.items()):
            if not future.done():
                continue
            del self.active_trades[symbol]
            cast(dict[str, str], state.setdefault("position_invalidation_dispatches", {})).pop(
                symbol, None
            )
            control = self.position_controls.pop(symbol, None)
            self.position_directions.pop(symbol, None)
            self.protected_symbols.discard(symbol)
            cast(
                dict[str, dict[str, object]],
                state.setdefault("active_position_metrics", {}),
            ).pop(symbol, None)
            try:
                result = future.result()
            except Exception as exc:
                occurred_at = datetime.now(UTC)
                expected_skip = isinstance(exc, TestnetProbeError) and str(exc) in {
                    "EXPERIMENT_PREDICTIVE_LIMIT_NOT_FILLED",
                    "EXPERIMENT_PREDICTIVE_EDGE_INSUFFICIENT",
                    "EXPERIMENT_MARKET_ENTRY_NOT_FILLED",
                    "EXPERIMENT_PREDICTIVE_DIRECTION_CONFLICT",
                    "EXPERIMENT_NET_REWARD_RISK_INSUFFICIENT",
                }
                self._append_event(
                    {
                        "record_type": (
                            "TESTNET_EXPERIMENT_SKIPPED"
                            if expected_skip
                            else "TESTNET_EXPERIMENT_ERROR"
                        ),
                        "occurred_at": occurred_at.isoformat().replace("+00:00", "Z"),
                        "symbol": symbol,
                        "reason_code": type(exc).__name__,
                        "message": str(exc),
                    }
                )
                if expected_skip:
                    continue
                self._notify(
                    severity="ERROR",
                    event_type="测试网交易执行失败",
                    summary=f"交易对: {symbol}\n异常: {type(exc).__name__}\n详情: {exc}",
                    key=f"experiment-error-{symbol}-{occurred_at:%Y%m%d%H%M%S}",
                )
                continue
            if (
                result.get("exit_reason") == "SIGNAL_REVERSAL"
                and control is not None
                and control.replacement_plan is not None
            ):
                self.reversal_plans[symbol] = control.replacement_plan
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
            completed_at = str(result["completed_at"])
            last_by_symbol = cast(dict[str, str], state.setdefault("last_trade_by_symbol", {}))
            last_by_symbol[symbol] = completed_at
            last_reasons = cast(dict[str, str], state.setdefault("last_exit_reason_by_symbol", {}))
            last_reasons[symbol] = str(result["exit_reason"])
            last_directions = cast(
                dict[str, str], state.setdefault("last_completed_direction_by_symbol", {})
            )
            last_directions[symbol] = str(result["direction"])
            state["last_trade_at"] = completed_at
            self._notify(
                severity="INFO" if net >= 0 else "WARNING",
                event_type="测试网交易已平仓",
                summary=(
                    f"{symbol} | {'做多' if result['direction'] == 'LONG' else '做空'} | "
                    f"{result['initial_leverage']}x\n"
                    f"净结果: {'+' if net >= 0 else ''}{_money(result['net_pnl'])} U\n"
                    f"结果: {_exit_reason_cn(str(result['exit_reason']))}\n"
                    "────────────\n"
                    f"价格: {result['entry_price']} → {result['exit_price']}\n"
                    f"已实现: {'+' if Decimal(str(result['realized_pnl'])) >= 0 else ''}"
                    f"{_money(result['realized_pnl'])} U\n"
                    f"手续费: -{_money(result['commission_paid'])} U"
                ),
                key=f"experiment-result-{symbol}-{result['completed_at']}",
            )

    def _drain_position_events(self, state: dict[str, Any]) -> None:
        while True:
            try:
                event = self.position_events.get_nowait()
            except Empty:
                return
            self._append_event(event)
            record_type = event.get("record_type")
            if record_type not in {
                "TESTNET_POSITION_PROTECTED",
                "TESTNET_POSITION_SCALED_AND_REPROTECTED",
            }:
                continue
            self.protected_symbols.add(str(event["symbol"]))
            self.position_directions[str(event["symbol"])] = Direction(str(event["direction"]))
            _initialize_position_metric(state, event)
            if record_type == "TESTNET_POSITION_SCALED_AND_REPROTECTED":
                state["submitted_trade_count"] = int(state.get("submitted_trade_count", 0)) + 1
                state["daily_trade_count"] = int(state["daily_trade_count"]) + 1
                self._notify(
                    severity="INFO",
                    event_type="测试网同向信号已加仓并重设保护",
                    summary=(
                        f"{event['symbol']} | "
                        f"{'做多' if event['direction'] == 'LONG' else '做空'} | "
                        f"{event['initial_leverage']}x\n"
                        f"本次增加: {event['added_quantity']} | "
                        f"当前总量: {event['quantity']}\n"
                        f"加权入场: {event['entry_price']}\n"
                        "────────────\n"
                        f"整仓止盈: {event['target_trigger']} | 预计 +"
                        f"{_money(event['estimated_target_net_pnl'])} U\n"
                        f"整仓止损: {event['stop_trigger']} | 预计 -"
                        f"{_money(event['estimated_stop_net_loss'])} U"
                    ),
                    key=(f"experiment-scaled-{event['symbol']}-{event['protected_at']}"),
                )
                continue
            self._notify(
                severity="INFO",
                event_type="测试网仓位已建立并完成保护",
                summary=(
                    f"{event['symbol']} | "
                    f"{'做多' if event['direction'] == 'LONG' else '做空'} | "
                    f"{event['initial_leverage']}x\n"
                    f"止盈: {event['target_trigger']} | 预计 +"
                    f"{_money(event['estimated_target_net_pnl'])} U\n"
                    f"止损: {event['stop_trigger']} | 预计 -"
                    f"{_money(event['estimated_stop_net_loss'])} U\n"
                    "────────────\n"
                    f"入场: {event['entry_price']}\n"
                    f"预测强度: {_two_decimals(event['directional_forecast_bps'])} bps\n"
                    f"保证金: {_money(event['actual_initial_margin'])} U | "
                    f"数量: {event['quantity']}\n"
                    "保护: 原生止盈/止损已生效"
                ),
                key=f"experiment-protected-{event['symbol']}-{event['protected_at']}",
            )

    def _observe_symbol(self, symbol: str, server_offset_ms: int) -> TestnetBaselineDecision:
        one_minute_klines = self.client.klines(symbol, "1m", limit=120)
        five_minute_klines = self.client.klines(symbol, "5m", limit=120)
        one_hour_klines = self.client.klines(symbol, "1h", limit=120)
        depth = self.client.depth(symbol, limit=20)
        aggregate_trades = self.trade_stream.snapshot(symbol, maximum_age_ms=5_000)
        server_time_ms = int(time.time() * 1_000) + server_offset_ms
        return evaluate_testnet_baseline(
            symbol=symbol,
            server_time_ms=server_time_ms,
            one_minute_klines=one_minute_klines,
            five_minute_klines=five_minute_klines,
            one_hour_klines=one_hour_klines,
            depth=depth,
            aggregate_trades=aggregate_trades,
            signal_parameters=TestnetSignalParameters(
                maximum_spread_bps=self.limits.maximum_entry_spread_bps,
                minimum_trade_imbalance=self.limits.minimum_trade_imbalance,
                minimum_book_imbalance=self.limits.minimum_book_imbalance,
                minimum_microprice_bps=self.limits.minimum_microprice_bps,
                maximum_opposing_book_imbalance=(self.limits.maximum_opposing_book_imbalance),
                maximum_opposing_microprice_bps=(self.limits.maximum_opposing_microprice_bps),
                minimum_target_feasibility_rate_15m=(
                    self.limits.minimum_target_feasibility_rate_15m
                ),
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
                if self.limits.duration_seconds == 0:
                    document["ends_at"] = None
                elif document.get("ends_at") is None:
                    document["ends_at"] = (
                        (datetime.now(UTC) + timedelta(seconds=self.limits.duration_seconds))
                        .isoformat()
                        .replace("+00:00", "Z")
                    )
                document["limits"] = self._limits_document()
                document["strategy"] = TESTNET_EXPERIMENT_STRATEGY_VERSION
                document["validation_status"] = "UNVALIDATED_TESTNET_EXPERIMENT"
                document["decision_authority"] = "TESTNET_DETERMINISTIC_RULE"
                document["codex_dependency"] = False
                document.setdefault("last_trade_by_symbol", {})
                document.setdefault("last_exit_reason_by_symbol", {})
                document.setdefault("last_completed_direction_by_symbol", {})
                document.setdefault("active_symbols", [])
                document.setdefault("pending_entry_symbols", [])
                document.setdefault("pending_signals", {})
                document.setdefault("position_invalidation_dispatches", {})
                document.setdefault("active_position_metrics", {})
                document.setdefault("aggressive_notional_history", {})
                document.setdefault("mid_price_history", {})
                document.setdefault("pullback_setups", {})
                document.setdefault("market_context_confirmation", {})
                document.setdefault("continuation_episodes_by_symbol", {})
                document.setdefault("consumed_signal_episodes_by_symbol", {})
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
            "ends_at": (
                None
                if self.limits.duration_seconds == 0
                else (now + timedelta(seconds=self.limits.duration_seconds))
                .isoformat()
                .replace("+00:00", "Z")
            ),
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
            "last_exit_reason_by_symbol": {},
            "last_completed_direction_by_symbol": {},
            "active_symbols": [],
            "pending_entry_symbols": [],
            "pending_signals": {},
            "position_invalidation_dispatches": {},
            "active_position_metrics": {},
            "aggressive_notional_history": {},
            "mid_price_history": {},
            "pullback_setups": {},
            "market_context_confirmation": {},
            "continuation_episodes_by_symbol": {},
            "consumed_signal_episodes_by_symbol": {},
            "production_endpoint_requests": 0,
            "limits": self._limits_document(),
            "prior_campaign": prior_campaign,
        }
        _atomic_write_json(self.state_file, state)
        return state

    def _limits_document(self) -> dict[str, object]:
        return {
            "duration_seconds": self.limits.duration_seconds,
            "continuous_operation": self.limits.duration_seconds == 0,
            "evaluation_interval_seconds": self.limits.evaluation_interval_seconds,
            "trade_cooldown_seconds": self.limits.trade_cooldown_seconds,
            "maximum_trades_per_day": self.limits.maximum_trades_per_day,
            "daily_net_loss_limit": format(self.limits.daily_net_loss_limit, "f"),
            "margin_budget": format(self.limits.margin_budget, "f"),
            "maximum_net_loss_per_trade": format(self.limits.maximum_net_loss_per_trade, "f"),
            "execution_mode": "TESTNET_EXPERIMENT",
            "decision_authority": "TESTNET_DETERMINISTIC_RULE",
            "codex_dependency": False,
            "execution_forecast_threshold_source": "CONFIRMED_PLAN",
            "maximum_parallel_observations": len(self.symbols),
            "maximum_parallel_positions": self._parallel_limit(),
            "maximum_candidates_per_round": self.limits.maximum_candidates_per_round,
            "market_episode_entry_limit_enabled": False,
            "duplicate_signal_suppression_enabled": True,
            "same_direction_scale_enabled": self.limits.same_direction_scale_enabled,
            "automatic_reversal_entry_enabled": (self.limits.automatic_reversal_entry_enabled),
            "position_slots_are_target": False,
            "signal_confirmation_rounds": self.limits.signal_confirmation_rounds,
            "impulse_confirmation_rounds": self.limits.impulse_confirmation_rounds,
            "minimum_signal_quality_score": format(self.limits.minimum_signal_quality_score, "f"),
            "minimum_directional_forecast_bps": format(
                self.limits.minimum_directional_forecast_bps, "f"
            ),
            "impulse_minimum_directional_forecast_bps": format(
                self.limits.impulse_minimum_directional_forecast_bps, "f"
            ),
            "continuation_minimum_directional_forecast_bps": format(
                self.limits.continuation_minimum_directional_forecast_bps, "f"
            ),
            "structure_substitute_minimum_directional_forecast_bps": format(
                self.limits.structure_substitute_minimum_directional_forecast_bps, "f"
            ),
            "structure_substitute_minimum_trade_imbalance": format(
                self.limits.structure_substitute_minimum_trade_imbalance, "f"
            ),
            "structure_substitute_minimum_secondary_flow": format(
                self.limits.structure_substitute_minimum_secondary_flow, "f"
            ),
            "minimum_target_feasibility_rate_15m": format(
                self.limits.minimum_target_feasibility_rate_15m, "f"
            ),
            "impulse_minimum_target_feasibility_rate_15m": format(
                self.limits.impulse_minimum_target_feasibility_rate_15m, "f"
            ),
            "minimum_estimated_net_target": format(self.limits.minimum_estimated_net_target, "f"),
            "minimum_net_reward_risk_ratio": format(self.limits.minimum_net_reward_risk_ratio, "f"),
            "impulse_minimum_net_reward_risk_ratio": format(
                self.limits.impulse_minimum_net_reward_risk_ratio, "f"
            ),
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
            "activity_filter_enabled": self.limits.activity_filter_enabled,
            "impulse_activity_filter_enabled": self.limits.impulse_activity_filter_enabled,
            "impulse_minimum_activity_ratio": format(
                self.limits.impulse_minimum_activity_ratio, "f"
            ),
            "impulse_maximum_activity_ratio": format(
                self.limits.impulse_maximum_activity_ratio, "f"
            ),
            "impulse_lookback_rounds": self.limits.impulse_lookback_rounds,
            "impulse_minimum_momentum_bps": format(self.limits.impulse_minimum_momentum_bps, "f"),
            "impulse_maximum_momentum_bps": format(self.limits.impulse_maximum_momentum_bps, "f"),
            "impulse_minimum_breadth_count": self.limits.impulse_minimum_breadth_count,
            "sustained_lookback_rounds": self.limits.sustained_lookback_rounds,
            "sustained_minimum_momentum_bps": format(
                self.limits.sustained_minimum_momentum_bps, "f"
            ),
            "pullback_minimum_bps": format(self.limits.pullback_minimum_bps, "f"),
            "pullback_resumption_bps": format(self.limits.pullback_resumption_bps, "f"),
            "pullback_maximum_bps": format(self.limits.pullback_maximum_bps, "f"),
            "pullback_setup_maximum_rounds": self.limits.pullback_setup_maximum_rounds,
            "signal_evidence_window_rounds": self.limits.signal_evidence_window_rounds,
            "continuation_minimum_breadth_count": (self.limits.continuation_minimum_breadth_count),
            "continuation_confirmation_rounds": self.limits.continuation_confirmation_rounds,
            "continuation_minimum_momentum_bps": format(
                self.limits.continuation_minimum_momentum_bps, "f"
            ),
            "continuation_maximum_momentum_bps": format(
                self.limits.continuation_maximum_momentum_bps, "f"
            ),
            "position_failed_followthrough_peak_bps": format(
                self.limits.position_failed_followthrough_peak_bps, "f"
            ),
            "position_adverse_invalidation_bps": format(
                self.limits.position_adverse_invalidation_bps, "f"
            ),
            "position_profit_protection_peak_bps": format(
                self.limits.position_profit_protection_peak_bps, "f"
            ),
            "position_profit_giveback_bps": format(
                self.limits.position_profit_giveback_bps, "f"
            ),
            "position_opposition_confirmation_rounds": (
                self.limits.position_opposition_confirmation_rounds
            ),
            "elapsed_time_exit_enabled": False,
        }


def _recovery_algo_pair(
    open_algos: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return the unique system stop/target pair, or an incomplete pair."""
    stops: list[dict[str, Any]] = []
    targets: list[dict[str, Any]] = []
    for algo in open_algos:
        client_id = algo.get("clientAlgoId")
        if (
            not isinstance(client_id, str)
            or not client_id.startswith("aqa-t-")
            or algo.get("algoStatus") not in {"NEW", "TRIGGERING"}
            or algo.get("closePosition") is not True
        ):
            continue
        if "-sl-" in client_id:
            stops.append(algo)
        elif "-tp-" in client_id:
            targets.append(algo)
    return (
        stops[0] if len(stops) == 1 else None,
        targets[0] if len(targets) == 1 else None,
    )


def _enrich_recovery_event_with_algos(
    event: dict[str, Any],
    stop_algo: dict[str, Any] | None,
    target_algo: dict[str, Any] | None,
) -> None:
    for role, algo in (("stop", stop_algo), ("target", target_algo)):
        if algo is None:
            continue
        algo_id = algo.get("algoId")
        client_id = algo.get("clientAlgoId")
        if isinstance(algo_id, int):
            event[f"{role}_algo_id"] = algo_id
        if isinstance(client_id, str):
            event[f"{role}_client_algo_id"] = client_id
        trigger = algo.get("triggerPrice")
        if trigger is not None:
            event[f"{role}_trigger"] = str(trigger)


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
        "TARGET_FEASIBILITY_INSUFFICIENT": "近期 15 分钟目标触达率不足",
        "ACTIVITY_RATIO_EXCESSIVE": "主动成交突增过大; 可能处于冲量尾端",
        "FAST_BREADTH_AUTHORITY_INSUFFICIENT": "三币快速背景已出现; 强预测与资金流确认不足",
    }.get(reason, reason)


def _exit_reason_cn(reason: str) -> str:
    return {
        "TAKE_PROFIT": "达到止盈目标",
        "STOP_LOSS": "跌破/突破结构止损",
        "SIGNAL_REVERSAL": "最新反向信号接管仓位",
        "SIGNAL_INVALIDATION": "市场宽度与本币动量反向; 原结构失效",
        "OPERATOR_SERVICE_STOP": "服务停止时人工平仓",
        "EXECUTION_FAIL_CLOSED": "成交后风控复核未通过; 已安全平仓",
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
    controllable_symbols: set[str] | None = None,
    evaluation_round: int,
    required_rounds: int,
    minimum_quality_score: Decimal,
    minimum_directional_forecast_bps: Decimal = Decimal(0),
    impulse_minimum_directional_forecast_bps: Decimal | None = None,
    continuation_minimum_directional_forecast_bps: Decimal | None = None,
    structure_substitute_minimum_directional_forecast_bps: Decimal = Decimal("3.00"),
    structure_substitute_minimum_trade_imbalance: Decimal = Decimal("0.75"),
    structure_substitute_minimum_secondary_flow: Decimal = Decimal("0.10"),
    structure_substitute_minimum_breadth_count: int = 3,
    activity_lookback_rounds: int,
    minimum_activity_samples: int,
    minimum_activity_ratio: Decimal,
    activity_filter_enabled: bool = True,
    impulse_required_rounds: int | None = None,
    impulse_minimum_activity_ratio: Decimal | None = None,
    impulse_maximum_activity_ratio: Decimal | None = None,
    impulse_activity_filter_enabled: bool = True,
) -> list[TestnetBaselineDecision]:
    """Require consecutive same-direction evidence without treating slots as a target."""
    controllable = set() if controllable_symbols is None else controllable_symbols
    blocked_symbols = active_symbols - controllable
    pending = cast(dict[str, dict[str, object]], state.setdefault("pending_signals", {}))
    activity_history = cast(
        dict[str, list[str]], state.setdefault("aggressive_notional_history", {})
    )
    diagnostics: dict[str, object] = {
        "evaluation_round": evaluation_round,
        "plan_count": 0,
        "confirmed_count": 0,
        "symbols": {},
    }
    state["last_confirmation_diagnostics"] = diagnostics
    counters = cast(dict[str, int], state.setdefault("confirmation_gate_counts", {}))

    def count(reason: str) -> None:
        counters[reason] = counters.get(reason, 0) + 1

    current_symbols = {decision.symbol for decision in decisions}
    for symbol in list(pending):
        if symbol not in current_symbols or symbol in blocked_symbols:
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
        is_market_breadth = plan is not None and plan.setup_type in {
            "MARKET_BREADTH_IMPULSE_FAST",
            "MARKET_BREADTH_TREND",
            "MARKET_BREADTH_PULLBACK_RESUMPTION",
            "MARKET_BREADTH_CONTINUATION",
        }
        required = (
            impulse_required_rounds
            if is_market_breadth and impulse_required_rounds is not None
            else required_rounds
        )
        required_activity = (
            impulse_minimum_activity_ratio
            if is_market_breadth and impulse_minimum_activity_ratio is not None
            else minimum_activity_ratio
        )
        maximum_activity = impulse_maximum_activity_ratio if is_market_breadth else None
        selected_activity_filter_enabled = activity_filter_enabled and (
            not is_market_breadth or impulse_activity_filter_enabled
        )
        required_forecast = (
            impulse_minimum_directional_forecast_bps
            if is_market_breadth and impulse_minimum_directional_forecast_bps is not None
            else minimum_directional_forecast_bps
        )
        if (
            plan is not None
            and plan.setup_type == "MARKET_BREADTH_CONTINUATION"
            and continuation_minimum_directional_forecast_bps is not None
        ):
            required_forecast = continuation_minimum_directional_forecast_bps
        if plan is None:
            pending.pop(decision.symbol, None)
            continue
        directional_forecast_bps = _plan_directional_forecast_bps(plan, decision.mid_price)
        structure_substitute = (
            is_market_breadth
            and plan.market_breadth_count >= structure_substitute_minimum_breadth_count
            and directional_forecast_bps
            >= structure_substitute_minimum_directional_forecast_bps
            and plan.directional_trade_imbalance
            >= structure_substitute_minimum_trade_imbalance
            and (
                plan.directional_book_imbalance
                >= structure_substitute_minimum_secondary_flow
                or plan.directional_microprice_bps
                >= structure_substitute_minimum_secondary_flow
            )
        )
        limited_fast_breadth = (
            is_market_breadth
            and plan.market_breadth_count < 4
        )
        diagnostics["plan_count"] = int(str(diagnostics["plan_count"])) + 1
        reason = "WAITING_CONFIRMATION"
        if plan.symbol in blocked_symbols:
            reason = "ALREADY_IN_FLIGHT"
        elif plan.signal_quality_score < minimum_quality_score:
            reason = "QUALITY_BELOW_THRESHOLD"
        elif limited_fast_breadth and not structure_substitute:
            reason = "FAST_BREADTH_AUTHORITY_INSUFFICIENT"
        elif is_market_breadth and plan.pa_alignment_count < 1 and not structure_substitute:
            reason = "PA_ALIGNMENT_INSUFFICIENT"
        elif required_forecast > 0 and directional_forecast_bps <= 0:
            reason = "FORECAST_DIRECTION_CONFLICT"
        elif required_forecast > 0 and directional_forecast_bps < required_forecast:
            reason = "FORECAST_EDGE_INSUFFICIENT"
        elif selected_activity_filter_enabled and len(activity_values) < minimum_activity_samples:
            reason = "ACTIVITY_HISTORY_INSUFFICIENT"
        elif selected_activity_filter_enabled and activity_ratio < required_activity:
            reason = "ACTIVITY_RATIO_INSUFFICIENT"
        elif (
            selected_activity_filter_enabled
            and maximum_activity is not None
            and activity_ratio > maximum_activity
        ):
            reason = "ACTIVITY_RATIO_EXCESSIVE"
        symbol_diagnostics = cast(dict[str, object], diagnostics["symbols"])
        details: dict[str, object] = {
            "gate_result": reason,
            "setup_type": plan.setup_type,
            "direction": str(plan.direction),
            "quality_score": format(plan.signal_quality_score, "f"),
            "required_quality_score": format(minimum_quality_score, "f"),
            "directional_forecast_bps": format(directional_forecast_bps, "f"),
            "required_directional_forecast_bps": format(required_forecast, "f"),
            "forecast_filter_enabled": required_forecast > 0,
            "structure_authority": (
                "FORECAST_AND_FLOW_SUBSTITUTE"
                if limited_fast_breadth or plan.pa_alignment_count < 1
                else "PA_ALIGNMENT"
            ),
            "structure_substitute_qualified": structure_substitute,
            "limited_fast_breadth": limited_fast_breadth,
            "activity_samples": len(activity_values),
            "required_activity_samples": minimum_activity_samples,
            "activity_ratio": format(activity_ratio, "f"),
            "required_activity_ratio": format(required_activity, "f"),
            "maximum_activity_ratio": (
                None if maximum_activity is None else format(maximum_activity, "f")
            ),
            "activity_filter_enabled": selected_activity_filter_enabled,
            "required_confirmation_rounds": required,
        }
        symbol_diagnostics[plan.symbol] = details
        if reason != "WAITING_CONFIRMATION":
            count(reason)
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
            confirmed_plan = (
                replace(plan, minimum_directional_forecast_bps=required_forecast)
                if required_forecast > 0
                else plan
            )
            confirmed.append(
                decision
                if confirmed_plan is plan
                else replace(decision, experimental_plan=confirmed_plan)
            )
            details["gate_result"] = "CONFIRMED"
            diagnostics["confirmed_count"] = int(str(diagnostics["confirmed_count"])) + 1
            count("CONFIRMED")
        else:
            details["consecutive_rounds"] = consecutive
            count("WAITING_CONFIRMATION")
    return confirmed


def _position_invalidation_plans(
    state: dict[str, Any],
    decisions: list[TestnetBaselineDecision],
    *,
    position_directions: dict[str, Direction],
    limits: CampaignLimits,
) -> list[TestnetExperimentalPlan]:
    """Turn broad local contradiction into exit-only plans, never automatic entries."""
    diagnostics = state.get("last_signal_diagnostics")
    if not isinstance(diagnostics, dict):
        return []
    symbol_diagnostics = diagnostics.get("symbols")
    if not isinstance(symbol_diagnostics, dict):
        return []
    by_symbol = {decision.symbol: decision for decision in decisions}
    evaluation_round = int(str(state.get("evaluation_round_count", 0))) + 1
    position_metrics = cast(
        dict[str, dict[str, object]],
        state.setdefault("active_position_metrics", {}),
    )
    invalidations: list[TestnetExperimentalPlan] = []
    for symbol, current_direction in position_directions.items():
        if current_direction not in {Direction.LONG, Direction.SHORT}:
            continue
        decision = by_symbol.get(symbol)
        detail = symbol_diagnostics.get(symbol)
        if decision is None or not isinstance(detail, dict):
            continue
        opposite = Direction.SHORT if current_direction is Direction.LONG else Direction.LONG
        if opposite is Direction.LONG:
            sustained_breadth = int(str(diagnostics.get("sustained_long_breadth", 0)))
            fast_breadth = int(str(diagnostics.get("fast_long_breadth", 0)))
            sign = Decimal(1)
        else:
            sustained_breadth = int(str(diagnostics.get("sustained_short_breadth", 0)))
            fast_breadth = int(str(diagnostics.get("fast_short_breadth", 0)))
            sign = Decimal(-1)
        sustained_momentum = sign * Decimal(str(detail.get("sustained_momentum_bps", "0")))
        fast_momentum = sign * Decimal(str(detail.get("fast_momentum_bps", "0")))
        sustained_invalidated = (
            sustained_breadth >= 4 and sustained_momentum >= limits.sustained_minimum_momentum_bps
        )
        context_confirmation = state.get("market_context_confirmation")
        fast_invalidated = (
            fast_breadth == len(TESTNET_EXPERIMENT_SYMBOLS)
            and fast_momentum >= limits.continuation_minimum_momentum_bps
            and isinstance(context_confirmation, dict)
            and context_confirmation.get("direction") == str(opposite)
            and int(str(context_confirmation.get("consecutive_rounds", 0)))
            >= limits.continuation_confirmation_rounds
        )
        sign = Decimal(1) if opposite is Direction.LONG else Decimal(-1)
        directional_trade = sign * decision.order_flow.trade_imbalance
        aligned_structure = "HH_HL" if opposite is Direction.LONG else "LH_LL"
        local_structure_opposes = opposite in {
            decision.pa_1m.direction,
            decision.pa_5m.direction,
        } or any(
            str(frame.structure) == aligned_structure for frame in (decision.pa_1m, decision.pa_5m)
        )
        flow_opposes = directional_trade >= limits.minimum_trade_imbalance
        metric = position_metrics.get(symbol)
        local_signal_invalidated = False
        local_invalidation_reason: str | None = None
        if metric is not None:
            entry_price = Decimal(str(metric.get("entry_price", "0")))
            if entry_price > 0:
                position_sign = Decimal(1) if current_direction is Direction.LONG else Decimal(-1)
                current_favorable = (
                    position_sign
                    * (decision.mid_price - entry_price)
                    / entry_price
                    * Decimal(10_000)
                )
                peak_favorable = max(
                    Decimal(str(metric.get("peak_favorable_bps", "0"))),
                    current_favorable,
                )
                opposite_book = sign * decision.order_flow.book_imbalance
                opposite_microprice = sign * decision.order_flow.microprice_mid_bps
                opposing_secondary_flow = (
                    opposite_book >= limits.minimum_book_imbalance
                    or opposite_microprice >= limits.minimum_microprice_bps
                )
                clean_opposing_flow = flow_opposes and opposing_secondary_flow
                previous_round = int(str(metric.get("last_evaluation_round", -1)))
                previous_count = int(str(metric.get("opposing_flow_rounds", 0)))
                opposing_rounds = (
                    previous_count + 1
                    if clean_opposing_flow and previous_round == evaluation_round - 1
                    else (1 if clean_opposing_flow else 0)
                )
                metric.update(
                    {
                        "peak_favorable_bps": format(peak_favorable, "f"),
                        "current_favorable_bps": format(current_favorable, "f"),
                        "opposing_flow_rounds": opposing_rounds,
                        "last_evaluation_round": evaluation_round,
                    }
                )
                opposition_confirmed = (
                    opposing_rounds >= limits.position_opposition_confirmation_rounds
                )
                failed_followthrough = (
                    peak_favorable >= limits.position_failed_followthrough_peak_bps
                    and current_favorable <= 0
                    and opposition_confirmed
                )
                adverse_move = (
                    current_favorable <= -limits.position_adverse_invalidation_bps
                    and opposition_confirmed
                )
                profit_giveback = (
                    peak_favorable >= limits.position_profit_protection_peak_bps
                    and peak_favorable - current_favorable
                    >= limits.position_profit_giveback_bps
                    and opposition_confirmed
                )
                if profit_giveback:
                    local_invalidation_reason = "PROFIT_GIVEBACK_WITH_OPPOSING_FLOW"
                elif failed_followthrough:
                    local_invalidation_reason = "FAILED_FOLLOWTHROUGH_WITH_OPPOSING_FLOW"
                elif adverse_move:
                    local_invalidation_reason = "ADVERSE_MOVE_WITH_OPPOSING_FLOW"
                local_signal_invalidated = local_invalidation_reason is not None
                metric["local_invalidation_reason"] = local_invalidation_reason
        broad_invalidated = sustained_invalidated or fast_invalidated
        if not local_signal_invalidated and (
            not broad_invalidated or not (local_structure_opposes or flow_opposes)
        ):
            continue
        if local_signal_invalidated:
            setup_type = "LOCAL_FOLLOWTHROUGH_POSITION_INVALIDATION"
            breadth_count = max(fast_breadth, sustained_breadth)
            momentum = max(fast_momentum, sustained_momentum, Decimal(0))
        else:
            setup_type = (
                "MARKET_BREADTH_FAST_POSITION_INVALIDATION"
                if fast_invalidated
                else "MARKET_BREADTH_TREND_POSITION_INVALIDATION"
            )
            breadth_count = fast_breadth if fast_invalidated else sustained_breadth
            momentum = fast_momentum if fast_invalidated else sustained_momentum
        invalidations.append(
            TestnetExperimentalPlan(
                symbol=symbol,
                direction=opposite,
                entry_reference=decision.mid_price,
                stop_anchor=decision.mid_price,
                target_reference=decision.mid_price,
                predictive_average_20m=decision.mid_price,
                signal_quality_score=Decimal(breadth_count) + momentum / Decimal(10),
                setup_type=setup_type,
                market_momentum_bps=momentum,
                market_breadth_count=breadth_count,
                minimum_directional_forecast_bps=Decimal(0),
                exit_only=True,
            )
        )
    return invalidations


def _apply_market_impulse_plans(
    state: dict[str, Any],
    decisions: list[TestnetBaselineDecision],
    *,
    limits: CampaignLimits,
    evaluation_round: int,
) -> list[TestnetBaselineDecision]:
    """Run the V5.5 signal-owned pullback and controlled-continuation state machine.

    Raw breadth and ordinary PA plans are intentionally diagnostic-only.  An
    executable plan is generated either after a pullback/resumption sequence or
    after consecutive broad-market confirmations. Three-coin fast breadth is
    admitted only as a context; the confirmation gate separately requires
    stronger predictive and order-flow authority. Pullback evidence may
    arrive across adjacent rounds so transient order-book noise cannot erase an
    otherwise coherent setup.
    """
    histories = cast(
        dict[str, list[dict[str, str | int]]], state.setdefault("mid_price_history", {})
    )
    fast_momentum: dict[str, Decimal] = {}
    sustained_momentum: dict[str, Decimal] = {}
    for decision in decisions:
        history = histories.setdefault(decision.symbol, [])
        history.append(
            {
                "evaluation_round": evaluation_round,
                "mid_price": format(decision.mid_price, "f"),
            }
        )
        del history[: -limits.sustained_lookback_rounds]
        if len(history) >= limits.impulse_lookback_rounds:
            fast_start = Decimal(str(history[-limits.impulse_lookback_rounds]["mid_price"]))
            fast_momentum[decision.symbol] = (
                decision.mid_price / fast_start - Decimal(1)
            ) * Decimal(10_000)
        if len(history) >= limits.sustained_lookback_rounds:
            sustained_start = Decimal(str(history[0]["mid_price"]))
            sustained_momentum[decision.symbol] = (
                decision.mid_price / sustained_start - Decimal(1)
            ) * Decimal(10_000)

    def breadth(momentum: dict[str, Decimal], threshold: Decimal) -> tuple[int, int]:
        return (
            sum(value >= threshold for value in momentum.values()),
            sum(value <= -threshold for value in momentum.values()),
        )

    fast_long, fast_short = breadth(fast_momentum, limits.impulse_minimum_momentum_bps)
    sustained_long, sustained_short = breadth(
        sustained_momentum, limits.sustained_minimum_momentum_bps
    )
    context: tuple[str, Direction, int, dict[str, Decimal], Decimal] | None = None
    history_fully_warmed = len(sustained_momentum) == len(decisions)
    fast_max = max(fast_long, fast_short)
    sustained_max = max(sustained_long, sustained_short)
    sustained_direction = Direction.LONG if sustained_long > sustained_short else Direction.SHORT
    sustained_sign = Decimal(1) if sustained_direction is Direction.LONG else Decimal(-1)
    sustained_strengths = sorted(
        sustained_sign * value
        for value in sustained_momentum.values()
        if sustained_sign * value >= limits.sustained_minimum_momentum_bps
    )
    fast_strengths_in_sustained_direction = sorted(
        sustained_sign * value for value in fast_momentum.values() if sustained_sign * value > 0
    )
    sustained_is_established = sustained_max >= 4 and (
        sustained_max > fast_max
        or (
            sustained_strengths
            and _median_decimal(sustained_strengths)
            >= _median_decimal(fast_strengths_in_sustained_direction)
            + limits.sustained_minimum_momentum_bps
        )
    )
    if history_fully_warmed and sustained_is_established and sustained_long != sustained_short:
        context = (
            "MARKET_BREADTH_TREND",
            sustained_direction,
            sustained_max,
            sustained_momentum,
            limits.sustained_minimum_momentum_bps,
        )
    elif (
        history_fully_warmed
        and fast_max >= limits.impulse_minimum_breadth_count
        and fast_long != fast_short
    ):
        context = (
            "MARKET_BREADTH_IMPULSE_FAST",
            Direction.LONG if fast_long > fast_short else Direction.SHORT,
            fast_max,
            fast_momentum,
            limits.impulse_minimum_momentum_bps,
        )
    elif (
        history_fully_warmed
        and max(sustained_long, sustained_short) >= 4
        and sustained_long != sustained_short
    ):
        context = (
            "MARKET_BREADTH_TREND",
            Direction.LONG if sustained_long > sustained_short else Direction.SHORT,
            sustained_max,
            sustained_momentum,
            limits.sustained_minimum_momentum_bps,
        )

    context_confirmation = cast(
        dict[str, object], state.setdefault("market_context_confirmation", {})
    )
    required_context_breadth_count = (
        limits.impulse_minimum_breadth_count
        if context is not None and context[0] == "MARKET_BREADTH_IMPULSE_FAST"
        else limits.continuation_minimum_breadth_count
    )
    if context is None or context[2] < required_context_breadth_count:
        context_confirmation.clear()
    else:
        previous_round = int(str(context_confirmation.get("evaluation_round", -1)))
        same_direction = context_confirmation.get("direction") == str(context[1])
        consecutive_rounds = (
            int(str(context_confirmation.get("consecutive_rounds", 0))) + 1
            if same_direction and previous_round == evaluation_round - 1
            else 1
        )
        episode_started_round = (
            int(str(context_confirmation.get("episode_started_round", evaluation_round)))
            if same_direction and previous_round == evaluation_round - 1
            else evaluation_round
        )
        context_confirmation.update(
            {
                "direction": str(context[1]),
                "consecutive_rounds": consecutive_rounds,
                "evaluation_round": evaluation_round,
                "episode_started_round": episode_started_round,
                "breadth_count": context[2],
                "context_type": context[0],
            }
        )
    continuation_confirmation_count = int(str(context_confirmation.get("consecutive_rounds", 0)))
    continuation_episode_start = int(
        str(context_confirmation.get("episode_started_round", evaluation_round))
    )
    continuation_block = max(
        0, continuation_confirmation_count - limits.continuation_confirmation_rounds
    ) // (limits.continuation_confirmation_rounds)
    continuation_episode = f"{continuation_episode_start}:{continuation_block}"

    diagnostics: dict[str, object] = {
        "evaluation_round": evaluation_round,
        "fast_long_breadth": fast_long,
        "fast_short_breadth": fast_short,
        "sustained_long_breadth": sustained_long,
        "sustained_short_breadth": sustained_short,
        "selected_setup": None if context is None else context[0],
        "required_context_breadth_count": required_context_breadth_count,
        "sustained_is_established": sustained_is_established,
        "history_fully_warmed": history_fully_warmed,
        "continuation_confirmation_count": continuation_confirmation_count,
        "required_continuation_confirmations": limits.continuation_confirmation_rounds,
        "continuation_episode": continuation_episode,
        "market_episode_started_round": continuation_episode_start,
        "market_episode_direction": None if context is None else str(context[1]),
        "symbols": {},
    }
    state["last_signal_diagnostics"] = diagnostics
    counters = cast(dict[str, int], state.setdefault("signal_gate_counts", {}))

    def count(reason: str) -> None:
        counters[reason] = counters.get(reason, 0) + 1

    setups = cast(dict[str, dict[str, object]], state.setdefault("pullback_setups", {}))
    continuation_episodes = cast(
        dict[str, str], state.setdefault("continuation_episodes_by_symbol", {})
    )
    current_symbols = {decision.symbol for decision in decisions}
    for symbol in list(setups):
        stored_setup = setups[symbol]
        started_round = int(str(stored_setup.get("started_round", evaluation_round)))
        if (
            symbol not in current_symbols
            or evaluation_round - started_round > limits.pullback_setup_maximum_rounds
        ):
            setups.pop(symbol, None)
    for symbol in list(continuation_episodes):
        if symbol not in current_symbols:
            continuation_episodes.pop(symbol, None)

    setup_type = None if context is None else context[0]
    direction = Direction.NEUTRAL if context is None else context[1]
    breadth_count = 0 if context is None else context[2]
    momentum_by_symbol: dict[str, Decimal] = {} if context is None else context[3]
    threshold = Decimal(0) if context is None else context[4]
    selected_target_feasibility = limits.impulse_minimum_target_feasibility_rate_15m
    parameters = TestnetSignalParameters(
        maximum_spread_bps=limits.maximum_entry_spread_bps,
        minimum_trade_imbalance=limits.minimum_trade_imbalance,
        minimum_book_imbalance=limits.minimum_book_imbalance,
        minimum_microprice_bps=limits.minimum_microprice_bps,
        maximum_opposing_book_imbalance=limits.maximum_opposing_book_imbalance,
        maximum_opposing_microprice_bps=limits.maximum_opposing_microprice_bps,
        minimum_target_feasibility_rate_15m=selected_target_feasibility,
    )
    promoted: list[TestnetBaselineDecision] = []
    for decision in decisions:
        momentum = momentum_by_symbol.get(decision.symbol, Decimal(0))
        directional_momentum = (
            momentum
            if direction is Direction.LONG
            else (-momentum if direction is Direction.SHORT else Decimal(0))
        )
        aligned = context is not None and directional_momentum >= threshold
        # V5 removes execution authority from both the old direct PA plan and raw
        # market impulse. They may still appear in the observation evidence.
        plan: TestnetExperimentalPlan | None = None
        existing_plan_disabled = decision.experimental_plan is not None
        setup: dict[str, object] | None = setups.get(decision.symbol)
        setup_phase = "IDLE"
        pullback_bps = Decimal(0)
        resumption_bps = Decimal(0)
        trigger = False
        reason = (
            "INSUFFICIENT_HISTORY"
            if context is None and not history_fully_warmed
            else "MARKET_BREADTH_INSUFFICIENT"
        )
        if context is not None and setup is not None and setup.get("direction") != str(direction):
            setups.pop(decision.symbol, None)
            setup = None
            reason = "SETUP_RESET_BY_CONTEXT_REVERSAL"
        if context is not None and setup is None and aligned:
            setup = {
                "direction": str(direction),
                "started_round": evaluation_round,
                "extreme_price": format(decision.mid_price, "f"),
                "previous_mid_price": format(decision.mid_price, "f"),
                "phase": "ARMED",
                "maximum_pullback_bps": "0",
                "market_breadth_count": breadth_count,
                "market_momentum_bps": format(momentum, "f"),
                "market_context_type": setup_type,
            }
            setups[decision.symbol] = setup
            setup_phase = "ARMED"
            reason = "SETUP_ARMED_WAITING_FOR_PULLBACK"
        elif context is not None and setup is None and not aligned:
            reason = "LOCAL_MOMENTUM_INSUFFICIENT"
        elif setup is not None:
            setup_direction = Direction(str(setup["direction"]))
            previous_mid = Decimal(str(setup["previous_mid_price"]))
            extreme = Decimal(str(setup["extreme_price"]))
            if setup_direction is Direction.LONG:
                extreme = max(extreme, decision.mid_price)
                pullback_bps = (extreme / decision.mid_price - Decimal(1)) * Decimal(10_000)
                resumption_bps = (decision.mid_price / previous_mid - Decimal(1)) * Decimal(10_000)
            else:
                extreme = min(extreme, decision.mid_price)
                pullback_bps = (decision.mid_price / extreme - Decimal(1)) * Decimal(10_000)
                resumption_bps = (previous_mid / decision.mid_price - Decimal(1)) * Decimal(10_000)
            maximum_pullback = max(
                Decimal(str(setup.get("maximum_pullback_bps", "0"))), pullback_bps
            )
            setup["extreme_price"] = format(extreme, "f")
            setup["previous_mid_price"] = format(decision.mid_price, "f")
            setup["maximum_pullback_bps"] = format(maximum_pullback, "f")
            if pullback_bps > limits.pullback_maximum_bps:
                setups.pop(decision.symbol, None)
                setup = None
                setup_phase = "STRUCTURE_BROKEN"
                reason = "SETUP_PULLBACK_STRUCTURE_BROKEN"
            else:
                if pullback_bps >= limits.pullback_minimum_bps:
                    setup["phase"] = "PULLBACK_OBSERVED"
                    setup.setdefault("pullback_observed_round", evaluation_round)
                setup_phase = str(setup.get("phase", "ARMED"))
                same_market_context = context is not None and direction is setup_direction
                aligned_structure = "HH_HL" if setup_direction is Direction.LONG else "LH_LL"
                price_action_resumed = setup_direction in {
                    decision.pa_1m.direction,
                    decision.pa_5m.direction,
                } or any(
                    str(frame.structure) == aligned_structure
                    for frame in (decision.pa_1m, decision.pa_5m)
                )
                five_coin_resumption = same_market_context and breadth_count == len(
                    TESTNET_EXPERIMENT_SYMBOLS
                )
                if setup_phase == "PULLBACK_OBSERVED":
                    if resumption_bps >= limits.pullback_resumption_bps:
                        setup["price_confirmation_round"] = evaluation_round
                    if price_action_resumed or five_coin_resumption:
                        setup["owner_confirmation_round"] = evaluation_round
                    sign = Decimal(1) if setup_direction is Direction.LONG else Decimal(-1)
                    directional_trade = sign * decision.order_flow.trade_imbalance
                    directional_book = sign * decision.order_flow.book_imbalance
                    directional_microprice = sign * decision.order_flow.microprice_mid_bps
                    aligned_secondary_flow = (
                        directional_book >= limits.minimum_book_imbalance
                        or directional_microprice >= limits.minimum_microprice_bps
                    )
                    if (
                        decision.order_flow.valid
                        and directional_trade >= limits.minimum_trade_imbalance
                        and aligned_secondary_flow
                    ):
                        setup.update(
                            {
                                "flow_confirmation_round": evaluation_round,
                                "confirmed_directional_trade_imbalance": format(
                                    directional_trade, "f"
                                ),
                                "confirmed_directional_book_imbalance": format(
                                    directional_book, "f"
                                ),
                                "confirmed_directional_microprice_bps": format(
                                    directional_microprice, "f"
                                ),
                                "confirmed_aggressive_notional": format(
                                    decision.order_flow.aggressive_notional, "f"
                                ),
                            }
                        )
                    confirmation_rounds = [
                        int(str(setup[key]))
                        for key in (
                            "price_confirmation_round",
                            "owner_confirmation_round",
                            "flow_confirmation_round",
                        )
                        if key in setup
                    ]
                    pullback_observed_round = int(
                        str(setup.get("pullback_observed_round", evaluation_round))
                    )
                    trigger = (
                        len(confirmation_rounds) == 3
                        and min(confirmation_rounds) >= pullback_observed_round
                        and evaluation_round - min(confirmation_rounds)
                        <= limits.signal_evidence_window_rounds
                    )
                reason = (
                    "SETUP_LATCHED_EVIDENCE_COMPLETE"
                    if trigger
                    else (
                        "SETUP_WAITING_FOR_LATCHED_EVIDENCE"
                        if setup_phase == "PULLBACK_OBSERVED"
                        else "SETUP_WAITING_FOR_PULLBACK"
                    )
                )
        entry_direction = Direction(str(setup["direction"])) if setup is not None else direction
        current_fast_momentum = fast_momentum.get(decision.symbol, Decimal(0))
        current_entry_directional_momentum = (
            current_fast_momentum
            if entry_direction is Direction.LONG
            else (-current_fast_momentum if entry_direction is Direction.SHORT else Decimal(0))
        )
        entry_breadth_count = breadth_count
        entry_momentum = current_fast_momentum
        target_feasibility = (
            decision.long_target_feasibility_rate_15m
            if entry_direction is Direction.LONG
            else decision.short_target_feasibility_rate_15m
        )
        aligned_structure = "HH_HL" if entry_direction is Direction.LONG else "LH_LL"
        price_action_resumed = entry_direction in {Direction.LONG, Direction.SHORT} and (
            entry_direction in {decision.pa_1m.direction, decision.pa_5m.direction}
            or any(
                str(frame.structure) == aligned_structure
                for frame in (decision.pa_1m, decision.pa_5m)
            )
        )
        same_market_context = context is not None and direction is entry_direction
        five_coin_resumption = same_market_context and breadth_count == len(
            TESTNET_EXPERIMENT_SYMBOLS
        )
        current_context_required_breadth = (
            limits.impulse_minimum_breadth_count
            if setup_type == "MARKET_BREADTH_IMPULSE_FAST"
            else limits.continuation_minimum_breadth_count
        )
        pullback_current_context_ready = (
            same_market_context
            and breadth_count >= current_context_required_breadth
            and limits.impulse_minimum_momentum_bps
            <= current_entry_directional_momentum
            <= limits.continuation_maximum_momentum_bps
        )
        if trigger and not pullback_current_context_ready:
            reason = "CURRENT_MARKET_CONTEXT_NOT_CONFIRMED"
        if (
            trigger
            and pullback_current_context_ready
            and setup is not None
            and decision.symbol in TESTNET_IMPULSE_ENTRY_SYMBOLS
        ):
            plan_without_feasibility = build_market_impulse_plan(
                decision,
                direction=entry_direction,
                momentum_bps=entry_momentum,
                breadth_count=entry_breadth_count,
                parameters=replace(parameters, minimum_target_feasibility_rate_15m=Decimal(0)),
                setup_type="MARKET_BREADTH_PULLBACK_RESUMPTION",
                confirmed_directional_trade_imbalance=Decimal(
                    str(setup["confirmed_directional_trade_imbalance"])
                ),
                confirmed_directional_book_imbalance=Decimal(
                    str(setup["confirmed_directional_book_imbalance"])
                ),
                confirmed_directional_microprice_bps=Decimal(
                    str(setup["confirmed_directional_microprice_bps"])
                ),
                confirmed_aggressive_notional=Decimal(str(setup["confirmed_aggressive_notional"])),
            )
            if plan_without_feasibility is None:
                reason = "MICROSTRUCTURE_OR_PA_REJECTED"
            elif target_feasibility < selected_target_feasibility:
                reason = "TARGET_FEASIBILITY_INSUFFICIENT"
            else:
                plan = plan_without_feasibility
                reason = "PLAN_GENERATED"
                setups.pop(decision.symbol, None)
        continuation_directional_momentum = (
            current_fast_momentum
            if direction is Direction.LONG
            else (-current_fast_momentum if direction is Direction.SHORT else Decimal(0))
        )
        continuation_context_ready = (
            context is not None
            and breadth_count >= current_context_required_breadth
            and continuation_confirmation_count >= limits.continuation_confirmation_rounds
        )
        continuation_local_momentum_ready = (
            limits.continuation_minimum_momentum_bps
            <= continuation_directional_momentum
            <= limits.continuation_maximum_momentum_bps
        )
        continuation_already_fired = (
            continuation_episodes.get(decision.symbol) == continuation_episode
        )
        if (
            plan is None
            and decision.symbol in TESTNET_IMPULSE_ENTRY_SYMBOLS
            and continuation_context_ready
            and continuation_local_momentum_ready
            and not continuation_already_fired
        ):
            continuation_plan_without_feasibility = build_market_impulse_plan(
                decision,
                direction=direction,
                momentum_bps=current_fast_momentum,
                breadth_count=breadth_count,
                parameters=replace(parameters, minimum_target_feasibility_rate_15m=Decimal(0)),
                setup_type="MARKET_BREADTH_CONTINUATION",
            )
            if continuation_plan_without_feasibility is None:
                reason = "CONTINUATION_MICROSTRUCTURE_OR_PA_REJECTED"
            elif target_feasibility < selected_target_feasibility:
                reason = "CONTINUATION_TARGET_FEASIBILITY_INSUFFICIENT"
            else:
                plan = continuation_plan_without_feasibility
                reason = "CONTINUATION_PLAN_GENERATED"
                continuation_episodes[decision.symbol] = continuation_episode
                setups.pop(decision.symbol, None)
        symbol_diagnostics = cast(dict[str, object], diagnostics["symbols"])
        symbol_diagnostics[decision.symbol] = {
            "fast_momentum_bps": format(fast_momentum.get(decision.symbol, Decimal(0)), "f"),
            "sustained_momentum_bps": format(
                sustained_momentum.get(decision.symbol, Decimal(0)), "f"
            ),
            "gate_result": reason,
            "plan_generated": plan is not None,
            "old_direct_plan_disabled": existing_plan_disabled,
            "entry_state": setup_phase,
            "pullback_bps": format(pullback_bps, "f"),
            "required_pullback_bps": format(limits.pullback_minimum_bps, "f"),
            "resumption_bps": format(resumption_bps, "f"),
            "required_resumption_bps": format(limits.pullback_resumption_bps, "f"),
            "price_action_resumed": price_action_resumed,
            "five_coin_resumption": five_coin_resumption,
            "current_context_required_breadth_count": current_context_required_breadth,
            "pullback_current_context_ready": pullback_current_context_ready,
            "latched_price_confirmation_round": (
                None if setup is None else setup.get("price_confirmation_round")
            ),
            "latched_owner_confirmation_round": (
                None if setup is None else setup.get("owner_confirmation_round")
            ),
            "latched_flow_confirmation_round": (
                None if setup is None else setup.get("flow_confirmation_round")
            ),
            "evidence_window_rounds": limits.signal_evidence_window_rounds,
            "continuation_context_ready": continuation_context_ready,
            "continuation_local_momentum_bps": format(continuation_directional_momentum, "f"),
            "continuation_local_momentum_ready": continuation_local_momentum_ready,
            "continuation_already_fired": continuation_already_fired,
            "pa_1m_direction": str(decision.pa_1m.direction),
            "pa_5m_direction": str(decision.pa_5m.direction),
            "pa_1h_direction": (None if decision.pa_1h is None else str(decision.pa_1h.direction)),
            "directional_trade_imbalance": format(
                decision.order_flow.trade_imbalance
                if entry_direction is Direction.LONG
                else -decision.order_flow.trade_imbalance,
                "f",
            ),
            "directional_book_imbalance": format(
                decision.order_flow.book_imbalance
                if entry_direction is Direction.LONG
                else -decision.order_flow.book_imbalance,
                "f",
            ),
            "directional_microprice_bps": format(
                decision.order_flow.microprice_mid_bps
                if entry_direction is Direction.LONG
                else -decision.order_flow.microprice_mid_bps,
                "f",
            ),
            "spread_bps": format(decision.spread_bps, "f"),
            "strong_breadth_consensus_policy": entry_breadth_count >= 4,
            "target_feasibility_rate_15m": format(target_feasibility, "f"),
            "required_target_feasibility_rate_15m": format(selected_target_feasibility, "f"),
        }
        count(reason)
        promoted.append(replace(decision, experimental_plan=plan))
    return promoted


def _median_decimal(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal(0)
    midpoint = len(values) // 2
    if len(values) % 2:
        return values[midpoint]
    return (values[midpoint - 1] + values[midpoint]) / Decimal(2)


def _plan_directional_forecast_bps(
    plan: TestnetExperimentalPlan, current_price: Decimal
) -> Decimal:
    if current_price <= 0 or plan.predictive_average_20m <= 0:
        return Decimal("-Infinity")
    sign = Decimal(1) if plan.direction is Direction.LONG else Decimal(-1)
    return sign * (plan.predictive_average_20m / current_price - Decimal(1)) * Decimal(10_000)


def _experimental_candidate_rank(decision: TestnetBaselineDecision) -> tuple[Decimal, str]:
    """Prefer persistent PA alignment and stronger executable order flow."""
    plan = decision.experimental_plan
    if plan is None:
        return Decimal("Infinity"), decision.symbol
    return -plan.signal_quality_score, decision.symbol


def _money(value: object) -> str:
    return format(Decimal(str(value)).quantize(Decimal("0.000001")), "f")


def _two_decimals(value: object) -> str:
    return format(Decimal(str(value)).quantize(Decimal("0.01")), "f")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _optional_time(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("campaign deadline is invalid")
    return _parse_time(value)


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
    parser.add_argument(
        "--duration-seconds",
        type=int,
        default=0,
        help="zero runs continuously; otherwise 60-604800 seconds",
    )
    parser.add_argument("--evaluation-interval-seconds", type=int, default=10)
    parser.add_argument("--trade-cooldown-seconds", type=int, default=0)
    parser.add_argument("--maximum-trades-per-day", type=int, default=8)
    parser.add_argument("--daily-net-loss-limit", type=Decimal, default=Decimal("1.00"))
    parser.add_argument("--margin-budget", type=Decimal, default=Decimal("1"))
    parser.add_argument("--maximum-net-loss-per-trade", type=Decimal, default=Decimal("1.00"))
    parser.add_argument("--maximum-parallel-positions", type=int, default=5)
    parser.add_argument("--maximum-candidates-per-round", type=int, default=5)
    parser.add_argument("--signal-confirmation-rounds", type=int, default=2)
    parser.add_argument("--impulse-confirmation-rounds", type=int, default=1)
    parser.add_argument("--minimum-signal-quality-score", type=Decimal, default=Decimal("2.00"))
    parser.add_argument("--minimum-directional-forecast-bps", type=Decimal, default=Decimal("2.00"))
    parser.add_argument(
        "--impulse-minimum-directional-forecast-bps",
        type=Decimal,
        default=Decimal("0.10"),
    )
    parser.add_argument(
        "--continuation-minimum-directional-forecast-bps",
        type=Decimal,
        default=Decimal("2.00"),
    )
    parser.add_argument(
        "--structure-substitute-minimum-directional-forecast-bps",
        type=Decimal,
        default=Decimal("3.00"),
    )
    parser.add_argument(
        "--structure-substitute-minimum-trade-imbalance",
        type=Decimal,
        default=Decimal("0.75"),
    )
    parser.add_argument(
        "--structure-substitute-minimum-secondary-flow",
        type=Decimal,
        default=Decimal("0.10"),
    )
    parser.add_argument(
        "--minimum-target-feasibility-rate-15m",
        type=Decimal,
        default=Decimal("0.30"),
    )
    parser.add_argument(
        "--impulse-minimum-target-feasibility-rate-15m",
        type=Decimal,
        default=Decimal("0.02"),
    )
    parser.add_argument("--minimum-estimated-net-target", type=Decimal, default=Decimal("0.10"))
    parser.add_argument("--minimum-net-reward-risk-ratio", type=Decimal, default=Decimal("0.50"))
    parser.add_argument(
        "--impulse-minimum-net-reward-risk-ratio",
        type=Decimal,
        default=Decimal("0.15"),
    )
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
    parser.add_argument(
        "--activity-filter-enabled",
        action=argparse.BooleanOptionalAction,
        default=False,
    )
    parser.add_argument(
        "--impulse-activity-filter-enabled",
        action=argparse.BooleanOptionalAction,
        default=False,
    )
    parser.add_argument("--impulse-minimum-activity-ratio", type=Decimal, default=Decimal("0.10"))
    parser.add_argument("--impulse-maximum-activity-ratio", type=Decimal, default=Decimal("10.00"))
    parser.add_argument("--impulse-lookback-rounds", type=int, default=10)
    parser.add_argument("--impulse-minimum-momentum-bps", type=Decimal, default=Decimal("2.00"))
    parser.add_argument("--impulse-maximum-momentum-bps", type=Decimal, default=Decimal("8.00"))
    parser.add_argument("--impulse-minimum-breadth-count", type=int, default=3)
    parser.add_argument("--sustained-lookback-rounds", type=int, default=22)
    parser.add_argument("--sustained-minimum-momentum-bps", type=Decimal, default=Decimal("5.00"))
    parser.add_argument("--pullback-minimum-bps", type=Decimal, default=Decimal("3.00"))
    parser.add_argument("--pullback-resumption-bps", type=Decimal, default=Decimal("0.50"))
    parser.add_argument("--pullback-maximum-bps", type=Decimal, default=Decimal("40.00"))
    parser.add_argument("--pullback-setup-maximum-rounds", type=int, default=60)
    parser.add_argument("--signal-evidence-window-rounds", type=int, default=6)
    parser.add_argument("--continuation-minimum-breadth-count", type=int, default=4)
    parser.add_argument("--continuation-confirmation-rounds", type=int, default=4)
    parser.add_argument(
        "--continuation-minimum-momentum-bps", type=Decimal, default=Decimal("4.00")
    )
    parser.add_argument(
        "--continuation-maximum-momentum-bps", type=Decimal, default=Decimal("15.00")
    )
    parser.add_argument(
        "--position-failed-followthrough-peak-bps", type=Decimal, default=Decimal("6.00")
    )
    parser.add_argument(
        "--position-adverse-invalidation-bps", type=Decimal, default=Decimal("10.00")
    )
    parser.add_argument(
        "--position-profit-protection-peak-bps", type=Decimal, default=Decimal("20.00")
    )
    parser.add_argument(
        "--position-profit-giveback-bps", type=Decimal, default=Decimal("10.00")
    )
    parser.add_argument("--position-opposition-confirmation-rounds", type=int, default=2)
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
        minimum_directional_forecast_bps=arguments.minimum_directional_forecast_bps,
        impulse_minimum_directional_forecast_bps=(
            arguments.impulse_minimum_directional_forecast_bps
        ),
        continuation_minimum_directional_forecast_bps=(
            arguments.continuation_minimum_directional_forecast_bps
        ),
        structure_substitute_minimum_directional_forecast_bps=(
            arguments.structure_substitute_minimum_directional_forecast_bps
        ),
        structure_substitute_minimum_trade_imbalance=(
            arguments.structure_substitute_minimum_trade_imbalance
        ),
        structure_substitute_minimum_secondary_flow=(
            arguments.structure_substitute_minimum_secondary_flow
        ),
        minimum_target_feasibility_rate_15m=(arguments.minimum_target_feasibility_rate_15m),
        impulse_minimum_target_feasibility_rate_15m=(
            arguments.impulse_minimum_target_feasibility_rate_15m
        ),
        minimum_estimated_net_target=arguments.minimum_estimated_net_target,
        minimum_net_reward_risk_ratio=arguments.minimum_net_reward_risk_ratio,
        impulse_minimum_net_reward_risk_ratio=(arguments.impulse_minimum_net_reward_risk_ratio),
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
        activity_filter_enabled=arguments.activity_filter_enabled,
        impulse_activity_filter_enabled=arguments.impulse_activity_filter_enabled,
        impulse_minimum_activity_ratio=arguments.impulse_minimum_activity_ratio,
        impulse_maximum_activity_ratio=arguments.impulse_maximum_activity_ratio,
        impulse_lookback_rounds=arguments.impulse_lookback_rounds,
        impulse_minimum_momentum_bps=arguments.impulse_minimum_momentum_bps,
        impulse_maximum_momentum_bps=arguments.impulse_maximum_momentum_bps,
        impulse_minimum_breadth_count=arguments.impulse_minimum_breadth_count,
        sustained_lookback_rounds=arguments.sustained_lookback_rounds,
        sustained_minimum_momentum_bps=arguments.sustained_minimum_momentum_bps,
        pullback_minimum_bps=arguments.pullback_minimum_bps,
        pullback_resumption_bps=arguments.pullback_resumption_bps,
        pullback_maximum_bps=arguments.pullback_maximum_bps,
        pullback_setup_maximum_rounds=arguments.pullback_setup_maximum_rounds,
        signal_evidence_window_rounds=arguments.signal_evidence_window_rounds,
        continuation_minimum_breadth_count=arguments.continuation_minimum_breadth_count,
        continuation_confirmation_rounds=arguments.continuation_confirmation_rounds,
        continuation_minimum_momentum_bps=arguments.continuation_minimum_momentum_bps,
        continuation_maximum_momentum_bps=arguments.continuation_maximum_momentum_bps,
        position_failed_followthrough_peak_bps=(
            arguments.position_failed_followthrough_peak_bps
        ),
        position_adverse_invalidation_bps=arguments.position_adverse_invalidation_bps,
        position_profit_protection_peak_bps=(arguments.position_profit_protection_peak_bps),
        position_profit_giveback_bps=arguments.position_profit_giveback_bps,
        position_opposition_confirmation_rounds=(
            arguments.position_opposition_confirmation_rounds
        ),
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
