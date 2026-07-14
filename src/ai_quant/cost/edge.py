"""Fail-closed net-edge arithmetic with all mandatory cost components."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class CostComponent:
    bps: Decimal
    observed_at: datetime
    evidence_hash: str

    def __post_init__(self) -> None:
        if self.bps < 0 or len(self.evidence_hash) != 64:
            raise ValueError("invalid cost component")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != UTC.utcoffset(
            self.observed_at
        ):
            raise ValueError("cost timestamp must be timezone-aware UTC")


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    entry_fee: CostComponent
    exit_fee: CostComponent
    entry_slippage: CostComponent
    exit_slippage: CostComponent
    adverse_selection: CostComponent
    funding: CostComponent
    failure_and_cancel: CostComponent

    @property
    def total_bps(self) -> Decimal:
        return sum(
            (
                self.entry_fee.bps,
                self.exit_fee.bps,
                self.entry_slippage.bps,
                self.exit_slippage.bps,
                self.adverse_selection.bps,
                self.funding.bps,
                self.failure_and_cancel.bps,
            ),
            Decimal(0),
        )


@dataclass(frozen=True, slots=True)
class EdgeDecision:
    approved: bool
    gross_edge_bps: Decimal
    total_cost_bps: Decimal
    net_edge_bps: Decimal
    reason_codes: tuple[str, ...]


def evaluate_edge(
    gross_edge_bps: Decimal | None,
    costs: CostBreakdown | None,
    *,
    now: datetime,
    maximum_component_age_seconds: int,
    minimum_net_edge_bps: Decimal,
    reducing_risk: bool = False,
) -> EdgeDecision:
    if reducing_risk:
        gross = gross_edge_bps or Decimal(0)
        total = costs.total_bps if costs else Decimal(0)
        return EdgeDecision(True, gross, total, gross - total, ("RISK_REDUCING_EXIT",))
    if gross_edge_bps is None or costs is None:
        return EdgeDecision(
            False,
            gross_edge_bps or Decimal(0),
            Decimal(0),
            Decimal(0),
            ("NET_EDGE_EVIDENCE_INCOMPLETE",),
        )
    components = (
        costs.entry_fee,
        costs.exit_fee,
        costs.entry_slippage,
        costs.exit_slippage,
        costs.adverse_selection,
        costs.funding,
        costs.failure_and_cancel,
    )
    if any(
        (now - component.observed_at).total_seconds() > maximum_component_age_seconds
        for component in components
    ):
        return EdgeDecision(
            False,
            gross_edge_bps,
            costs.total_bps,
            gross_edge_bps - costs.total_bps,
            ("NET_EDGE_COMPONENT_STALE",),
        )
    net = gross_edge_bps - costs.total_bps
    return EdgeDecision(
        approved=net >= minimum_net_edge_bps,
        gross_edge_bps=gross_edge_bps,
        total_cost_bps=costs.total_bps,
        net_edge_bps=net,
        reason_codes=() if net >= minimum_net_edge_bps else ("NET_EDGE_INSUFFICIENT",),
    )
