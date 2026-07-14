from ai_quant.monitoring.metrics import ALERT_RULES, MetricRegistry


def test_metrics_are_stable_and_every_alert_has_a_runbook() -> None:
    registry = MetricRegistry()
    registry.set_gauge("aiq_database_writable", 0)
    registry.set_gauge("aiq_order_unknown_age_seconds", 6, symbol="BTCUSDT")

    assert registry.render() == (
        'aiq_database_writable 0\naiq_order_unknown_age_seconds{symbol="BTCUSDT"} 6\n'
    )
    assert all(rule.runbook.startswith("runbooks/") for rule in ALERT_RULES)
    assert all(rule.severity in {"P0", "P1"} for rule in ALERT_RULES)
