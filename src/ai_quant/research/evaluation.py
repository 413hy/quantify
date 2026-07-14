"""Closed 90-day champion retention criteria."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ForwardMetrics:
    trades: int
    net_expectancy: Decimal
    profit_factor: Decimal
    maximum_drawdown_pct: Decimal
    stressed_net_expectancy: Decimal
    all_parameter_variants_positive: bool
    maximum_symbol_positive_contribution_pct: Decimal
    expectancy_without_top_symbol: Decimal


@dataclass(frozen=True, slots=True)
class ResearchGateDecision:
    decision: str
    reason_codes: tuple[str, ...]
    runtime_action: str


def evaluate_forward_metrics(metrics: ForwardMetrics) -> ResearchGateDecision:
    reasons: list[str] = []
    if metrics.trades < 500:
        reasons.append("OOS_TRADES_INSUFFICIENT")
    if metrics.net_expectancy <= 0:
        reasons.append("NET_EXPECTANCY_NOT_POSITIVE")
    if metrics.profit_factor < Decimal("1.15"):
        reasons.append("PROFIT_FACTOR_BELOW_1_15")
    if metrics.maximum_drawdown_pct > Decimal("5"):
        reasons.append("MAX_DRAWDOWN_ABOVE_5_PERCENT")
    if metrics.stressed_net_expectancy < 0:
        reasons.append("COST_STRESS_NEGATIVE")
    if not metrics.all_parameter_variants_positive:
        reasons.append("PARAMETER_STABILITY_FAILED")
    if metrics.maximum_symbol_positive_contribution_pct > Decimal("40"):
        reasons.append("SYMBOL_CONCENTRATION_ABOVE_40_PERCENT")
    if metrics.expectancy_without_top_symbol < 0:
        reasons.append("TOP_SYMBOL_DEPENDENCY")
    return ResearchGateDecision(
        decision="FAIL" if reasons else "PASS",
        reason_codes=tuple(reasons),
        runtime_action="PAUSED_NEW_ENTRIES" if reasons else "ENABLE_MONTHLY_REVIEW",
    )
