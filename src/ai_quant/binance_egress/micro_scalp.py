"""One bounded, attended Testnet micro-scalp lifecycle for protocol validation."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from ai_quant.binance_egress.testnet_probe import (
    BinanceTestnetClient,
    TestnetProbeError,
    Transport,
    _credential,
    _flatten_position,
    _position_quantity,
    _query_algo_consistent,
    _symbol_filters,
    _terminalize_algo_after_flat,
    _urllib_transport,
)
from ai_quant.risk.micro_scalp import (
    MicroScalpPlan,
    plan_long_micro_scalp,
    plan_long_micro_scalp_for_quantity,
)


def run_testnet_micro_scalp(
    *,
    api_key_file: Path,
    api_secret_file: Path,
    repository_root: Path,
    symbol: str = "SOLUSDT",
    margin_budget: Decimal = Decimal("1"),
    target_net_profit: Decimal = Decimal("0.1"),
    maximum_net_loss: Decimal = Decimal("0.1"),
    maximum_holding_seconds: int = 30,
    adverse_exit_slippage_bps: Decimal = Decimal("2"),
    transport: Transport = _urllib_transport,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run one Testnet-only position with native TP/SL and a hard time exit."""
    if not 30 <= maximum_holding_seconds <= 900:
        raise TestnetProbeError("MICRO_SCALP_MAX_HOLD_INVALID")
    if margin_budget <= 0 or margin_budget > Decimal("1"):
        raise TestnetProbeError("MICRO_SCALP_MARGIN_BUDGET_INVALID")
    if target_net_profit <= 0 or maximum_net_loss <= 0:
        raise TestnetProbeError("MICRO_SCALP_PNL_BUDGET_INVALID")

    started_at = datetime.now(UTC)
    api_key = _credential(api_key_file, repository_root)
    api_secret = _credential(api_secret_file, repository_root)
    client = BinanceTestnetClient(api_key, api_secret, transport=transport)
    server_time_ms, offset = client.synchronize_time()
    if abs(offset) > 1_000:
        raise TestnetProbeError("TESTNET_CLOCK_OFFSET_EXCESSIVE")
    if client.position_mode().get("dualSidePosition") is not False:
        raise TestnetProbeError("ACCOUNT_POSITION_MODE_NOT_ONE_WAY")
    if client.open_orders(symbol) or client.open_algo_orders(symbol):
        raise TestnetProbeError("TESTNET_ACCOUNT_NOT_CLEAN")
    if _position_quantity(client, symbol) != 0:
        raise TestnetProbeError("TESTNET_ACCOUNT_NOT_CLEAN")

    leverage = 10
    changed = client.change_initial_leverage(symbol, leverage)
    if changed.get("symbol") != symbol or changed.get("leverage") != leverage:
        raise TestnetProbeError("CHANGE_INITIAL_LEVERAGE_RESPONSE_MISMATCH")
    commission = client.commission_rate(symbol)
    try:
        taker_fee_rate = Decimal(str(commission["takerCommissionRate"]))
    except (KeyError, ArithmeticError) as exc:
        raise TestnetProbeError("COMMISSION_RATE_INVALID_RESPONSE") from exc

    exchange_info = client.exchange_info()
    ticker = client.book_ticker(symbol)
    filters = _symbol_filters(exchange_info, symbol)
    try:
        ask_price = Decimal(str(ticker["askPrice"]))
        market_lot = filters.get("MARKET_LOT_SIZE") or filters["LOT_SIZE"]
        step_size = Decimal(str(market_lot["stepSize"]))
        minimum_quantity = Decimal(str(market_lot["minQty"]))
        minimum_notional = Decimal(str(filters.get("MIN_NOTIONAL", {}).get("notional", "0")))
        tick_size = Decimal(str(filters["PRICE_FILTER"]["tickSize"]))
    except (KeyError, ArithmeticError) as exc:
        raise TestnetProbeError("TEST_SYMBOL_FILTERS_INVALID") from exc
    try:
        entry_plan = plan_long_micro_scalp(
            entry_assumption=ask_price,
            margin_budget=margin_budget * Decimal("0.95"),
            initial_leverage=Decimal(leverage),
            step_size=step_size,
            minimum_quantity=minimum_quantity,
            minimum_notional=minimum_notional,
            tick_size=tick_size,
            taker_fee_rate=taker_fee_rate,
            target_net_profit=target_net_profit,
            maximum_net_loss=maximum_net_loss,
            adverse_exit_slippage_bps=adverse_exit_slippage_bps,
        )
    except ValueError as exc:
        raise TestnetProbeError("MICRO_SCALP_PLAN_REJECTED") from exc

    entry_client_id = f"aq-t-micro-{secrets.token_hex(6)}"
    stop_client_id = f"aqa-t-micro-stop-{secrets.token_hex(5)}"
    target_client_id = f"aqa-t-micro-tp-{secrets.token_hex(5)}"
    entry_document: dict[str, Any] | None = None
    close_document: dict[str, Any] | None = None
    stop_document: dict[str, Any] | None = None
    target_document: dict[str, Any] | None = None
    stop_id: int | None = None
    target_id: int | None = None
    exit_reason = "UNRESOLVED"
    stop_latency_ms: int | None = None
    target_latency_ms: int | None = None
    final_plan: MicroScalpPlan | None = None
    try:
        entry_document = client.place_order(
            {
                "symbol": symbol,
                "side": "BUY",
                "positionSide": "BOTH",
                "type": "MARKET",
                "quantity": format(entry_plan.quantity, "f"),
                "newOrderRespType": "RESULT",
                "newClientOrderId": entry_client_id,
            }
        )
        if entry_document.get("clientOrderId") != entry_client_id:
            raise TestnetProbeError("MICRO_SCALP_ENTRY_ID_MISMATCH")
        if entry_document.get("status") != "FILLED":
            raise TestnetProbeError("MICRO_SCALP_ENTRY_NOT_FILLED")
        position_quantity = _position_quantity(client, symbol)
        if position_quantity <= 0:
            raise TestnetProbeError("MICRO_SCALP_POSITION_NOT_LONG")
        entry_price = _resolve_entry_price(client, symbol, entry_client_id, entry_document)
        try:
            final_plan = plan_long_micro_scalp_for_quantity(
                entry_assumption=entry_price,
                quantity=position_quantity,
                margin_budget=margin_budget,
                initial_leverage=Decimal(leverage),
                minimum_quantity=minimum_quantity,
                minimum_notional=minimum_notional,
                tick_size=tick_size,
                taker_fee_rate=taker_fee_rate,
                target_net_profit=target_net_profit,
                maximum_net_loss=maximum_net_loss,
                adverse_exit_slippage_bps=adverse_exit_slippage_bps,
            )
        except ValueError as exc:
            raise TestnetProbeError("MICRO_SCALP_ACTUAL_FILL_REJECTED") from exc

        entry_update = entry_document.get("updateTime")
        if not isinstance(entry_update, int):
            raise TestnetProbeError("MICRO_SCALP_ENTRY_TIME_MISSING")
        stop_document = _place_protection(
            client,
            symbol=symbol,
            client_algo_id=stop_client_id,
            order_type="STOP_MARKET",
            trigger_price=final_plan.stop_trigger,
        )
        stop_id = _confirmed_algo_id(stop_document, stop_client_id, "STOP")
        stop_create = stop_document.get("createTime")
        if not isinstance(stop_create, int):
            raise TestnetProbeError("PROTECTION_LATENCY_TIMESTAMPS_MISSING")
        stop_latency_ms = stop_create - entry_update
        if not 0 <= stop_latency_ms <= 1_000:
            raise TestnetProbeError("PROTECTION_CONFIRMATION_OVER_1000MS")
        _require_algo_new(client, symbol, stop_id, stop_client_id, "STOP")

        target_document = _place_protection(
            client,
            symbol=symbol,
            client_algo_id=target_client_id,
            order_type="TAKE_PROFIT_MARKET",
            trigger_price=final_plan.target_trigger,
        )
        target_id = _confirmed_algo_id(target_document, target_client_id, "TAKE_PROFIT")
        target_create = target_document.get("createTime")
        if not isinstance(target_create, int):
            raise TestnetProbeError("PROTECTION_LATENCY_TIMESTAMPS_MISSING")
        target_latency_ms = target_create - entry_update
        if target_latency_ms < 0:
            raise TestnetProbeError("PROTECTION_LATENCY_TIMESTAMPS_INVALID")
        _require_algo_new(client, symbol, target_id, target_client_id, "TAKE_PROFIT")

        deadline = monotonic() + maximum_holding_seconds
        while _position_quantity(client, symbol) != 0 and monotonic() < deadline:
            sleep(min(1.0, max(0.0, deadline - monotonic())))
        if _position_quantity(client, symbol) != 0:
            close_document = _flatten_position(client, symbol)
            if close_document is None or close_document.get("status") != "FILLED":
                raise TestnetProbeError("MICRO_SCALP_TIME_EXIT_NOT_FILLED")
            exit_reason = "MAX_HOLDING_TIME"
        else:
            stop_status = _algo_status(client, symbol, stop_id, stop_client_id)
            target_status = _algo_status(client, symbol, target_id, target_client_id)
            if target_status in {"TRIGGERED", "FINISHED"}:
                exit_reason = "TAKE_PROFIT"
            elif stop_status in {"TRIGGERED", "FINISHED"}:
                exit_reason = "STOP_LOSS"
            else:
                exit_reason = "NATIVE_EXIT_UNCLASSIFIED"
        if _position_quantity(client, symbol) != 0:
            raise TestnetProbeError("MICRO_SCALP_POSITION_NOT_FLAT")

        _terminalize_algo_after_flat(
            client, symbol=symbol, algo_id=stop_id, client_algo_id=stop_client_id
        )
        _terminalize_algo_after_flat(
            client, symbol=symbol, algo_id=target_id, client_algo_id=target_client_id
        )
    finally:
        if entry_document is not None and _position_quantity(client, symbol) != 0:
            _flatten_position(client, symbol)
        for algo_id in (stop_id, target_id):
            if algo_id is None:
                continue
            try:
                if any(item.get("algoId") == algo_id for item in client.open_algo_orders(symbol)):
                    client.cancel_algo_order(algo_id=algo_id)
            except TestnetProbeError:
                pass

    if client.open_orders(symbol) or client.open_algo_orders(symbol):
        raise TestnetProbeError("MICRO_SCALP_CLEANUP_INCOMPLETE")
    if _position_quantity(client, symbol) != 0:
        raise TestnetProbeError("MICRO_SCALP_CLEANUP_INCOMPLETE")
    if final_plan is None or entry_document is None:
        raise TestnetProbeError("MICRO_SCALP_RESULT_INCOMPLETE")

    trades = _load_run_trades(client, symbol, server_time_ms, sleep)
    realized_pnl = sum((Decimal(str(item.get("realizedPnl", "0"))) for item in trades), Decimal(0))
    commission_paid = Decimal(0)
    for trade in trades:
        asset = trade.get("commissionAsset")
        if asset not in {None, "USDT"}:
            raise TestnetProbeError("MICRO_SCALP_COMMISSION_ASSET_UNSUPPORTED")
        commission_paid += Decimal(str(trade.get("commission", "0")))
    net_pnl = realized_pnl - commission_paid
    request_hash = hashlib.sha256(
        json.dumps(
            {
                "margin_budget": format(margin_budget, "f"),
                "maximum_holding_seconds": maximum_holding_seconds,
                "maximum_net_loss": format(maximum_net_loss, "f"),
                "symbol": symbol,
                "target_net_profit": format(target_net_profit, "f"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {
        "schema_version": "1.0.0",
        "probe": "BINANCE_USDS_M_FUTURES_TESTNET_MICRO_SCALP",
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "completed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "result": "PASS",
        "environment": "testnet",
        "production_endpoint_requests": 0,
        "symbol": symbol,
        "request_hash": request_hash,
        "initial_leverage": leverage,
        "margin_budget": format(margin_budget, "f"),
        "actual_initial_margin": format(final_plan.initial_margin, "f"),
        "quantity": format(final_plan.quantity, "f"),
        "entry_price": format(final_plan.entry_assumption, "f"),
        "stop_trigger": format(final_plan.stop_trigger, "f"),
        "target_trigger": format(final_plan.target_trigger, "f"),
        "target_net_profit": format(target_net_profit, "f"),
        "maximum_net_loss": format(maximum_net_loss, "f"),
        "maximum_holding_seconds": maximum_holding_seconds,
        "exit_reason": exit_reason,
        "realized_pnl": format(realized_pnl, "f"),
        "commission_paid": format(commission_paid, "f"),
        "net_pnl": format(net_pnl, "f"),
        "target_achieved": net_pnl >= target_net_profit,
        "stop_confirmation_latency_ms": stop_latency_ms,
        "take_profit_confirmation_latency_ms": target_latency_ms,
        "account_trade_count": len(trades),
        "final_open_order_count": 0,
        "final_open_algo_order_count": 0,
        "final_position_quantity": "0",
        "clock_offset_ms": offset,
    }


def _place_protection(
    client: BinanceTestnetClient,
    *,
    symbol: str,
    client_algo_id: str,
    order_type: str,
    trigger_price: Decimal,
) -> dict[str, Any]:
    return client.place_algo_order(
        {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": "SELL",
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
        raise TestnetProbeError(f"MICRO_SCALP_{role}_NOT_CONFIRMED")
    algo_id = document.get("algoId")
    if not isinstance(algo_id, int):
        raise TestnetProbeError(f"MICRO_SCALP_{role}_ID_MISSING")
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
        raise TestnetProbeError(f"MICRO_SCALP_{role}_QUERY_NOT_NEW")


def _algo_status(
    client: BinanceTestnetClient,
    symbol: str,
    algo_id: int,
    client_id: str,
) -> str:
    try:
        return str(
            _query_algo_consistent(
                client, symbol=symbol, algo_id=algo_id, client_algo_id=client_id
            ).get("algoStatus")
        )
    except TestnetProbeError:
        return "REMOVED"


def _filled_average_price(document: dict[str, Any]) -> Decimal:
    average = Decimal(str(document.get("avgPrice", "0")))
    if average > 0:
        return average
    quantity = Decimal(str(document.get("executedQty", "0")))
    quote = Decimal(str(document.get("cumQuote", "0")))
    if quantity <= 0 or quote <= 0:
        raise TestnetProbeError("MICRO_SCALP_ENTRY_PRICE_MISSING")
    return quote / quantity


def _resolve_entry_price(
    client: BinanceTestnetClient,
    symbol: str,
    client_order_id: str,
    entry_document: dict[str, Any],
) -> Decimal:
    try:
        return _filled_average_price(entry_document)
    except TestnetProbeError:
        pass
    queried = client.query_order(symbol, client_order_id)
    try:
        return _filled_average_price(queried)
    except TestnetProbeError:
        pass
    positions = client.position_risk(symbol)
    entry_prices = {
        Decimal(str(item.get("entryPrice", "0")))
        for item in positions
        if Decimal(str(item.get("positionAmt", "0"))) > 0
    }
    if len(entry_prices) != 1:
        raise TestnetProbeError("MICRO_SCALP_ENTRY_PRICE_MISSING")
    entry_price = next(iter(entry_prices))
    if entry_price <= 0:
        raise TestnetProbeError("MICRO_SCALP_ENTRY_PRICE_MISSING")
    return entry_price


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
    raise TestnetProbeError("MICRO_SCALP_TRADES_INCOMPLETE")
