"""Oldest-cycle-first single-concurrency iteration queue."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum


class CycleState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    DEFERRED_QUOTA = "DEFERRED_QUOTA"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class IterationCycle:
    cycle_id: str
    scheduled_for: datetime
    data_cutoff_at: datetime
    champion_hash: str
    state: CycleState = CycleState.QUEUED
    attempt_count: int = 0
    retry_after: datetime | None = None


class IterationQueue:
    def __init__(self) -> None:
        self._cycles: dict[str, IterationCycle] = {}
        self._running: str | None = None

    def enqueue(self, cycle: IterationCycle) -> None:
        existing = self._cycles.get(cycle.cycle_id)
        if existing and existing != cycle:
            raise ValueError("cycle identity conflict")
        self._cycles.setdefault(cycle.cycle_id, cycle)

    def claim(self, now: datetime, *, quota_available: bool) -> IterationCycle | None:
        if self._running is not None:
            return None
        eligible = [
            cycle
            for cycle in self._cycles.values()
            if cycle.state in {CycleState.QUEUED, CycleState.DEFERRED_QUOTA}
            and cycle.scheduled_for <= now
            and (cycle.retry_after is None or cycle.retry_after <= now)
        ]
        if not eligible:
            return None
        cycle = min(eligible, key=lambda item: (item.scheduled_for, item.cycle_id))
        if not quota_available:
            deferred = replace(
                cycle,
                state=CycleState.DEFERRED_QUOTA,
                retry_after=now + timedelta(days=1),
            )
            self._cycles[cycle.cycle_id] = deferred
            return deferred
        running = replace(
            cycle,
            state=CycleState.RUNNING,
            attempt_count=cycle.attempt_count + 1,
            retry_after=None,
        )
        self._cycles[cycle.cycle_id] = running
        self._running = cycle.cycle_id
        return running

    def finish(self, cycle_id: str, *, success: bool) -> IterationCycle:
        if self._running != cycle_id:
            raise ValueError("cycle is not the running attempt")
        finished = replace(
            self._cycles[cycle_id],
            state=CycleState.COMPLETED if success else CycleState.FAILED,
        )
        self._cycles[cycle_id] = finished
        self._running = None
        return finished
