from pathlib import Path


def test_business_and_host_control_migration_trees_are_independent() -> None:
    root = Path(__file__).resolve().parents[2]
    business = root / "migrations/business"
    host = root / "migrations/host_control"
    assert business.resolve() != host.resolve()
    assert (business / "alembic.ini").is_file()
    assert (host / "alembic.ini").is_file()
    assert list((business / "versions").glob("*.py"))
    assert list((host / "versions").glob("*.py"))


def test_market_data_migration_contains_required_evidence_tables() -> None:
    root = Path(__file__).resolve().parents[2]
    migration = (root / "migrations/business/versions/0002_market_data.py").read_text()
    for table in (
        "market.raw_archive_objects",
        "market.remote_archive_receipts",
        "market.data_manifests",
        "market.data_quality_intervals",
    ):
        assert table in migration
    assert "create_hypertable" in migration
    assert "reject_append_only_mutation" in migration


def test_risk_execution_migration_keeps_decisions_and_intents_append_only() -> None:
    root = Path(__file__).resolve().parents[2]
    migration = (root / "migrations/business/versions/0003_risk_execution.py").read_text()
    for table in (
        "trading.risk_decisions",
        "trading.risk_reservation_events",
        "trading.order_intents",
        "trading.account_snapshots",
        "trading.position_snapshots",
        "trading.protection_observations",
    ):
        assert table in migration
    assert "reject_append_only_mutation" in migration


def test_operations_migration_is_event_sourced_and_append_only() -> None:
    root = Path(__file__).resolve().parents[2]
    migration = (root / "migrations/business/versions/0004_operations.py").read_text()
    for table in (
        "control.command_requests",
        "control.command_events",
        "control.flatten_challenges",
        "control.flatten_challenge_consumptions",
        "control.incident_events",
        "control.notification_deliveries",
        "control.backup_manifests",
    ):
        assert table in migration
    assert "reject_append_only_mutation" in migration
