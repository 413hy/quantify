"""Full startup reconciliation; runtime always remains RISK_LOCKED until approved."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class AccountState:
    one_way_mode: bool
    cross_margin: bool
    equity: Decimal
    positions: dict[str, Decimal]
    ordinary_open_orders: frozenset[str]
    algo_open_orders: frozenset[str]
    protected_quantity: dict[str, Decimal]


@dataclass(frozen=True, slots=True)
class StartupReconciliation:
    runtime_state: str
    consistent: bool
    blocking_reasons: tuple[str, ...]
    repair_symbols: tuple[str, ...]
    orphan_local_orders: tuple[str, ...]
    external_orders: tuple[str, ...]


def reconcile_startup(local: AccountState, exchange: AccountState) -> StartupReconciliation:
    reasons: list[str] = []
    repair_symbols: set[str] = set()
    if not exchange.one_way_mode:
        reasons.append("ACCOUNT_POSITION_MODE_MISMATCH")
    if not exchange.cross_margin:
        reasons.append("ACCOUNT_MARGIN_MODE_MISMATCH")
    all_symbols = set(local.positions) | set(exchange.positions)
    for symbol in all_symbols:
        local_quantity = local.positions.get(symbol, Decimal(0))
        exchange_quantity = exchange.positions.get(symbol, Decimal(0))
        if local_quantity != exchange_quantity:
            reasons.append("POSITION_RECONCILIATION_MISMATCH")
            repair_symbols.add(symbol)
        if exchange_quantity != 0 and exchange.protected_quantity.get(symbol, Decimal(0)) < abs(
            exchange_quantity
        ):
            reasons.append("POSITION_PROTECTION_INSUFFICIENT")
            repair_symbols.add(symbol)
    local_orders = local.ordinary_open_orders | local.algo_open_orders
    exchange_orders = exchange.ordinary_open_orders | exchange.algo_open_orders
    orphan_local = local_orders - exchange_orders
    external = exchange_orders - local_orders
    if orphan_local:
        reasons.append("LOCAL_ORDER_MISSING_AT_EXCHANGE")
    if external:
        reasons.append("EXTERNAL_ORDER_REQUIRES_REPAIR")
    unique_reasons = tuple(dict.fromkeys(reasons))
    return StartupReconciliation(
        runtime_state="RISK_LOCKED",
        consistent=not unique_reasons,
        blocking_reasons=unique_reasons or ("MANUAL_UNLOCK_REQUIRED",),
        repair_symbols=tuple(sorted(repair_symbols)),
        orphan_local_orders=tuple(sorted(orphan_local)),
        external_orders=tuple(sorted(external)),
    )
