from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from ai_quant.cost.edge import CostBreakdown, CostComponent, evaluate_edge
from tests.market_fixtures import BASE_TIME


def costs() -> CostBreakdown:
    component = CostComponent(Decimal("1"), BASE_TIME, "a" * 64)
    return CostBreakdown(*(component for _ in range(7)))


def test_all_cost_components_are_summed_exactly() -> None:
    decision = evaluate_edge(
        Decimal("10"),
        costs(),
        now=BASE_TIME + timedelta(seconds=1),
        maximum_component_age_seconds=10,
        minimum_net_edge_bps=Decimal("2"),
    )
    assert decision.total_cost_bps == Decimal(7)
    assert decision.net_edge_bps == Decimal(3)
    assert decision.approved


def test_stale_component_rejects_entry_but_not_risk_exit() -> None:
    stale = replace(costs(), entry_fee=CostComponent(Decimal(1), BASE_TIME, "b" * 64))
    denied = evaluate_edge(
        Decimal(10),
        stale,
        now=BASE_TIME + timedelta(seconds=11),
        maximum_component_age_seconds=10,
        minimum_net_edge_bps=Decimal(1),
    )
    assert not denied.approved
    assert denied.reason_codes == ("NET_EDGE_COMPONENT_STALE",)

    exit_decision = evaluate_edge(
        None,
        None,
        now=BASE_TIME,
        maximum_component_age_seconds=0,
        minimum_net_edge_bps=Decimal(99),
        reducing_risk=True,
    )
    assert exit_decision.approved
