from decimal import Decimal

from ai_quant.research.evaluation import ForwardMetrics, evaluate_forward_metrics


def metrics(**changes: object) -> ForwardMetrics:
    values: dict[str, object] = {
        "trades": 500,
        "net_expectancy": Decimal("0.1"),
        "profit_factor": Decimal("1.15"),
        "maximum_drawdown_pct": Decimal("5"),
        "stressed_net_expectancy": Decimal("0"),
        "all_parameter_variants_positive": True,
        "maximum_symbol_positive_contribution_pct": Decimal("40"),
        "expectancy_without_top_symbol": Decimal("0"),
    }
    values.update(changes)
    return ForwardMetrics(**values)  # type: ignore[arg-type]


def test_boundary_metrics_pass() -> None:
    assert evaluate_forward_metrics(metrics()).decision == "PASS"


def test_any_failed_metric_pauses_new_entries() -> None:
    decision = evaluate_forward_metrics(
        metrics(trades=499, stressed_net_expectancy=Decimal("-0.01"))
    )
    assert decision.decision == "FAIL"
    assert decision.runtime_action == "PAUSED_NEW_ENTRIES"
    assert decision.reason_codes == (
        "OOS_TRADES_INSUFFICIENT",
        "COST_STRESS_NEGATIVE",
    )
