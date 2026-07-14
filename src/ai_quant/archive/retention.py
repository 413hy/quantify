"""Fail-closed local retention planning: only remotely verified objects are eligible."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RetentionCandidate:
    path: Path
    size_bytes: int
    modified_at: datetime


def plan_verified_deletions(
    candidates: list[RetentionCandidate],
    *,
    remotely_verified: set[Path],
    now: datetime,
    maximum_age: timedelta = timedelta(hours=72),
    maximum_bytes: int = 80 * 1024**3,
) -> tuple[RetentionCandidate, ...]:
    """Return oldest verified files needed to satisfy age and size constraints."""
    if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
        raise ValueError("now must be timezone-aware UTC")
    ordered = sorted(candidates, key=lambda item: (item.modified_at, str(item.path)))
    selected: list[RetentionCandidate] = []
    selected_paths: set[Path] = set()
    for item in ordered:
        if item.modified_at < now - maximum_age and item.path in remotely_verified:
            selected.append(item)
            selected_paths.add(item.path)
    remaining_bytes = sum(item.size_bytes for item in ordered if item.path not in selected_paths)
    for item in ordered:
        if remaining_bytes <= maximum_bytes:
            break
        if item.path in remotely_verified and item.path not in selected_paths:
            selected.append(item)
            selected_paths.add(item.path)
            remaining_bytes -= item.size_bytes
    return tuple(selected)
