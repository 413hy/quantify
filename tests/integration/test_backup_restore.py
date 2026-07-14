from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ai_quant.backup.manifest import (
    create_backup_manifest,
    manifest_bytes,
    verify_backup_manifest,
)


def test_backup_manifest_detects_corruption(tmp_path: Path) -> None:
    database = tmp_path / "business.dump"
    wal = tmp_path / "000000010000000000000001.zst"
    database.write_bytes(b"postgres-custom-dump")
    wal.write_bytes(b"wal-segment")
    manifest = create_backup_manifest(
        tmp_path,
        [wal, database],
        database_migration_heads={"business": "0003_risk_execution", "host": "0010"},
        created_at=datetime(2026, 7, 14, tzinfo=UTC),
    )

    assert verify_backup_manifest(tmp_path, manifest)
    assert manifest_bytes(manifest).endswith(b"\n")
    wal.write_bytes(b"corrupted")
    assert not verify_backup_manifest(tmp_path, manifest)
