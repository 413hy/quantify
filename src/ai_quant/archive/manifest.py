"""Stable, append-only daily archive manifests."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

from ai_quant.archive.parquet import ArchivedObject


def write_daily_manifest(
    root: Path,
    day: date,
    objects: list[ArchivedObject],
    *,
    previous_manifest_hash: str | None = None,
    manifest_id: str | None = None,
    created_at: datetime | None = None,
) -> Path:
    """Publish a new immutable manifest; older daily manifests are never rewritten."""
    if any(item.hour.date() != day for item in objects):
        raise ValueError("daily manifest cannot include an object from another UTC date")
    identity = manifest_id or uuid.uuid4().hex
    target = root / "manifests" / f"date={day.isoformat()}" / f"manifest-{identity}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError("manifest already exists")
    timestamp = created_at or datetime.now(UTC)
    document = {
        "created_at": timestamp.isoformat(),
        "date": day.isoformat(),
        "objects": [
            {
                "path": item.relative_path,
                "row_count": item.row_count,
                "schema_version": item.schema_version,
                "sha256": item.sha256,
                "size_bytes": item.size_bytes,
                "stream": item.stream,
                "symbol": item.symbol,
            }
            for item in sorted(objects, key=lambda value: value.relative_path)
        ],
        "previous_manifest_hash": previous_manifest_hash,
        "schema_version": "1.0.0",
    }
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    temporary = target.with_suffix(".json.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def manifest_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
