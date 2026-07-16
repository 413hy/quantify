"""Execute one unvalidated Testnet structural experiment with native protection."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path
from queue import Empty, SimpleQueue
from typing import Any

from ai_quant.binance_egress.testnet_probe import (
    BinanceTestnetClient,
    TestnetProbeError,
    _credential,
    _decimal_step,
    _flatten_position,
    _position_quantity,
    _query_algo_consistent,
    _symbol_filters,
    _terminalize_algo_after_flat,
)
from ai_quant.features.price_action import Direction
from ai_quant.strategy.testnet_baseline import TestnetExperimentalPlan


@dataclass(slots=True)
class PositionSignalControl:
    """Single-owner mailbox for confirmed signals targeting an existing position."""

    signals: SimpleQueue[TestnetExperimentalPlan] = field(default_factory=SimpleQueue)
    replacement_plan: TestnetExperimentalPlan | None = None

    def submit(self, plan: TestnetExperimentalPlan) -> None:
        self.signals.put(plan)

    def take_latest(self) -> TestnetExperimentalPlan | None:
        latest: TestnetExperimentalPlan | None = None
        while True:
            try:
                latest = self.signals.get_nowait()
            except Empty:
                return latest


def _opposing_signal_action(
    plan: TestnetExperimentalPlan,
) -> tuple[TestnetExperimentalPlan | None, str, str]:
    """Separate exit-only invalidation from an approved reversal entry."""
    if plan.exit_only:
        return None, "TESTNET_POSITION_INVALIDATION_REQUESTED", "SIGNAL_INVALIDATION"
    return plan, "TESTNET_POSITION_REVERSAL_REQUESTED", "SIGNAL_REVERSAL"


@dataclass(frozen=True, slots=True)
class _ScaleInPreparation:
    quantity: Decimal
    entry_reference_price: Decimal
    directional_forecast_bps: Decimal
    entry_forecast_model: str
    effective_margin_budget: Decimal
    combined_entry: Decimal
    stop_trigger: Decimal
    target_trigger: Decimal


def _position_snapshot(
    client: BinanceTestnetClient,
    symbol: str,
    direction: Direction,
) -> tuple[Decimal, Decimal]:
    matches = [
        item
        for item in client.position_risk(symbol)
        if (direction is Direction.LONG and Decimal(str(item.get("positionAmt", "0"))) > 0)
        or (direction is Direction.SHORT and Decimal(str(item.get("positionAmt", "0"))) < 0)
    ]
    if len(matches) != 1:
        raise TestnetProbeError("EXPERIMENT_POSITION_SNAPSHOT_MISSING")
    quantity = abs(Decimal(str(matches[0].get("positionAmt", "0"))))
    entry_price = Decimal(str(matches[0].get("entryPrice", "0")))
    if quantity <= 0 or entry_price <= 0:
        raise TestnetProbeError("EXPERIMENT_POSITION_SNAPSHOT_MISSING")
    return quantity, entry_price


def _prepare_scale_in(
    client: BinanceTestnetClient,
    *,
    plan: TestnetExperimentalPlan,
    exchange_info: Mapping[str, Any],
    leverage: int,
    margin_ceiling: Decimal,
    maximum_net_loss: Decimal,
    minimum_estimated_net_target: Decimal,
    minimum_net_reward_risk_ratio: Decimal = Decimal(0),
    risk_sizing_slippage_rate: Decimal,
    taker_fee_rate: Decimal,
    current_quantity: Decimal,
    current_entry: Decimal,
    current_stop: Decimal,
) -> _ScaleInPreparation:
    """Size one same-direction add while keeping the whole position in budget."""
    ticker = client.book_ticker(plan.symbol)
    filters = _symbol_filters(exchange_info, plan.symbol)
    try:
        bid_price = Decimal(str(ticker["bidPrice"]))
        ask_price = Decimal(str(ticker["askPrice"]))
        tick_size = Decimal(str(filters["PRICE_FILTER"]["tickSize"]))
        lot = filters.get("MARKET_LOT_SIZE") or filters["LOT_SIZE"]
        step_size = Decimal(str(lot["stepSize"]))
        minimum_quantity = Decimal(str(lot["minQty"]))
        minimum_notional = Decimal(str(filters.get("MIN_NOTIONAL", {}).get("notional", "0")))
    except (KeyError, ArithmeticError) as exc:
        raise TestnetProbeError("TEST_SYMBOL_FILTERS_INVALID") from exc
    (
        entry_reference_price,
        directional_forecast_bps,
        entry_forecast_model,
    ) = market_entry_reference(
        plan.direction,
        bid_price=bid_price,
        ask_price=ask_price,
        predictive_average_20m=plan.predictive_average_20m,
        minimum_forecast_edge_bps=plan.minimum_directional_forecast_bps,
    )
    loss_sign = Decimal(1) if plan.direction is Direction.LONG else Decimal(-1)
    cost_rate = taker_fee_rate * Decimal(2) + risk_sizing_slippage_rate
    existing_unit_risk = (
        max(Decimal(0), loss_sign * (current_entry - current_stop)) + current_entry * cost_rate
    )
    existing_risk = current_quantity * existing_unit_risk
    remaining_risk = maximum_net_loss - existing_risk
    if remaining_risk <= 0:
        raise TestnetProbeError("EXPERIMENT_SCALE_RISK_BUDGET_EXHAUSTED")
    effective_margin_budget = risk_adjusted_margin_budget(
        plan,
        margin_ceiling=margin_ceiling,
        leverage=leverage,
        maximum_net_loss=remaining_risk,
        taker_fee_rate=taker_fee_rate,
        adverse_slippage_rate=risk_sizing_slippage_rate,
    )
    desired_quantity = plan_market_quantity(
        exchange_info,
        symbol=plan.symbol,
        reference_price=entry_reference_price,
        margin_budget=effective_margin_budget,
        leverage=leverage,
    )
    added_unit_risk = (
        max(Decimal(0), loss_sign * (entry_reference_price - current_stop))
        + entry_reference_price * cost_rate
    )
    if added_unit_risk <= 0:
        raise TestnetProbeError("EXPERIMENT_SCALE_RISK_ESTIMATE_INVALID")
    risk_limited_quantity = _decimal_step(remaining_risk / added_unit_risk, step_size, ROUND_FLOOR)
    quantity = min(desired_quantity, risk_limited_quantity)
    required_quantity = minimum_quantity
    if minimum_notional > 0:
        required_quantity = max(
            required_quantity,
            _decimal_step(minimum_notional / entry_reference_price, step_size, ROUND_CEILING),
        )
    if quantity < required_quantity or quantity <= 0:
        raise TestnetProbeError("EXPERIMENT_SCALE_REMAINING_RISK_BELOW_EXCHANGE_MINIMUM")
    combined_quantity = current_quantity + quantity
    combined_entry = (
        current_quantity * current_entry + quantity * entry_reference_price
    ) / combined_quantity
    combined_plan = replace(plan, stop_anchor=current_stop)
    stop_trigger, target_trigger = quantize_protection(
        combined_plan,
        actual_entry=combined_entry,
        tick_size=tick_size,
    )
    if (plan.direction is Direction.LONG and target_trigger <= ask_price) or (
        plan.direction is Direction.SHORT and target_trigger >= bid_price
    ):
        raise TestnetProbeError("EXPERIMENT_SCALE_TARGET_ALREADY_CROSSED")
    _, _, target_net, stop_net_loss = estimated_position_outcomes(
        quantity=combined_quantity,
        actual_entry=combined_entry,
        stop_trigger=stop_trigger,
        target_trigger=target_trigger,
        taker_fee_rate=taker_fee_rate,
        adverse_slippage_rate=risk_sizing_slippage_rate,
    )
    if stop_net_loss > maximum_net_loss:
        raise TestnetProbeError("EXPERIMENT_SCALE_EXCEEDS_LOSS_BUDGET")
    if target_net < minimum_estimated_net_target:
        raise TestnetProbeError("EXPERIMENT_SCALE_NET_TARGET_INSUFFICIENT")
    if stop_net_loss <= 0 or target_net / stop_net_loss < minimum_net_reward_risk_ratio:
        raise TestnetProbeError("EXPERIMENT_NET_REWARD_RISK_INSUFFICIENT")
    return _ScaleInPreparation(
        quantity=quantity,
        entry_reference_price=entry_reference_price,
        directional_forecast_bps=directional_forecast_bps,
        entry_forecast_model=entry_forecast_model,
        effective_margin_budget=effective_margin_budget,
        combined_entry=combined_entry,
        stop_trigger=stop_trigger,
        target_trigger=target_trigger,
    )


def _position_control_event(
    *,
    record_type: str,
    plan: TestnetExperimentalPlan,
    reason_code: str,
) -> dict[str, Any]:
    return {
        "record_type": record_type,
        "environment": "testnet",
        "validation_status": "UNVALIDATED_TESTNET_EXPERIMENT",
        "strategy": plan.strategy_version,
        "symbol": plan.symbol,
        "direction": plan.direction,
        "signal_quality_score": format(plan.signal_quality_score, "f"),
        "reason_code": reason_code,
        "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "production_endpoint_requests": 0,
    }


def plan_market_quantity(
    exchange_info: Mapping[str, Any],
    *,
    symbol: str,
    reference_price: Decimal,
    margin_budget: Decimal,
    leverage: int,
) -> Decimal:
    """Size at or below the margin budget, rejecting incompatible exchange minima."""
    if reference_price <= 0 or not Decimal("0") < margin_budget <= Decimal("1"):
        raise TestnetProbeError("EXPERIMENT_SIZE_INPUT_INVALID")
    filters = _symbol_filters(exchange_info, symbol)
    try:
        lot = filters.get("MARKET_LOT_SIZE") or filters["LOT_SIZE"]
        step = Decimal(str(lot["stepSize"]))
        minimum = Decimal(str(lot["minQty"]))
        minimum_notional = Decimal(str(filters.get("MIN_NOTIONAL", {}).get("notional", "0")))
    except (KeyError, ArithmeticError) as exc:
        raise TestnetProbeError("TEST_SYMBOL_FILTERS_INVALID") from exc
    maximum_notional = margin_budget * Decimal(leverage) * Decimal("0.95")
    quantity = _decimal_step(maximum_notional / reference_price, step, ROUND_FLOOR)
    required = minimum
    if minimum_notional > 0:
        required = max(
            required,
            _decimal_step(minimum_notional / reference_price, step, ROUND_CEILING),
        )
    if quantity < required or quantity <= 0:
        raise TestnetProbeError("EXPERIMENT_EXCHANGE_MINIMUM_EXCEEDS_MARGIN_BUDGET")
    return quantity


def risk_adjusted_margin_budget(
    plan: TestnetExperimentalPlan,
    *,
    margin_ceiling: Decimal,
    leverage: int,
    maximum_net_loss: Decimal,
    taker_fee_rate: Decimal,
    adverse_slippage_rate: Decimal = Decimal("0.0012"),
) -> Decimal:
    """Shrink margin so stop, round-trip fees, and slippage fit the loss budget."""
    if (
        not Decimal(0) < margin_ceiling <= Decimal(1)
        or maximum_net_loss <= 0
        or taker_fee_rate < 0
        or adverse_slippage_rate < 0
    ):
        raise TestnetProbeError("EXPERIMENT_RISK_BUDGET_INVALID")
    stop_fraction = abs(plan.entry_reference - plan.stop_anchor) / plan.entry_reference
    loss_fraction = stop_fraction + taker_fee_rate * Decimal(2) + adverse_slippage_rate
    if loss_fraction <= 0:
        raise TestnetProbeError("EXPERIMENT_RISK_BUDGET_INVALID")
    risk_margin = maximum_net_loss / loss_fraction / Decimal(leverage)
    return min(margin_ceiling, risk_margin)


def quantize_protection(
    plan: TestnetExperimentalPlan,
    *,
    actual_entry: Decimal,
    tick_size: Decimal,
) -> tuple[Decimal, Decimal]:
    """Keep the structural stop and target distance valid after the actual fill."""
    if min(actual_entry, tick_size) <= 0:
        raise TestnetProbeError("EXPERIMENT_PROTECTION_INPUT_INVALID")
    target_bps = abs(plan.target_reference - plan.entry_reference) / plan.entry_reference
    if plan.direction is Direction.LONG:
        stop = _decimal_step(plan.stop_anchor, tick_size, ROUND_FLOOR)
        target = _decimal_step(actual_entry * (Decimal(1) + target_bps), tick_size, ROUND_CEILING)
        valid = stop < actual_entry < target
    elif plan.direction is Direction.SHORT:
        stop = _decimal_step(plan.stop_anchor, tick_size, ROUND_CEILING)
        target = _decimal_step(actual_entry * (Decimal(1) - target_bps), tick_size, ROUND_FLOOR)
        valid = target < actual_entry < stop
    else:
        raise TestnetProbeError("EXPERIMENT_DIRECTION_INVALID")
    risk_bps = abs(actual_entry - stop) / actual_entry * Decimal(10_000)
    if not valid or not Decimal(28) <= risk_bps <= Decimal(120):
        raise TestnetProbeError("EXPERIMENT_ACTUAL_FILL_INVALIDATES_PROTECTION")
    return stop, target


def market_entry_reference(
    direction: Direction,
    *,
    bid_price: Decimal,
    ask_price: Decimal,
    predictive_average_20m: Decimal,
    minimum_forecast_edge_bps: Decimal = Decimal("1"),
) -> tuple[Decimal, Decimal, str]:
    """Validate the forecast and return the immediately executable book side."""
    if (
        min(bid_price, ask_price, predictive_average_20m) <= 0
        or minimum_forecast_edge_bps < 0
        or bid_price >= ask_price
    ):
        raise TestnetProbeError("EXPERIMENT_MARKET_REFERENCE_INPUT_INVALID")
    mid_price = (bid_price + ask_price) / Decimal(2)
    sign = Decimal(1) if direction is Direction.LONG else Decimal(-1)
    directional_forecast_bps = (
        sign * (predictive_average_20m / mid_price - Decimal(1)) * Decimal(10_000)
    )
    if minimum_forecast_edge_bps > 0:
        if directional_forecast_bps <= 0:
            raise TestnetProbeError("EXPERIMENT_PREDICTIVE_DIRECTION_CONFLICT")
        if directional_forecast_bps < minimum_forecast_edge_bps:
            raise TestnetProbeError("EXPERIMENT_PREDICTIVE_EDGE_INSUFFICIENT")
        entry_model = "FORECAST_ALIGNED_MARKET_REFERENCE"
    else:
        entry_model = "STRONG_BREADTH_MARKET_REFERENCE_FORECAST_DIAGNOSTIC_ONLY"
    if direction is Direction.LONG:
        return ask_price, directional_forecast_bps, entry_model
    if direction is Direction.SHORT:
        return bid_price, directional_forecast_bps, entry_model
    raise TestnetProbeError("EXPERIMENT_DIRECTION_INVALID")


def _confirmed_market_entry(
    client: BinanceTestnetClient,
    *,
    symbol: str,
    side: str,
    quantity: Decimal,
    client_order_id: str,
    position_guard: Callable[[], bool] | None = None,
) -> tuple[dict[str, Any] | None, Decimal, str]:
    """Execute a fully confirmed Testnet signal without a limit-order stage."""
    if position_guard is not None and not position_guard():
        return None, Decimal(0), "PARENT_POSITION_CLOSED"
    document = client.place_order(
        {
            "symbol": symbol,
            "side": side,
            "positionSide": "BOTH",
            "type": "MARKET",
            "quantity": format(quantity, "f"),
            "newOrderRespType": "RESULT",
            "newClientOrderId": client_order_id,
        }
    )
    executed = Decimal(str(document.get("executedQty", "0")))
    if executed >= quantity and document.get("status") == "FILLED":
        return document, executed, "CONFIRMED_SIGNAL_MARKET_FILLED"
    if executed > 0:
        return document, executed, "CONFIRMED_SIGNAL_MARKET_PARTIALLY_FILLED"
    return document, Decimal(0), "CONFIRMED_SIGNAL_MARKET_NOT_FILLED"


def run_structural_experiment(
    *,
    api_key_file: Path,
    api_secret_file: Path,
    repository_root: Path,
    plan: TestnetExperimentalPlan,
    margin_budget: Decimal = Decimal("1"),
    maximum_net_loss: Decimal = Decimal("1.00"),
    minimum_estimated_net_target: Decimal = Decimal("0.10"),
    minimum_net_reward_risk_ratio: Decimal = Decimal("0"),
    risk_sizing_slippage_rate: Decimal = Decimal("0.0012"),
    on_entry_attempt: Callable[[dict[str, Any]], None] | None = None,
    on_position_protected: Callable[[dict[str, Any]], None] | None = None,
    on_position_control: Callable[[dict[str, Any]], None] | None = None,
    position_control: PositionSignalControl | None = None,
    stop_requested: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Open one Testnet position and wait only for strategy protection or operator stop."""
    if (
        not Decimal(0) <= minimum_estimated_net_target <= Decimal(1)
        or not Decimal(0) <= minimum_net_reward_risk_ratio <= Decimal(1)
        or not Decimal(0) <= risk_sizing_slippage_rate <= Decimal("0.01")
    ):
        raise TestnetProbeError("EXPERIMENT_NET_TARGET_THRESHOLD_INVALID")
    started_at = datetime.now(UTC)
    key = _credential(api_key_file, repository_root)
    secret = _credential(api_secret_file, repository_root)
    client = BinanceTestnetClient(key, secret)
    server_time_ms, offset = client.synchronize_time()
    if abs(offset) > 1_000:
        raise TestnetProbeError("TESTNET_CLOCK_OFFSET_EXCESSIVE")
    if client.position_mode().get("dualSidePosition") is not False:
        raise TestnetProbeError("ACCOUNT_POSITION_MODE_NOT_ONE_WAY")
    symbol = plan.symbol
    if client.open_orders(symbol) or client.open_algo_orders(symbol):
        raise TestnetProbeError("TESTNET_SYMBOL_NOT_CLEAN")
    if _position_quantity(client, symbol) != 0:
        raise TestnetProbeError("TESTNET_SYMBOL_NOT_CLEAN")

    leverage = exchange_maximum_initial_leverage(client.leverage_brackets(symbol), symbol)
    changed = client.change_initial_leverage(symbol, leverage)
    if changed.get("symbol") != symbol or changed.get("leverage") != leverage:
        raise TestnetProbeError("CHANGE_INITIAL_LEVERAGE_RESPONSE_MISMATCH")
    exchange_info = client.exchange_info()
    ticker = client.book_ticker(symbol)
    commission_document = client.commission_rate(symbol)
    try:
        bid_price = Decimal(str(ticker["bidPrice"]))
        ask_price = Decimal(str(ticker["askPrice"]))
        tick_size = Decimal(str(_symbol_filters(exchange_info, symbol)["PRICE_FILTER"]["tickSize"]))
        taker_fee_rate = Decimal(str(commission_document["takerCommissionRate"]))
    except (KeyError, ArithmeticError) as exc:
        raise TestnetProbeError("TEST_SYMBOL_FILTERS_INVALID") from exc
    effective_margin_budget = risk_adjusted_margin_budget(
        plan,
        margin_ceiling=margin_budget,
        leverage=leverage,
        maximum_net_loss=maximum_net_loss,
        taker_fee_rate=taker_fee_rate,
        adverse_slippage_rate=risk_sizing_slippage_rate,
    )
    (
        entry_reference_price,
        directional_forecast_bps,
        entry_forecast_model,
    ) = market_entry_reference(
        plan.direction,
        bid_price=bid_price,
        ask_price=ask_price,
        predictive_average_20m=plan.predictive_average_20m,
        minimum_forecast_edge_bps=plan.minimum_directional_forecast_bps,
    )
    quantity = plan_market_quantity(
        exchange_info,
        symbol=symbol,
        reference_price=entry_reference_price,
        margin_budget=effective_margin_budget,
        leverage=leverage,
    )
    pretrade_stop, pretrade_target = quantize_protection(
        plan, actual_entry=entry_reference_price, tick_size=tick_size
    )
    _, _, estimated_net_target, estimated_stop_net_loss = estimated_position_outcomes(
        quantity=quantity,
        actual_entry=entry_reference_price,
        stop_trigger=pretrade_stop,
        target_trigger=pretrade_target,
        taker_fee_rate=taker_fee_rate,
    )
    if estimated_net_target < minimum_estimated_net_target:
        raise TestnetProbeError("EXPERIMENT_ESTIMATED_NET_TARGET_INSUFFICIENT")
    if estimated_stop_net_loss <= 0 or (
        estimated_net_target / estimated_stop_net_loss < minimum_net_reward_risk_ratio
    ):
        raise TestnetProbeError("EXPERIMENT_NET_REWARD_RISK_INSUFFICIENT")
    entry_side = "BUY" if plan.direction is Direction.LONG else "SELL"
    close_side = "SELL" if plan.direction is Direction.LONG else "BUY"
    market_entry_id = f"aq-t-exp-mkt-{secrets.token_hex(5)}"
    entry_id = market_entry_id
    stop_client_id = f"aqa-t-exp-sl-{secrets.token_hex(5)}"
    target_client_id = f"aqa-t-exp-tp-{secrets.token_hex(5)}"
    entry: dict[str, Any] | None = None
    stop_id: int | None = None
    target_id: int | None = None
    exit_reason = "UNRESOLVED"
    actual_entry = Decimal(0)
    stop_trigger = Decimal(0)
    target_trigger = Decimal(0)
    stop_final_status = "UNRESOLVED"
    target_final_status = "UNRESOLVED"
    entry_execution_mode = "UNRESOLVED"
    execution_error_code: str | None = None
    total_entry_executed_quantity = Decimal(0)
    scale_in_count = 0
    current_plan = plan
    try:
        if on_entry_attempt is not None:
            on_entry_attempt(
                {
                    "record_type": "TESTNET_MARKET_ENTRY_SUBMITTED",
                    "environment": "testnet",
                    "validation_status": "UNVALIDATED_TESTNET_EXPERIMENT",
                    "strategy": plan.strategy_version,
                    "symbol": symbol,
                    "direction": plan.direction,
                    "initial_leverage": leverage,
                    "quantity": format(quantity, "f"),
                    "current_bid_price": format(bid_price, "f"),
                    "current_ask_price": format(ask_price, "f"),
                    "predictive_average_20m": format(plan.predictive_average_20m, "f"),
                    "entry_reference_price": format(entry_reference_price, "f"),
                    "directional_forecast_bps": format(directional_forecast_bps, "f"),
                    "entry_forecast_model": entry_forecast_model,
                    "attempted_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "production_endpoint_requests": 0,
                }
            )
        entry, total_entry_executed_quantity, entry_execution_mode = _confirmed_market_entry(
            client,
            symbol=symbol,
            side=entry_side,
            quantity=quantity,
            client_order_id=market_entry_id,
        )
        if on_entry_attempt is not None:
            on_entry_attempt(
                {
                    "record_type": "TESTNET_MARKET_ENTRY_RESULT",
                    "environment": "testnet",
                    "validation_status": "UNVALIDATED_TESTNET_EXPERIMENT",
                    "strategy": plan.strategy_version,
                    "symbol": symbol,
                    "direction": plan.direction,
                    "client_order_id": (
                        market_entry_id if entry is None else entry.get("clientOrderId")
                    ),
                    "entry_reference_price": format(entry_reference_price, "f"),
                    "execution_mode": entry_execution_mode,
                    "order_status": None if entry is None else entry.get("status"),
                    "executed_quantity": format(total_entry_executed_quantity, "f"),
                    "completed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "production_endpoint_requests": 0,
                }
            )
        if total_entry_executed_quantity > 0:
            quantity = total_entry_executed_quantity
        else:
            raise TestnetProbeError("EXPERIMENT_MARKET_ENTRY_NOT_FILLED")
        if (
            entry is None
            or entry.get("clientOrderId") != market_entry_id
            or Decimal(str(entry.get("executedQty", "0"))) <= 0
        ):
            raise TestnetProbeError("EXPERIMENT_ENTRY_NOT_FILLED")
        entry_id = str(entry["clientOrderId"])
        position = _position_quantity(client, symbol)
        if (plan.direction is Direction.LONG and position <= 0) or (
            plan.direction is Direction.SHORT and position >= 0
        ):
            raise TestnetProbeError("EXPERIMENT_POSITION_DIRECTION_MISMATCH")
        actual_entry = _resolve_entry_price(client, symbol, entry_id, entry, plan.direction)
        stop_trigger, target_trigger = quantize_protection(
            plan, actual_entry=actual_entry, tick_size=tick_size
        )
        _, _, actual_target_net_pnl, actual_stop_net_loss = estimated_position_outcomes(
            quantity=quantity,
            actual_entry=actual_entry,
            stop_trigger=stop_trigger,
            target_trigger=target_trigger,
            taker_fee_rate=taker_fee_rate,
        )
        if actual_stop_net_loss > maximum_net_loss:
            raise TestnetProbeError("EXPERIMENT_ACTUAL_FILL_EXCEEDS_LOSS_BUDGET")
        if actual_target_net_pnl < minimum_estimated_net_target:
            raise TestnetProbeError("EXPERIMENT_ACTUAL_ENTRY_NET_TARGET_INSUFFICIENT")
        if actual_stop_net_loss <= 0 or (
            actual_target_net_pnl / actual_stop_net_loss < minimum_net_reward_risk_ratio
        ):
            raise TestnetProbeError("EXPERIMENT_NET_REWARD_RISK_INSUFFICIENT")
        stop_doc = _place_protection(
            client,
            symbol=symbol,
            side=close_side,
            client_algo_id=stop_client_id,
            order_type="STOP_MARKET",
            trigger_price=stop_trigger,
        )
        stop_id = _confirmed_algo_id(stop_doc, stop_client_id, "STOP")
        _require_algo_new(client, symbol, stop_id, stop_client_id, "STOP")
        target_doc = _place_protection(
            client,
            symbol=symbol,
            side=close_side,
            client_algo_id=target_client_id,
            order_type="TAKE_PROFIT_MARKET",
            trigger_price=target_trigger,
        )
        target_id = _confirmed_algo_id(target_doc, target_client_id, "TAKE_PROFIT")
        _require_algo_new(client, symbol, target_id, target_client_id, "TAKE_PROFIT")

        protected_position = _protected_position_event(
            plan=plan,
            leverage=leverage,
            quantity=quantity,
            actual_entry=actual_entry,
            stop_trigger=stop_trigger,
            target_trigger=target_trigger,
            effective_margin_budget=effective_margin_budget,
            taker_fee_rate=taker_fee_rate,
            entry_execution_mode=entry_execution_mode,
            entry_reference_price=entry_reference_price,
            directional_forecast_bps=directional_forecast_bps,
            entry_forecast_model=entry_forecast_model,
            position_started_at=started_at,
            stop_algo_id=stop_id,
            stop_client_algo_id=stop_client_id,
            target_algo_id=target_id,
            target_client_algo_id=target_client_id,
        )
        if on_position_protected is not None:
            on_position_protected(protected_position)

        while _position_quantity(client, symbol) != 0 and not stop_requested():
            latest_plan = None if position_control is None else position_control.take_latest()
            if latest_plan is None:
                sleep(1)
                continue
            if latest_plan.direction is not current_plan.direction:
                replacement_plan, control_record_type, control_exit_reason = (
                    _opposing_signal_action(latest_plan)
                )
                if position_control is not None:
                    position_control.replacement_plan = replacement_plan
                if on_position_control is not None:
                    on_position_control(
                        {
                            "record_type": control_record_type,
                            "environment": "testnet",
                            "validation_status": "UNVALIDATED_TESTNET_EXPERIMENT",
                            "strategy": latest_plan.strategy_version,
                            "symbol": symbol,
                            "old_direction": current_plan.direction,
                            "new_direction": latest_plan.direction,
                            "exit_only": latest_plan.exit_only,
                            "setup_type": latest_plan.setup_type,
                            "signal_quality_score": format(latest_plan.signal_quality_score, "f"),
                            "requested_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                            "production_endpoint_requests": 0,
                        }
                    )
                closed = _flatten_position(client, symbol)
                if closed is None or closed.get("status") != "FILLED":
                    raise TestnetProbeError("EXPERIMENT_SIGNAL_REVERSAL_NOT_FILLED")
                stop_final_status = _terminalize_algo_after_flat(
                    client,
                    symbol=symbol,
                    algo_id=stop_id,
                    client_algo_id=stop_client_id,
                )
                target_final_status = _terminalize_algo_after_flat(
                    client,
                    symbol=symbol,
                    algo_id=target_id,
                    client_algo_id=target_client_id,
                )
                exit_reason = control_exit_reason
                break

            try:
                scale = _prepare_scale_in(
                    client,
                    plan=latest_plan,
                    exchange_info=exchange_info,
                    leverage=leverage,
                    margin_ceiling=margin_budget,
                    maximum_net_loss=maximum_net_loss,
                    minimum_estimated_net_target=minimum_estimated_net_target,
                    minimum_net_reward_risk_ratio=(minimum_net_reward_risk_ratio),
                    risk_sizing_slippage_rate=risk_sizing_slippage_rate,
                    taker_fee_rate=taker_fee_rate,
                    current_quantity=quantity,
                    current_entry=actual_entry,
                    current_stop=stop_trigger,
                )
            except TestnetProbeError as exc:
                if on_position_control is not None:
                    on_position_control(
                        _position_control_event(
                            record_type="TESTNET_POSITION_SCALE_SKIPPED",
                            plan=latest_plan,
                            reason_code=str(exc),
                        )
                    )
                continue
            scale_order_id = f"aq-t-scale-mkt-{secrets.token_hex(5)}"
            scale_side = "BUY" if latest_plan.direction is Direction.LONG else "SELL"
            if on_entry_attempt is not None:
                on_entry_attempt(
                    {
                        "record_type": "TESTNET_MARKET_SCALE_SUBMITTED",
                        "environment": "testnet",
                        "validation_status": "UNVALIDATED_TESTNET_EXPERIMENT",
                        "strategy": latest_plan.strategy_version,
                        "symbol": symbol,
                        "direction": latest_plan.direction,
                        "quantity": format(scale.quantity, "f"),
                        "entry_reference_price": format(scale.entry_reference_price, "f"),
                        "directional_forecast_bps": format(scale.directional_forecast_bps, "f"),
                        "entry_forecast_model": scale.entry_forecast_model,
                        "attempted_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                        "production_endpoint_requests": 0,
                    }
                )
            scale_direction = latest_plan.direction

            def position_still_open(direction: Direction = scale_direction) -> bool:
                position = _position_quantity(client, symbol)
                return position > 0 if direction is Direction.LONG else position < 0

            scale_document, added_quantity, scale_execution_mode = _confirmed_market_entry(
                client,
                symbol=symbol,
                side=scale_side,
                quantity=scale.quantity,
                client_order_id=scale_order_id,
                position_guard=position_still_open,
            )
            if on_entry_attempt is not None:
                on_entry_attempt(
                    {
                        "record_type": "TESTNET_MARKET_SCALE_RESULT",
                        "environment": "testnet",
                        "validation_status": "UNVALIDATED_TESTNET_EXPERIMENT",
                        "strategy": latest_plan.strategy_version,
                        "symbol": symbol,
                        "direction": latest_plan.direction,
                        "client_order_id": (
                            scale_order_id
                            if scale_document is None
                            else scale_document.get("clientOrderId")
                        ),
                        "entry_reference_price": format(scale.entry_reference_price, "f"),
                        "execution_mode": scale_execution_mode,
                        "order_status": (
                            None if scale_document is None else scale_document.get("status")
                        ),
                        "executed_quantity": format(added_quantity, "f"),
                        "completed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                        "production_endpoint_requests": 0,
                    }
                )
            if added_quantity <= 0:
                if on_position_control is not None:
                    on_position_control(
                        _position_control_event(
                            record_type="TESTNET_POSITION_SCALE_SKIPPED",
                            plan=latest_plan,
                            reason_code=scale_execution_mode,
                        )
                    )
                continue
            total_entry_executed_quantity += added_quantity
            signed_position = _position_quantity(client, symbol)
            if (latest_plan.direction is Direction.LONG and signed_position <= 0) or (
                latest_plan.direction is Direction.SHORT and signed_position >= 0
            ):
                raise TestnetProbeError("EXPERIMENT_POSITION_CLOSED_DURING_SCALE")
            new_quantity, new_entry = _position_snapshot(client, symbol, latest_plan.direction)
            combined_plan = replace(latest_plan, stop_anchor=stop_trigger)
            new_stop, new_target = quantize_protection(
                combined_plan,
                actual_entry=new_entry,
                tick_size=tick_size,
            )
            _, _, new_target_net, new_stop_loss = estimated_position_outcomes(
                quantity=new_quantity,
                actual_entry=new_entry,
                stop_trigger=new_stop,
                target_trigger=new_target,
                taker_fee_rate=taker_fee_rate,
                adverse_slippage_rate=risk_sizing_slippage_rate,
            )
            if new_stop_loss > maximum_net_loss:
                raise TestnetProbeError("EXPERIMENT_SCALE_FILL_EXCEEDS_LOSS_BUDGET")
            if new_target_net < minimum_estimated_net_target:
                raise TestnetProbeError("EXPERIMENT_SCALE_FILL_NET_TARGET_INSUFFICIENT")
            if new_stop_loss <= 0 or (
                new_target_net / new_stop_loss < minimum_net_reward_risk_ratio
            ):
                raise TestnetProbeError("EXPERIMENT_NET_REWARD_RISK_INSUFFICIENT")
            stop_final_status = _terminalize_algo_after_flat(
                client,
                symbol=symbol,
                algo_id=stop_id,
                client_algo_id=stop_client_id,
            )
            target_final_status = _terminalize_algo_after_flat(
                client,
                symbol=symbol,
                algo_id=target_id,
                client_algo_id=target_client_id,
            )
            stop_client_id = f"aqa-t-scale-sl-{secrets.token_hex(5)}"
            target_client_id = f"aqa-t-scale-tp-{secrets.token_hex(5)}"
            stop_doc = _place_protection(
                client,
                symbol=symbol,
                side=close_side,
                client_algo_id=stop_client_id,
                order_type="STOP_MARKET",
                trigger_price=new_stop,
            )
            stop_id = _confirmed_algo_id(stop_doc, stop_client_id, "STOP")
            _require_algo_new(client, symbol, stop_id, stop_client_id, "STOP")
            target_doc = _place_protection(
                client,
                symbol=symbol,
                side=close_side,
                client_algo_id=target_client_id,
                order_type="TAKE_PROFIT_MARKET",
                trigger_price=new_target,
            )
            target_id = _confirmed_algo_id(target_doc, target_client_id, "TAKE_PROFIT")
            _require_algo_new(client, symbol, target_id, target_client_id, "TAKE_PROFIT")
            quantity = new_quantity
            actual_entry = new_entry
            stop_trigger = new_stop
            target_trigger = new_target
            current_plan = latest_plan
            effective_margin_budget += scale.effective_margin_budget
            scale_in_count += 1
            if on_position_control is not None:
                scaled_event = _protected_position_event(
                    plan=current_plan,
                    leverage=leverage,
                    quantity=quantity,
                    actual_entry=actual_entry,
                    stop_trigger=stop_trigger,
                    target_trigger=target_trigger,
                    effective_margin_budget=effective_margin_budget,
                    taker_fee_rate=taker_fee_rate,
                    entry_execution_mode=scale_execution_mode,
                    entry_reference_price=scale.entry_reference_price,
                    directional_forecast_bps=scale.directional_forecast_bps,
                    entry_forecast_model=scale.entry_forecast_model,
                    position_started_at=started_at,
                    stop_algo_id=stop_id,
                    stop_client_algo_id=stop_client_id,
                    target_algo_id=target_id,
                    target_client_algo_id=target_client_id,
                )
                scaled_event["record_type"] = "TESTNET_POSITION_SCALED_AND_REPROTECTED"
                scaled_event["added_quantity"] = format(added_quantity, "f")
                scaled_event["scale_in_count"] = scale_in_count
                on_position_control(scaled_event)
        if exit_reason in {"SIGNAL_REVERSAL", "SIGNAL_INVALIDATION"}:
            pass
        elif _position_quantity(client, symbol) != 0:
            closed = _flatten_position(client, symbol)
            if closed is None or closed.get("status") != "FILLED":
                raise TestnetProbeError("EXPERIMENT_OPERATOR_EXIT_NOT_FILLED")
            exit_reason = "OPERATOR_SERVICE_STOP"
        else:
            exit_reason, stop_final_status, target_final_status = _classify_native_exit(
                client,
                symbol=symbol,
                stop_id=stop_id,
                stop_client_id=stop_client_id,
                target_id=target_id,
                target_client_id=target_client_id,
                sleep=sleep,
            )
        stop_final_status = _terminalize_algo_after_flat(
            client, symbol=symbol, algo_id=stop_id, client_algo_id=stop_client_id
        )
        target_final_status = _terminalize_algo_after_flat(
            client, symbol=symbol, algo_id=target_id, client_algo_id=target_client_id
        )
    except TestnetProbeError as exc:
        signed_position = _position_quantity(client, symbol)
        recordable_position = total_entry_executed_quantity > 0 and (
            (plan.direction is Direction.LONG and signed_position > 0)
            or (plan.direction is Direction.SHORT and signed_position < 0)
        )
        if not recordable_position:
            raise
        execution_error_code = str(exc)
        quantity, actual_entry = _position_snapshot(client, symbol, plan.direction)
        closed = _flatten_position(client, symbol)
        if closed is None or closed.get("status") != "FILLED":
            raise TestnetProbeError("EXPERIMENT_FAIL_CLOSED_EXIT_NOT_FILLED") from exc
        if stop_id is not None:
            try:
                stop_final_status = _terminalize_algo_after_flat(
                    client,
                    symbol=symbol,
                    algo_id=stop_id,
                    client_algo_id=stop_client_id,
                )
            except TestnetProbeError:
                stop_final_status = "CLEANUP_DEFERRED"
        if target_id is not None:
            try:
                target_final_status = _terminalize_algo_after_flat(
                    client,
                    symbol=symbol,
                    algo_id=target_id,
                    client_algo_id=target_client_id,
                )
            except TestnetProbeError:
                target_final_status = "CLEANUP_DEFERRED"
        exit_reason = "EXECUTION_FAIL_CLOSED"
    finally:
        if entry is not None and _position_quantity(client, symbol) != 0:
            _flatten_position(client, symbol)
        for algo_id in (stop_id, target_id):
            if algo_id is None:
                continue
            try:
                if any(item.get("algoId") == algo_id for item in client.open_algo_orders(symbol)):
                    client.cancel_algo_order(algo_id=algo_id)
            except TestnetProbeError:
                pass

    trades = _load_run_trades(client, symbol, server_time_ms, sleep, expected_exit_side=close_side)
    realized = sum((Decimal(str(item.get("realizedPnl", "0"))) for item in trades), Decimal(0))
    commission = sum((Decimal(str(item.get("commission", "0"))) for item in trades), Decimal(0))
    net = realized - commission
    exit_price = _exit_trade_price(trades)
    position_notional = quantity * actual_entry
    return {
        "schema_version": "1.0.0",
        "record_type": "TESTNET_EXPERIMENT_RESULT",
        "environment": "testnet",
        "validation_status": "UNVALIDATED_TESTNET_EXPERIMENT",
        "strategy": plan.strategy_version,
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "completed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "symbol": symbol,
        "direction": plan.direction,
        "initial_leverage": leverage,
        "leverage_policy": "EXCHANGE_MAXIMUM",
        "margin_budget": format(margin_budget, "f"),
        "effective_margin_budget": format(effective_margin_budget, "f"),
        "actual_initial_margin": format(position_notional / Decimal(leverage), "f"),
        "position_notional": format(position_notional, "f"),
        "entry_execution_mode": entry_execution_mode,
        "execution_error_code": execution_error_code,
        "entry_reference_price": format(entry_reference_price, "f"),
        "directional_forecast_bps": format(directional_forecast_bps, "f"),
        "entry_forecast_model": entry_forecast_model,
        "entry_executed_quantity": format(total_entry_executed_quantity, "f"),
        "scale_in_count": scale_in_count,
        "maximum_net_loss_budget": format(maximum_net_loss, "f"),
        "minimum_estimated_net_target": format(minimum_estimated_net_target, "f"),
        "minimum_net_reward_risk_ratio": format(minimum_net_reward_risk_ratio, "f"),
        "risk_sizing_slippage_rate": format(risk_sizing_slippage_rate, "f"),
        "signal_quality_score": format(plan.signal_quality_score, "f"),
        "signal_confirmation_rounds": plan.signal_confirmation_rounds,
        "setup_type": plan.setup_type,
        "market_momentum_bps": format(plan.market_momentum_bps, "f"),
        "market_breadth_count": plan.market_breadth_count,
        "target_feasibility_rate_15m": format(plan.target_feasibility_rate_15m, "f"),
        "pa_alignment_count": plan.pa_alignment_count,
        "directional_trade_imbalance": format(plan.directional_trade_imbalance, "f"),
        "directional_book_imbalance": format(plan.directional_book_imbalance, "f"),
        "directional_microprice_bps": format(plan.directional_microprice_bps, "f"),
        "aggressive_notional": format(plan.aggressive_notional, "f"),
        "aggressive_notional_ratio": format(plan.aggressive_notional_ratio, "f"),
        "observed_spread_bps": format(plan.observed_spread_bps, "f"),
        "quantity": format(quantity, "f"),
        "entry_price": format(actual_entry, "f"),
        "stop_trigger": format(stop_trigger, "f"),
        "target_trigger": format(target_trigger, "f"),
        "protection_working_type": "CONTRACT_PRICE",
        "stop_final_status": stop_final_status,
        "target_final_status": target_final_status,
        "exit_reason": exit_reason,
        "exit_price": format(exit_price, "f"),
        "realized_pnl": format(realized, "f"),
        "commission_paid": format(commission, "f"),
        "net_pnl": format(net, "f"),
        "target_achieved": exit_reason == "TAKE_PROFIT",
        "account_trade_count": len(trades),
        "elapsed_time_exit_enabled": False,
        "production_endpoint_requests": 0,
    }


def resume_protected_structural_experiment(
    *,
    api_key_file: Path,
    api_secret_file: Path,
    repository_root: Path,
    recovery_event: Mapping[str, Any],
    position_control: PositionSignalControl | None = None,
    on_position_control: Callable[[dict[str, Any]], None] | None = None,
    stop_requested: Callable[[], bool] = lambda: False,
    maximum_net_loss: Decimal = Decimal("1.00"),
    minimum_estimated_net_target: Decimal = Decimal("0.10"),
    risk_sizing_slippage_rate: Decimal = Decimal("0.0012"),
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Resume ownership of a still-native-protected position after process loss.

    Recovery deliberately does not add to an inherited position. It keeps both
    exchange-native exits in force, accepts a newly confirmed reversal, records
    the eventual account result, and removes the inactive sibling Algo order.
    """
    symbol = str(recovery_event.get("symbol", ""))
    try:
        direction = Direction(str(recovery_event["direction"]))
        recorded_quantity = Decimal(str(recovery_event["quantity"]))
        recorded_entry = Decimal(str(recovery_event["entry_price"]))
    except (KeyError, ValueError, ArithmeticError) as exc:
        raise TestnetProbeError("EXPERIMENT_RECOVERY_EVIDENCE_INVALID") from exc
    if not symbol or recorded_quantity <= 0 or recorded_entry <= 0:
        raise TestnetProbeError("EXPERIMENT_RECOVERY_EVIDENCE_INVALID")

    started_at = _recovery_started_at(recovery_event)
    account_trade_start_ms = int((started_at.timestamp() - 30) * 1_000)
    stop_id = _optional_positive_int(recovery_event.get("stop_algo_id"))
    target_id = _optional_positive_int(recovery_event.get("target_algo_id"))
    stop_client_id = _optional_nonempty_string(recovery_event.get("stop_client_algo_id"))
    target_client_id = _optional_nonempty_string(recovery_event.get("target_client_algo_id"))
    if (stop_id is None) != (stop_client_id is None) or (target_id is None) != (
        target_client_id is None
    ):
        raise TestnetProbeError("EXPERIMENT_RECOVERY_ALGO_IDENTITY_INVALID")

    key = _credential(api_key_file, repository_root)
    secret = _credential(api_secret_file, repository_root)
    client = BinanceTestnetClient(key, secret)
    _, offset = client.synchronize_time()
    if abs(offset) > 1_000:
        raise TestnetProbeError("TESTNET_CLOCK_OFFSET_EXCESSIVE")
    if client.position_mode().get("dualSidePosition") is not False:
        raise TestnetProbeError("ACCOUNT_POSITION_MODE_NOT_ONE_WAY")

    expected_exit_side = "SELL" if direction is Direction.LONG else "BUY"
    exit_reason = "NATIVE_EXIT_UNCLASSIFIED"
    stop_final_status = "UNRESOLVED"
    target_final_status = "UNRESOLVED"
    replacement_requested = False
    try:
        signed_position = _position_quantity(client, symbol)
        if signed_position != 0:
            if (direction is Direction.LONG and signed_position <= 0) or (
                direction is Direction.SHORT and signed_position >= 0
            ):
                raise TestnetProbeError("EXPERIMENT_RECOVERY_DIRECTION_MISMATCH")
            if stop_id is None or target_id is None:
                raise TestnetProbeError("EXPERIMENT_RECOVERY_PROTECTION_INCOMPLETE")
            if client.open_orders(symbol):
                raise TestnetProbeError("EXPERIMENT_RECOVERY_STANDARD_ORDER_PRESENT")
            open_algo_ids = {
                _optional_positive_int(item.get("algoId"))
                for item in client.open_algo_orders(symbol)
            }
            if stop_id not in open_algo_ids or target_id not in open_algo_ids:
                raise TestnetProbeError("EXPERIMENT_RECOVERY_PROTECTION_INCOMPLETE")

            while _position_quantity(client, symbol) != 0 and not stop_requested():
                latest_plan = None if position_control is None else position_control.take_latest()
                if latest_plan is None:
                    sleep(1)
                    continue
                if latest_plan.direction is direction:
                    if on_position_control is not None:
                        on_position_control(
                            {
                                "record_type": "TESTNET_RECOVERED_POSITION_SCALE_SKIPPED",
                                "environment": "testnet",
                                "validation_status": "UNVALIDATED_TESTNET_EXPERIMENT",
                                "strategy": latest_plan.strategy_version,
                                "symbol": symbol,
                                "direction": direction,
                                "reason_code": "RESTART_RECOVERY_DOES_NOT_SCALE_INHERITED_POSITION",
                                "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                                "production_endpoint_requests": 0,
                            }
                        )
                    continue
                replacement_plan, control_record_type, control_exit_reason = (
                    _opposing_signal_action(latest_plan)
                )
                if position_control is not None:
                    position_control.replacement_plan = replacement_plan
                if on_position_control is not None:
                    on_position_control(
                        {
                            "record_type": control_record_type,
                            "environment": "testnet",
                            "validation_status": "UNVALIDATED_TESTNET_EXPERIMENT",
                            "strategy": latest_plan.strategy_version,
                            "symbol": symbol,
                            "old_direction": direction,
                            "new_direction": latest_plan.direction,
                            "exit_only": latest_plan.exit_only,
                            "setup_type": latest_plan.setup_type,
                            "signal_quality_score": format(latest_plan.signal_quality_score, "f"),
                            "requested_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                            "production_endpoint_requests": 0,
                        }
                    )
                closed = _flatten_position(client, symbol)
                if closed is None or closed.get("status") != "FILLED":
                    raise TestnetProbeError("EXPERIMENT_SIGNAL_REVERSAL_NOT_FILLED")
                stop_final_status = _terminalize_algo_after_flat(
                    client,
                    symbol=symbol,
                    algo_id=stop_id,
                    client_algo_id=str(stop_client_id),
                )
                target_final_status = _terminalize_algo_after_flat(
                    client,
                    symbol=symbol,
                    algo_id=target_id,
                    client_algo_id=str(target_client_id),
                )
                replacement_requested = True
                exit_reason = control_exit_reason
                break

            if not replacement_requested:
                if _position_quantity(client, symbol) != 0:
                    closed = _flatten_position(client, symbol)
                    if closed is None or closed.get("status") != "FILLED":
                        raise TestnetProbeError("EXPERIMENT_OPERATOR_EXIT_NOT_FILLED")
                    exit_reason = "OPERATOR_SERVICE_STOP"
                else:
                    exit_reason, stop_final_status, target_final_status = _classify_native_exit(
                        client,
                        symbol=symbol,
                        stop_id=stop_id,
                        stop_client_id=str(stop_client_id),
                        target_id=target_id,
                        target_client_id=str(target_client_id),
                        sleep=sleep,
                    )
        else:
            stop_status = (
                "UNKNOWN"
                if stop_id is None or stop_client_id is None
                else _algo_status(client, symbol, stop_id, stop_client_id)
            )
            target_status = (
                "UNKNOWN"
                if target_id is None or target_client_id is None
                else _algo_status(client, symbol, target_id, target_client_id)
            )
            if target_status in {"TRIGGERED", "FINISHED"} or (
                stop_status in {"NEW", "TRIGGERING"}
                and (target_status == "REMOVED" or target_id is None)
            ):
                exit_reason = "TAKE_PROFIT"
            elif stop_status in {"TRIGGERED", "FINISHED"} or (
                target_status in {"NEW", "TRIGGERING"}
                and (stop_status == "REMOVED" or stop_id is None)
            ):
                exit_reason = "STOP_LOSS"
            stop_final_status = stop_status
            target_final_status = target_status

        if stop_id is not None and stop_client_id is not None:
            stop_final_status = _terminalize_algo_after_flat(
                client,
                symbol=symbol,
                algo_id=stop_id,
                client_algo_id=stop_client_id,
            )
        if target_id is not None and target_client_id is not None:
            target_final_status = _terminalize_algo_after_flat(
                client,
                symbol=symbol,
                algo_id=target_id,
                client_algo_id=target_client_id,
            )
    finally:
        if _position_quantity(client, symbol) != 0:
            _flatten_position(client, symbol)
        for algo_id in (stop_id, target_id):
            if algo_id is None:
                continue
            try:
                if any(item.get("algoId") == algo_id for item in client.open_algo_orders(symbol)):
                    client.cancel_algo_order(algo_id=algo_id)
            except TestnetProbeError:
                pass

    trades = _load_run_trades(
        client,
        symbol,
        account_trade_start_ms,
        sleep,
        expected_exit_side=expected_exit_side,
    )
    realized = sum((Decimal(str(item.get("realizedPnl", "0"))) for item in trades), Decimal(0))
    commission = sum((Decimal(str(item.get("commission", "0"))) for item in trades), Decimal(0))
    net = realized - commission
    quantity = Decimal(str(recovery_event.get("quantity", recorded_quantity)))
    entry_price = Decimal(str(recovery_event.get("entry_price", recorded_entry)))
    leverage = int(str(recovery_event.get("initial_leverage", 1)))
    return {
        "schema_version": "1.0.0",
        "record_type": "TESTNET_EXPERIMENT_RESULT",
        "environment": "testnet",
        "validation_status": "UNVALIDATED_TESTNET_EXPERIMENT",
        "strategy": str(recovery_event.get("strategy", "UNKNOWN_TESTNET_STRATEGY")),
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "completed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "symbol": symbol,
        "direction": direction,
        "initial_leverage": leverage,
        "leverage_policy": "EXCHANGE_MAXIMUM",
        "margin_budget": str(recovery_event.get("effective_margin_budget", "0")),
        "effective_margin_budget": str(recovery_event.get("effective_margin_budget", "0")),
        "actual_initial_margin": str(
            recovery_event.get(
                "actual_initial_margin",
                quantity * entry_price / Decimal(max(leverage, 1)),
            )
        ),
        "position_notional": str(recovery_event.get("position_notional", quantity * entry_price)),
        "entry_execution_mode": "RECOVERED_NATIVE_PROTECTED_POSITION",
        "execution_error_code": None,
        "entry_reference_price": str(recovery_event.get("entry_reference_price", entry_price)),
        "directional_forecast_bps": str(recovery_event.get("directional_forecast_bps", "0")),
        "entry_forecast_model": str(recovery_event.get("entry_forecast_model", "RESTART_RECOVERY")),
        "entry_executed_quantity": format(quantity, "f"),
        "scale_in_count": int(str(recovery_event.get("scale_in_count", 0))),
        "maximum_net_loss_budget": format(maximum_net_loss, "f"),
        "minimum_estimated_net_target": format(minimum_estimated_net_target, "f"),
        "risk_sizing_slippage_rate": format(risk_sizing_slippage_rate, "f"),
        "signal_quality_score": str(recovery_event.get("signal_quality_score", "0")),
        "signal_confirmation_rounds": int(str(recovery_event.get("signal_confirmation_rounds", 1))),
        "setup_type": str(recovery_event.get("setup_type", "RESTART_RECOVERY")),
        "quantity": format(quantity, "f"),
        "entry_price": format(entry_price, "f"),
        "stop_trigger": str(recovery_event.get("stop_trigger", "0")),
        "target_trigger": str(recovery_event.get("target_trigger", "0")),
        "protection_working_type": "CONTRACT_PRICE",
        "stop_final_status": stop_final_status,
        "target_final_status": target_final_status,
        "exit_reason": exit_reason,
        "exit_price": format(_exit_trade_price(trades), "f"),
        "realized_pnl": format(realized, "f"),
        "commission_paid": format(commission, "f"),
        "net_pnl": format(net, "f"),
        "target_achieved": exit_reason == "TAKE_PROFIT",
        "account_trade_count": len(trades),
        "recovered_after_restart": True,
        "elapsed_time_exit_enabled": False,
        "production_endpoint_requests": 0,
    }


def _recovery_started_at(event: Mapping[str, Any]) -> datetime:
    raw = event.get("position_started_at") or event.get("protected_at")
    if not isinstance(raw, str):
        raise TestnetProbeError("EXPERIMENT_RECOVERY_START_TIME_MISSING")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TestnetProbeError("EXPERIMENT_RECOVERY_START_TIME_INVALID") from exc
    if parsed.tzinfo is None:
        raise TestnetProbeError("EXPERIMENT_RECOVERY_START_TIME_INVALID")
    return parsed.astimezone(UTC)


def _optional_positive_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(str(value))
    except ValueError as exc:
        raise TestnetProbeError("EXPERIMENT_RECOVERY_ALGO_IDENTITY_INVALID") from exc
    if parsed <= 0:
        raise TestnetProbeError("EXPERIMENT_RECOVERY_ALGO_IDENTITY_INVALID")
    return parsed


def _optional_nonempty_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        raise TestnetProbeError("EXPERIMENT_RECOVERY_ALGO_IDENTITY_INVALID")
    return value


def exchange_maximum_initial_leverage(brackets: list[dict[str, Any]], symbol: str) -> int:
    """Read the maximum current initial leverage for one Testnet symbol."""
    symbol_document = next((item for item in brackets if item.get("symbol") == symbol), None)
    if not isinstance(symbol_document, dict):
        raise TestnetProbeError("LEVERAGE_BRACKET_SYMBOL_MISSING")
    rows = symbol_document.get("brackets")
    if not isinstance(rows, list) or not rows:
        raise TestnetProbeError("LEVERAGE_BRACKET_ROWS_MISSING")
    try:
        leverage = max(int(item["initialLeverage"]) for item in rows if isinstance(item, dict))
    except (KeyError, TypeError, ValueError) as exc:
        raise TestnetProbeError("LEVERAGE_BRACKET_INVALID_RESPONSE") from exc
    if not 1 <= leverage <= 125:
        raise TestnetProbeError("LEVERAGE_BRACKET_INVALID_RESPONSE")
    return leverage


def _place_protection(
    client: BinanceTestnetClient,
    *,
    symbol: str,
    side: str,
    client_algo_id: str,
    order_type: str,
    trigger_price: Decimal,
) -> dict[str, Any]:
    return client.place_algo_order(
        {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": side,
            "positionSide": "BOTH",
            "type": order_type,
            "triggerPrice": format(trigger_price, "f"),
            # The Testnet scalp signal and executable book both use contract
            # prices. MARK_PRICE can cross a tiny target while the Testnet book
            # remains near entry, producing a nominal target with a net loss.
            "workingType": "CONTRACT_PRICE",
            "closePosition": "true",
            "priceProtect": "false",
            "clientAlgoId": client_algo_id,
            "newOrderRespType": "RESULT",
        }
    )


def _confirmed_algo_id(document: dict[str, Any], client_id: str, role: str) -> int:
    if document.get("clientAlgoId") != client_id or document.get("algoStatus") != "NEW":
        raise TestnetProbeError(f"EXPERIMENT_{role}_NOT_CONFIRMED")
    algo_id = document.get("algoId")
    if not isinstance(algo_id, int):
        raise TestnetProbeError(f"EXPERIMENT_{role}_ID_MISSING")
    return algo_id


def _require_algo_new(
    client: BinanceTestnetClient,
    symbol: str,
    algo_id: int,
    client_id: str,
    role: str,
) -> None:
    document = _query_algo_consistent(
        client, symbol=symbol, algo_id=algo_id, client_algo_id=client_id
    )
    if document.get("algoStatus") != "NEW":
        raise TestnetProbeError(f"EXPERIMENT_{role}_QUERY_NOT_NEW")


def _algo_status(client: BinanceTestnetClient, symbol: str, algo_id: int, client_id: str) -> str:
    try:
        return str(
            _query_algo_consistent(
                client, symbol=symbol, algo_id=algo_id, client_algo_id=client_id
            ).get("algoStatus")
        )
    except TestnetProbeError:
        return "REMOVED"


def _classify_native_exit(
    client: BinanceTestnetClient,
    *,
    symbol: str,
    stop_id: int,
    stop_client_id: str,
    target_id: int,
    target_client_id: str,
    sleep: Callable[[float], None],
) -> tuple[str, str, str]:
    """Wait briefly for the asynchronous Algo status to identify the exit."""
    stop_status = "UNRESOLVED"
    target_status = "UNRESOLVED"
    for attempt in range(10):
        stop_status = _algo_status(client, symbol, stop_id, stop_client_id)
        target_status = _algo_status(client, symbol, target_id, target_client_id)
        if target_status in {"TRIGGERED", "FINISHED"}:
            return "TAKE_PROFIT", stop_status, target_status
        if stop_status in {"TRIGGERED", "FINISHED"}:
            return "STOP_LOSS", stop_status, target_status
        if attempt < 9:
            sleep(0.2)
    return "NATIVE_EXIT_UNCLASSIFIED", stop_status, target_status


def _protected_position_event(
    *,
    plan: TestnetExperimentalPlan,
    leverage: int,
    quantity: Decimal,
    actual_entry: Decimal,
    stop_trigger: Decimal,
    target_trigger: Decimal,
    effective_margin_budget: Decimal,
    taker_fee_rate: Decimal,
    entry_execution_mode: str,
    entry_reference_price: Decimal,
    directional_forecast_bps: Decimal,
    entry_forecast_model: str,
    position_started_at: datetime | None = None,
    stop_algo_id: int | None = None,
    stop_client_algo_id: str | None = None,
    target_algo_id: int | None = None,
    target_client_algo_id: str | None = None,
) -> dict[str, Any]:
    target_gross, round_trip_fee, target_net, stop_net_loss = estimated_position_outcomes(
        quantity=quantity,
        actual_entry=actual_entry,
        stop_trigger=stop_trigger,
        target_trigger=target_trigger,
        taker_fee_rate=taker_fee_rate,
    )
    notional = quantity * actual_entry
    adverse_slippage = notional * Decimal("0.0002")
    return {
        "record_type": "TESTNET_POSITION_PROTECTED",
        "environment": "testnet",
        "validation_status": "UNVALIDATED_TESTNET_EXPERIMENT",
        "strategy": plan.strategy_version,
        "symbol": plan.symbol,
        "direction": plan.direction,
        "initial_leverage": leverage,
        "leverage_policy": "EXCHANGE_MAXIMUM",
        "quantity": format(quantity, "f"),
        "entry_price": format(actual_entry, "f"),
        "position_notional": format(notional, "f"),
        "actual_initial_margin": format(notional / Decimal(leverage), "f"),
        "entry_execution_mode": entry_execution_mode,
        "entry_reference_price": format(entry_reference_price, "f"),
        "directional_forecast_bps": format(directional_forecast_bps, "f"),
        "entry_forecast_model": entry_forecast_model,
        "effective_margin_budget": format(effective_margin_budget, "f"),
        "signal_quality_score": format(plan.signal_quality_score, "f"),
        "signal_confirmation_rounds": plan.signal_confirmation_rounds,
        "setup_type": plan.setup_type,
        "market_momentum_bps": format(plan.market_momentum_bps, "f"),
        "market_breadth_count": plan.market_breadth_count,
        "target_feasibility_rate_15m": format(plan.target_feasibility_rate_15m, "f"),
        "pa_alignment_count": plan.pa_alignment_count,
        "directional_trade_imbalance": format(plan.directional_trade_imbalance, "f"),
        "directional_book_imbalance": format(plan.directional_book_imbalance, "f"),
        "directional_microprice_bps": format(plan.directional_microprice_bps, "f"),
        "aggressive_notional": format(plan.aggressive_notional, "f"),
        "aggressive_notional_ratio": format(plan.aggressive_notional_ratio, "f"),
        "observed_spread_bps": format(plan.observed_spread_bps, "f"),
        "stop_trigger": format(stop_trigger, "f"),
        "target_trigger": format(target_trigger, "f"),
        "estimated_target_gross_pnl": format(target_gross, "f"),
        "estimated_round_trip_fee": format(round_trip_fee, "f"),
        "estimated_adverse_slippage": format(adverse_slippage, "f"),
        "estimated_target_net_pnl": format(target_net, "f"),
        "estimated_stop_net_loss": format(stop_net_loss, "f"),
        "protection_working_type": "CONTRACT_PRICE",
        "position_started_at": (
            None
            if position_started_at is None
            else position_started_at.isoformat().replace("+00:00", "Z")
        ),
        "stop_algo_id": stop_algo_id,
        "stop_client_algo_id": stop_client_algo_id,
        "target_algo_id": target_algo_id,
        "target_client_algo_id": target_client_algo_id,
        "protected_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "production_endpoint_requests": 0,
    }


def estimated_position_outcomes(
    *,
    quantity: Decimal,
    actual_entry: Decimal,
    stop_trigger: Decimal,
    target_trigger: Decimal,
    taker_fee_rate: Decimal,
    adverse_slippage_rate: Decimal = Decimal("0.0002"),
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Estimate target gross, round-trip fee, target net and stop net loss."""
    if (
        min(quantity, actual_entry, stop_trigger, target_trigger) <= 0
        or taker_fee_rate < 0
        or adverse_slippage_rate < 0
    ):
        raise TestnetProbeError("EXPERIMENT_OUTCOME_ESTIMATE_INVALID")
    notional = quantity * actual_entry
    target_gross = quantity * abs(target_trigger - actual_entry)
    stop_gross = quantity * abs(actual_entry - stop_trigger)
    round_trip_fee = notional * taker_fee_rate * Decimal(2)
    adverse_slippage = notional * adverse_slippage_rate
    return (
        target_gross,
        round_trip_fee,
        target_gross - round_trip_fee - adverse_slippage,
        stop_gross + round_trip_fee + adverse_slippage,
    )


def _filled_average_price(document: Mapping[str, Any]) -> Decimal:
    average = Decimal(str(document.get("avgPrice", "0")))
    if average > 0:
        return average
    quantity = Decimal(str(document.get("executedQty", "0")))
    quote = Decimal(str(document.get("cumQuote", "0")))
    if quantity <= 0 or quote <= 0:
        raise TestnetProbeError("EXPERIMENT_ENTRY_PRICE_MISSING")
    return quote / quantity


def _resolve_entry_price(
    client: BinanceTestnetClient,
    symbol: str,
    client_order_id: str,
    entry: dict[str, Any],
    direction: Direction,
) -> Decimal:
    for document in (entry, client.query_order(symbol, client_order_id)):
        try:
            return _filled_average_price(document)
        except TestnetProbeError:
            pass
    positions = client.position_risk(symbol)
    prices = {
        Decimal(str(item.get("entryPrice", "0")))
        for item in positions
        if (direction is Direction.LONG and Decimal(str(item.get("positionAmt", "0"))) > 0)
        or (direction is Direction.SHORT and Decimal(str(item.get("positionAmt", "0"))) < 0)
    }
    if len(prices) != 1 or next(iter(prices)) <= 0:
        raise TestnetProbeError("EXPERIMENT_ENTRY_PRICE_MISSING")
    return next(iter(prices))


def _load_run_trades(
    client: BinanceTestnetClient,
    symbol: str,
    start_time_ms: int,
    sleep: Callable[[float], None],
    *,
    expected_exit_side: str,
) -> list[dict[str, Any]]:
    for _ in range(10):
        trades = client.account_trades(symbol, start_time_ms=start_time_ms)
        if len(trades) >= 2 and any(str(item.get("side")) == expected_exit_side for item in trades):
            return trades
        sleep(0.2)
    raise TestnetProbeError("EXPERIMENT_TRADES_INCOMPLETE")


def _exit_trade_price(trades: list[dict[str, Any]]) -> Decimal:
    try:
        exit_trade = max(
            trades,
            key=lambda item: (int(item.get("time", 0)), int(item.get("id", 0))),
        )
        price = Decimal(str(exit_trade["price"]))
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise TestnetProbeError("EXPERIMENT_EXIT_PRICE_MISSING") from exc
    if price <= 0:
        raise TestnetProbeError("EXPERIMENT_EXIT_PRICE_MISSING")
    return price
