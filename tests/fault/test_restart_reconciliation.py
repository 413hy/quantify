from __future__ import annotations

from decimal import Decimal

from ai_quant.execution.startup import AccountState, reconcile_startup


def state(
    *,
    positions: dict[str, Decimal] | None = None,
    ordinary: frozenset[str] = frozenset(),
    algo: frozenset[str] = frozenset(),
    protected: dict[str, Decimal] | None = None,
) -> AccountState:
    return AccountState(
        one_way_mode=True,
        cross_margin=True,
        equity=Decimal("10000"),
        positions=positions or {},
        ordinary_open_orders=ordinary,
        algo_open_orders=algo,
        protected_quantity=protected or {},
    )


def test_restart_detects_external_position_order_and_missing_protection() -> None:
    local = state()
    exchange = state(
        positions={"BTCUSDT": Decimal("1")},
        ordinary=frozenset({"external-order"}),
    )

    result = reconcile_startup(local, exchange)

    assert result.runtime_state == "RISK_LOCKED"
    assert not result.consistent
    assert result.repair_symbols == ("BTCUSDT",)
    assert result.external_orders == ("external-order",)
    assert "POSITION_PROTECTION_INSUFFICIENT" in result.blocking_reasons


def test_even_consistent_restart_requires_manual_unlock() -> None:
    result = reconcile_startup(state(), state())
    assert result.consistent
    assert result.runtime_state == "RISK_LOCKED"
    assert result.blocking_reasons == ("MANUAL_UNLOCK_REQUIRED",)
