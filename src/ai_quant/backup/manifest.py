"""Create and verify exact checksummed PostgreSQL/WAL backup manifests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BackupArtifact:
    path: str
    sha256: str
    size_bytes: int


def create_backup_manifest(
    root: Path,
    artifacts: list[Path],
    *,
    database_migration_heads: dict[str, str],
    created_at: datetime | None = None,
) -> dict[str, object]:
    entries = []
    for artifact in sorted(artifacts):
        resolved = artifact.resolve()
        if not resolved.is_file() or not resolved.is_relative_to(root.resolve()):
            raise ValueError("backup artifact must be a regular file below backup root")
        entries.append(
            {
                "path": resolved.relative_to(root.resolve()).as_posix(),
                "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
                "size_bytes": resolved.stat().st_size,
            }
        )
    return {
        "schema_version": "1.0.0",
        "created_at": (created_at or datetime.now(UTC)).isoformat(),
        "database_migration_heads": dict(sorted(database_migration_heads.items())),
        "artifacts": entries,
    }


def verify_backup_manifest(root: Path, manifest: dict[str, object]) -> bool:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        return False
    for item in artifacts:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "size_bytes"}:
            return False
        path = (root / str(item["path"])).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            return False
        if path.stat().st_size != item["size_bytes"]:
            return False
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            return False
    return True


def manifest_bytes(manifest: dict[str, object]) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode() + b"\n"
