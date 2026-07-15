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


@dataclass(frozen=True, slots=True)
class _ScaleInPreparation:
    quantity: Decimal
    predictive_price: Decimal
    predicted_pullback_bps: Decimal
    directional_forecast_bps: Decimal
    predictive_entry_model: str
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
        predictive_price,
        predicted_pullback_bps,
        directional_forecast_bps,
        predictive_entry_model,
    ) = predictive_limit_price(
        plan.direction,
        bid_price=bid_price,
        ask_price=ask_price,
        tick_size=tick_size,
        predictive_average_20m=plan.predictive_average_20m,
    )
    loss_sign = Decimal(1) if plan.direction is Direction.LONG else Decimal(-1)
    cost_rate = taker_fee_rate * Decimal(2) + risk_sizing_slippage_rate
    existing_unit_risk = max(
        Decimal(0), loss_sign * (current_entry - current_stop)
    ) + current_entry * cost_rate
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
        reference_price=predictive_price,
        margin_budget=effective_margin_budget,
        leverage=leverage,
    )
    added_unit_risk = max(
        Decimal(0), loss_sign * (predictive_price - current_stop)
    ) + predictive_price * cost_rate
    if added_unit_risk <= 0:
        raise TestnetProbeError("EXPERIMENT_SCALE_RISK_ESTIMATE_INVALID")
    risk_limited_quantity = _decimal_step(
        remaining_risk / added_unit_risk, step_size, ROUND_FLOOR
    )
    quantity = min(desired_quantity, risk_limited_quantity)
    required_quantity = minimum_quantity
    if minimum_notional > 0:
        required_quantity = max(
            required_quantity,
            _decimal_step(
                minimum_notional / predictive_price, step_size, ROUND_CEILING
            ),
        )
    if quantity < required_quantity or quantity <= 0:
        raise TestnetProbeError("EXPERIMENT_SCALE_REMAINING_RISK_BELOW_EXCHANGE_MINIMUM")
    combined_quantity = current_quantity + quantity
    combined_entry = (
        current_quantity * current_entry + quantity * predictive_price
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
    return _ScaleInPreparation(
        quantity=quantity,
        predictive_price=predictive_price,
        predicted_pullback_bps=predicted_pullback_bps,
        directional_forecast_bps=directional_forecast_bps,
        predictive_entry_model=predictive_entry_model,
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


def predictive_limit_price(
    direction: Direction,
    *,
    bid_price: Decimal,
    ask_price: Decimal,
    tick_size: Decimal,
    predictive_average_20m: Decimal,
    minimum_forecast_edge_bps: Decimal = Decimal("1"),
    minimum_entry_distance_bps: Decimal = Decimal("0"),
    maximum_entry_distance_bps: Decimal = Decimal("0"),
) -> tuple[Decimal, Decimal, Decimal, str]:
    """Use an aligned forecast as a gate and join the passive best quote."""
    if (
        min(
            bid_price,
            ask_price,
            tick_size,
            predictive_average_20m,
            minimum_forecast_edge_bps,
        )
        <= 0
        or bid_price >= ask_price
        or minimum_entry_distance_bps < 0
        or minimum_entry_distance_bps > maximum_entry_distance_bps
    ):
        raise TestnetProbeError("EXPERIMENT_MAKER_PRICE_INPUT_INVALID")
    mid_price = (bid_price + ask_price) / Decimal(2)
    sign = Decimal(1) if direction is Direction.LONG else Decimal(-1)
    directional_forecast_bps = (
        sign * (predictive_average_20m / mid_price - Decimal(1)) * Decimal(10_000)
    )
    if abs(directional_forecast_bps) < minimum_forecast_edge_bps:
        raise TestnetProbeError("EXPERIMENT_PREDICTIVE_EDGE_INSUFFICIENT")
    planned_distance_bps = minimum_entry_distance_bps
    distance_rate = planned_distance_bps / Decimal(10_000)
    entry_model = (
        "FORECAST_ALIGNED_BEST_QUOTE"
        if directional_forecast_bps > 0
        else "FORECAST_CONFLICT_SIGNAL_PRIORITY_BEST_QUOTE"
    )
    if direction is Direction.LONG:
        price = _decimal_step(
            bid_price * (Decimal(1) - distance_rate), tick_size, ROUND_FLOOR
        )
        pullback_bps = (bid_price - price) / bid_price * Decimal(10_000)
        return price, pullback_bps, directional_forecast_bps, entry_model
    if direction is Direction.SHORT:
        price = _decimal_step(
            ask_price * (Decimal(1) + distance_rate), tick_size, ROUND_CEILING
        )
        pullback_bps = (price - ask_price) / ask_price * Decimal(10_000)
        return price, pullback_bps, directional_forecast_bps, entry_model
    raise TestnetProbeError("EXPERIMENT_DIRECTION_INVALID")


def _predictive_limit_entry(
    client: BinanceTestnetClient,
    *,
    symbol: str,
    direction: Direction,
    side: str,
    quantity: Decimal,
    price: Decimal,
    client_order_id: str,
    sleep: Callable[[float], None],
    polling_attempts: int = 120,
    position_guard: Callable[[], bool] | None = None,
    maximum_wait_seconds: float = 30.0,
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[dict[str, Any] | None, Decimal, str]:
    """Wait up to 30 seconds for the predicted average, then cancel without chasing."""
    try:
        document = client.place_order(
            {
                "symbol": symbol,
                "side": side,
                "positionSide": "BOTH",
                "type": "LIMIT",
                "timeInForce": "GTX",
                "quantity": format(quantity, "f"),
                "price": format(price, "f"),
                "newOrderRespType": "RESULT",
                "newClientOrderId": client_order_id,
            }
        )
    except TestnetProbeError:
        return None, Decimal(0), "PREDICTIVE_GTX_REJECTED"
    latest = document
    deadline = monotonic() + maximum_wait_seconds
    for _ in range(polling_attempts):
        if latest.get("status") in {
            "FILLED",
            "PARTIALLY_FILLED",
            "CANCELED",
            "EXPIRED",
            "REJECTED",
        }:
            break
        if position_guard is not None and not position_guard():
            client.cancel_order(symbol, client_order_id)
            latest = client.query_order(symbol, client_order_id)
            return latest, Decimal(0), "PARENT_POSITION_CLOSED"
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        sleep(min(0.25, remaining))
        latest = client.query_order(symbol, client_order_id)
    if latest.get("status") in {"NEW", "PARTIALLY_FILLED"}:
        client.cancel_order(symbol, client_order_id)
        latest = client.query_order(symbol, client_order_id)
    executed = Decimal(str(latest.get("executedQty", "0")))
    if executed >= quantity and latest.get("status") == "FILLED":
        return latest, executed, "PREDICTIVE_GTX_FILLED"
    if executed > 0:
        return latest, executed, "PREDICTIVE_GTX_PARTIALLY_FILLED"
    return latest, Decimal(0), "PREDICTIVE_GTX_NOT_FILLED"


def run_structural_experiment(
    *,
    api_key_file: Path,
    api_secret_file: Path,
    repository_root: Path,
    plan: TestnetExperimentalPlan,
    margin_budget: Decimal = Decimal("1"),
    maximum_net_loss: Decimal = Decimal("1.00"),
    minimum_estimated_net_target: Decimal = Decimal("0.10"),
    risk_sizing_slippage_rate: Decimal = Decimal("0.0012"),
    on_entry_attempt: Callable[[dict[str, Any]], None] | None = None,
    on_position_protected: Callable[[dict[str, Any]], None] | None = None,
    on_position_control: Callable[[dict[str, Any]], None] | None = None,
    position_control: PositionSignalControl | None = None,
    stop_requested: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Open one Testnet position and wait only for strategy protection or operator stop."""
    if not Decimal(0) <= minimum_estimated_net_target <= Decimal(1) or not Decimal(
        0
    ) <= risk_sizing_slippage_rate <= Decimal("0.01"):
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
        predictive_price,
        predicted_pullback_bps,
        directional_forecast_bps,
        predictive_entry_model,
    ) = predictive_limit_price(
        plan.direction,
        bid_price=bid_price,
        ask_price=ask_price,
        tick_size=tick_size,
        predictive_average_20m=plan.predictive_average_20m,
    )
    quantity = plan_market_quantity(
        exchange_info,
        symbol=symbol,
        reference_price=predictive_price,
        margin_budget=effective_margin_budget,
        leverage=leverage,
    )
    pretrade_stop, pretrade_target = quantize_protection(
        plan, actual_entry=predictive_price, tick_size=tick_size
    )
    _, _, estimated_net_target, _ = estimated_position_outcomes(
        quantity=quantity,
        actual_entry=predictive_price,
        stop_trigger=pretrade_stop,
        target_trigger=pretrade_target,
        taker_fee_rate=taker_fee_rate,
    )
    if estimated_net_target < minimum_estimated_net_target:
        raise TestnetProbeError("EXPERIMENT_ESTIMATED_NET_TARGET_INSUFFICIENT")
    entry_side = "BUY" if plan.direction is Direction.LONG else "SELL"
    close_side = "SELL" if plan.direction is Direction.LONG else "BUY"
    maker_entry_id = f"aq-t-exp-m-{secrets.token_hex(5)}"
    entry_id = maker_entry_id
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
    maker_executed_quantity = Decimal(0)
    scale_in_count = 0
    current_plan = plan
    try:
        if on_entry_attempt is not None:
            on_entry_attempt(
                {
                    "record_type": "TESTNET_PREDICTIVE_LIMIT_SUBMITTED",
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
                    "predictive_limit_price": format(predictive_price, "f"),
                    "predicted_pullback_bps": format(predicted_pullback_bps, "f"),
                    "directional_forecast_bps": format(directional_forecast_bps, "f"),
                    "predictive_entry_model": predictive_entry_model,
                    "attempted_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "production_endpoint_requests": 0,
                }
            )
        entry, maker_executed_quantity, entry_execution_mode = _predictive_limit_entry(
            client,
            symbol=symbol,
            direction=plan.direction,
            side=entry_side,
            quantity=quantity,
            price=predictive_price,
            client_order_id=maker_entry_id,
            sleep=sleep,
        )
        if on_entry_attempt is not None:
            on_entry_attempt(
                {
                    "record_type": "TESTNET_PREDICTIVE_LIMIT_RESULT",
                    "environment": "testnet",
                    "validation_status": "UNVALIDATED_TESTNET_EXPERIMENT",
                    "strategy": plan.strategy_version,
                    "symbol": symbol,
                    "direction": plan.direction,
                    "client_order_id": maker_entry_id,
                    "predictive_limit_price": format(predictive_price, "f"),
                    "execution_mode": entry_execution_mode,
                    "order_status": None if entry is None else entry.get("status"),
                    "executed_quantity": format(maker_executed_quantity, "f"),
                    "completed_at": datetime.now(UTC)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "production_endpoint_requests": 0,
                }
            )
        if maker_executed_quantity > 0:
            quantity = maker_executed_quantity
        else:
            raise TestnetProbeError("EXPERIMENT_PREDICTIVE_LIMIT_NOT_FILLED")
        if (
            entry is None
            or entry.get("clientOrderId") != entry_id
            or Decimal(str(entry.get("executedQty", "0"))) <= 0
        ):
            raise TestnetProbeError("EXPERIMENT_ENTRY_NOT_FILLED")
        position = _position_quantity(client, symbol)
        if (plan.direction is Direction.LONG and position <= 0) or (
            plan.direction is Direction.SHORT and position >= 0
        ):
            raise TestnetProbeError("EXPERIMENT_POSITION_DIRECTION_MISMATCH")
        actual_entry = _resolve_entry_price(client, symbol, entry_id, entry, plan.direction)
        stop_trigger, target_trigger = quantize_protection(
            plan, actual_entry=actual_entry, tick_size=tick_size
        )
        _, _, _, actual_stop_net_loss = estimated_position_outcomes(
            quantity=quantity,
            actual_entry=actual_entry,
            stop_trigger=stop_trigger,
            target_trigger=target_trigger,
            taker_fee_rate=taker_fee_rate,
        )
        if actual_stop_net_loss > maximum_net_loss:
            raise TestnetProbeError("EXPERIMENT_ACTUAL_FILL_EXCEEDS_LOSS_BUDGET")
        _, _, actual_target_net_pnl, _ = estimated_position_outcomes(
            quantity=quantity,
            actual_entry=actual_entry,
            stop_trigger=stop_trigger,
            target_trigger=target_trigger,
            taker_fee_rate=taker_fee_rate,
        )
        if actual_target_net_pnl < minimum_estimated_net_target:
            raise TestnetProbeError("EXPERIMENT_ACTUAL_ENTRY_NET_TARGET_INSUFFICIENT")
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
            predictive_limit_price=predictive_price,
            predicted_pullback_bps=predicted_pullback_bps,
            directional_forecast_bps=directional_forecast_bps,
            predictive_entry_model=predictive_entry_model,
        )
        if on_position_protected is not None:
            on_position_protected(protected_position)

        while _position_quantity(client, symbol) != 0 and not stop_requested():
            latest_plan = None if position_control is None else position_control.take_latest()
            if latest_plan is None:
                sleep(1)
                continue
            if latest_plan.direction is not current_plan.direction:
                if position_control is not None:
                    position_control.replacement_plan = latest_plan
                if on_position_control is not None:
                    on_position_control(
                        {
                            "record_type": "TESTNET_POSITION_REVERSAL_REQUESTED",
                            "environment": "testnet",
                            "validation_status": "UNVALIDATED_TESTNET_EXPERIMENT",
                            "strategy": latest_plan.strategy_version,
                            "symbol": symbol,
                            "old_direction": current_plan.direction,
                            "new_direction": latest_plan.direction,
                            "signal_quality_score": format(
                                latest_plan.signal_quality_score, "f"
                            ),
                            "requested_at": datetime.now(UTC)
                            .isoformat()
                            .replace("+00:00", "Z"),
                            "production_endpoint_requests": 0,
                        }
                    )
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
                closed = _flatten_position(client, symbol)
                if closed is None or closed.get("status") != "FILLED":
                    raise TestnetProbeError("EXPERIMENT_SIGNAL_REVERSAL_NOT_FILLED")
                exit_reason = "SIGNAL_REVERSAL"
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
            scale_order_id = f"aq-t-scale-m-{secrets.token_hex(5)}"
            scale_side = "BUY" if latest_plan.direction is Direction.LONG else "SELL"
            if on_entry_attempt is not None:
                on_entry_attempt(
                    {
                        "record_type": "TESTNET_PREDICTIVE_SCALE_LIMIT_SUBMITTED",
                        "environment": "testnet",
                        "validation_status": "UNVALIDATED_TESTNET_EXPERIMENT",
                        "strategy": latest_plan.strategy_version,
                        "symbol": symbol,
                        "direction": latest_plan.direction,
                        "quantity": format(scale.quantity, "f"),
                        "predictive_limit_price": format(scale.predictive_price, "f"),
                        "predicted_pullback_bps": format(
                            scale.predicted_pullback_bps, "f"
                        ),
                        "directional_forecast_bps": format(
                            scale.directional_forecast_bps, "f"
                        ),
                        "predictive_entry_model": scale.predictive_entry_model,
                        "attempted_at": datetime.now(UTC)
                        .isoformat()
                        .replace("+00:00", "Z"),
                        "production_endpoint_requests": 0,
                    }
                )
            scale_direction = latest_plan.direction

            def position_still_open(direction: Direction = scale_direction) -> bool:
                position = _position_quantity(client, symbol)
                return position > 0 if direction is Direction.LONG else position < 0

            scale_document, added_quantity, scale_execution_mode = _predictive_limit_entry(
                client,
                symbol=symbol,
                direction=latest_plan.direction,
                side=scale_side,
                quantity=scale.quantity,
                price=scale.predictive_price,
                client_order_id=scale_order_id,
                sleep=sleep,
                position_guard=position_still_open,
            )
            if on_entry_attempt is not None:
                on_entry_attempt(
                    {
                        "record_type": "TESTNET_PREDICTIVE_SCALE_LIMIT_RESULT",
                        "environment": "testnet",
                        "validation_status": "UNVALIDATED_TESTNET_EXPERIMENT",
                        "strategy": latest_plan.strategy_version,
                        "symbol": symbol,
                        "direction": latest_plan.direction,
                        "client_order_id": scale_order_id,
                        "predictive_limit_price": format(scale.predictive_price, "f"),
                        "execution_mode": scale_execution_mode,
                        "order_status": (
                            None if scale_document is None else scale_document.get("status")
                        ),
                        "executed_quantity": format(added_quantity, "f"),
                        "completed_at": datetime.now(UTC)
                        .isoformat()
                        .replace("+00:00", "Z"),
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
            maker_executed_quantity += added_quantity
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
                    predictive_limit_price=scale.predictive_price,
                    predicted_pullback_bps=scale.predicted_pullback_bps,
                    directional_forecast_bps=scale.directional_forecast_bps,
                    predictive_entry_model=scale.predictive_entry_model,
                )
                scaled_event["record_type"] = "TESTNET_POSITION_SCALED_AND_REPROTECTED"
                scaled_event["added_quantity"] = format(added_quantity, "f")
                scaled_event["scale_in_count"] = scale_in_count
                on_position_control(scaled_event)
        if exit_reason == "SIGNAL_REVERSAL":
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

    trades = _load_run_trades(
        client, symbol, server_time_ms, sleep, expected_exit_side=close_side
    )
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
        "predictive_limit_price": format(predictive_price, "f"),
        "predicted_pullback_bps": format(predicted_pullback_bps, "f"),
        "directional_forecast_bps": format(directional_forecast_bps, "f"),
        "predictive_entry_model": predictive_entry_model,
        "maker_executed_quantity": format(maker_executed_quantity, "f"),
        "scale_in_count": scale_in_count,
        "maximum_net_loss_budget": format(maximum_net_loss, "f"),
        "minimum_estimated_net_target": format(minimum_estimated_net_target, "f"),
        "risk_sizing_slippage_rate": format(risk_sizing_slippage_rate, "f"),
        "signal_quality_score": format(plan.signal_quality_score, "f"),
        "signal_confirmation_rounds": plan.signal_confirmation_rounds,
        "setup_type": plan.setup_type,
        "market_momentum_bps": format(plan.market_momentum_bps, "f"),
        "market_breadth_count": plan.market_breadth_count,
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
    predictive_limit_price: Decimal,
    predicted_pullback_bps: Decimal,
    directional_forecast_bps: Decimal,
    predictive_entry_model: str,
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
        "predictive_limit_price": format(predictive_limit_price, "f"),
        "predicted_pullback_bps": format(predicted_pullback_bps, "f"),
        "directional_forecast_bps": format(directional_forecast_bps, "f"),
        "predictive_entry_model": predictive_entry_model,
        "effective_margin_budget": format(effective_margin_budget, "f"),
        "signal_quality_score": format(plan.signal_quality_score, "f"),
        "signal_confirmation_rounds": plan.signal_confirmation_rounds,
        "setup_type": plan.setup_type,
        "market_momentum_bps": format(plan.market_momentum_bps, "f"),
        "market_breadth_count": plan.market_breadth_count,
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
        if len(trades) >= 2 and any(
            str(item.get("side")) == expected_exit_side for item in trades
        ):
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
