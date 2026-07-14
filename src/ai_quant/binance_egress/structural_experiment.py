"""Execute one unvalidated Testnet structural experiment with native protection."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path
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
    adverse_slippage_rate: Decimal = Decimal("0.0002"),
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


def run_structural_experiment(
    *,
    api_key_file: Path,
    api_secret_file: Path,
    repository_root: Path,
    plan: TestnetExperimentalPlan,
    margin_budget: Decimal = Decimal("1"),
    maximum_net_loss: Decimal = Decimal("0.35"),
    stop_requested: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Open one Testnet position and wait only for strategy protection or operator stop."""
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
    changed = client.change_testnet_experiment_leverage(symbol, leverage)
    if changed.get("symbol") != symbol or changed.get("leverage") != leverage:
        raise TestnetProbeError("CHANGE_INITIAL_LEVERAGE_RESPONSE_MISMATCH")
    exchange_info = client.exchange_info()
    ticker = client.book_ticker(symbol)
    commission_document = client.commission_rate(symbol)
    try:
        price_key = "askPrice" if plan.direction is Direction.LONG else "bidPrice"
        reference = Decimal(str(ticker[price_key]))
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
    )
    quantity = plan_market_quantity(
        exchange_info,
        symbol=symbol,
        reference_price=reference,
        margin_budget=effective_margin_budget,
        leverage=leverage,
    )
    entry_side = "BUY" if plan.direction is Direction.LONG else "SELL"
    close_side = "SELL" if plan.direction is Direction.LONG else "BUY"
    entry_id = f"aq-t-exp-{secrets.token_hex(6)}"
    stop_client_id = f"aqa-t-exp-sl-{secrets.token_hex(5)}"
    target_client_id = f"aqa-t-exp-tp-{secrets.token_hex(5)}"
    entry: dict[str, Any] | None = None
    stop_id: int | None = None
    target_id: int | None = None
    exit_reason = "UNRESOLVED"
    actual_entry = Decimal(0)
    stop_trigger = Decimal(0)
    target_trigger = Decimal(0)
    try:
        entry = client.place_order(
            {
                "symbol": symbol,
                "side": entry_side,
                "positionSide": "BOTH",
                "type": "MARKET",
                "quantity": format(quantity, "f"),
                "newOrderRespType": "RESULT",
                "newClientOrderId": entry_id,
            }
        )
        if entry.get("clientOrderId") != entry_id or entry.get("status") != "FILLED":
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

        while _position_quantity(client, symbol) != 0 and not stop_requested():
            sleep(1)
        if _position_quantity(client, symbol) != 0:
            closed = _flatten_position(client, symbol)
            if closed is None or closed.get("status") != "FILLED":
                raise TestnetProbeError("EXPERIMENT_OPERATOR_EXIT_NOT_FILLED")
            exit_reason = "OPERATOR_SERVICE_STOP"
        else:
            stop_status = _algo_status(client, symbol, stop_id, stop_client_id)
            target_status = _algo_status(client, symbol, target_id, target_client_id)
            if target_status in {"TRIGGERED", "FINISHED"}:
                exit_reason = "TAKE_PROFIT"
            elif stop_status in {"TRIGGERED", "FINISHED"}:
                exit_reason = "STOP_LOSS"
            else:
                exit_reason = "NATIVE_EXIT_UNCLASSIFIED"
        _terminalize_algo_after_flat(
            client, symbol=symbol, algo_id=stop_id, client_algo_id=stop_client_id
        )
        _terminalize_algo_after_flat(
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

    trades = _load_run_trades(client, symbol, server_time_ms, sleep)
    realized = sum((Decimal(str(item.get("realizedPnl", "0"))) for item in trades), Decimal(0))
    commission = sum((Decimal(str(item.get("commission", "0"))) for item in trades), Decimal(0))
    net = realized - commission
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
        "leverage_policy": "EXCHANGE_MAXIMUM_TESTNET_ONLY",
        "margin_budget": format(margin_budget, "f"),
        "effective_margin_budget": format(effective_margin_budget, "f"),
        "maximum_net_loss_budget": format(maximum_net_loss, "f"),
        "quantity": format(quantity, "f"),
        "entry_price": format(actual_entry, "f"),
        "stop_trigger": format(stop_trigger, "f"),
        "target_trigger": format(target_trigger, "f"),
        "exit_reason": exit_reason,
        "realized_pnl": format(realized, "f"),
        "commission_paid": format(commission, "f"),
        "net_pnl": format(net, "f"),
        "target_achieved": exit_reason == "TAKE_PROFIT",
        "account_trade_count": len(trades),
        "elapsed_time_exit_enabled": False,
        "production_endpoint_requests": 0,
    }


def exchange_maximum_initial_leverage(
    brackets: list[dict[str, Any]], symbol: str
) -> int:
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
            "workingType": "MARK_PRICE",
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


def _algo_status(
    client: BinanceTestnetClient, symbol: str, algo_id: int, client_id: str
) -> str:
    try:
        return str(
            _query_algo_consistent(
                client, symbol=symbol, algo_id=algo_id, client_algo_id=client_id
            ).get("algoStatus")
        )
    except TestnetProbeError:
        return "REMOVED"


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
) -> list[dict[str, Any]]:
    for _ in range(10):
        trades = client.account_trades(symbol, start_time_ms=start_time_ms)
        if len(trades) >= 2:
            return trades
        sleep(0.2)
    raise TestnetProbeError("EXPERIMENT_TRADES_INCOMPLETE")
