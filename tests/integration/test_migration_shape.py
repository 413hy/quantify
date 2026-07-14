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
