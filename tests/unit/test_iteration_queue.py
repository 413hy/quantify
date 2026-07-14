from __future__ import annotations

from datetime import timedelta

from ai_quant.iteration.queue import CycleState, IterationCycle, IterationQueue
from tests.market_fixtures import BASE_TIME


def cycle(identity: str, days: int) -> IterationCycle:
    return IterationCycle(
        cycle_id=identity,
        scheduled_for=BASE_TIME + timedelta(days=days),
        data_cutoff_at=BASE_TIME + timedelta(days=days),
        champion_hash="a" * 64,
    )


def test_old_cycle_has_fifo_priority_and_only_one_attempt_runs() -> None:
    queue = IterationQueue()
    queue.enqueue(cycle("new", 1))
    queue.enqueue(cycle("old", 0))
    claimed = queue.claim(BASE_TIME + timedelta(days=2), quota_available=True)
    assert claimed is not None and claimed.cycle_id == "old"
    assert queue.claim(BASE_TIME + timedelta(days=2), quota_available=True) is None
    queue.finish("old", success=True)
    assert queue.claim(BASE_TIME + timedelta(days=2), quota_available=True).cycle_id == "new"  # type: ignore[union-attr]


def test_quota_deferral_preserves_cycle_cutoff_and_champion() -> None:
    queue = IterationQueue()
    original = cycle("cycle-1", 0)
    queue.enqueue(original)
    deferred = queue.claim(BASE_TIME, quota_available=False)
    assert deferred is not None
    assert deferred.state is CycleState.DEFERRED_QUOTA
    assert deferred.data_cutoff_at == original.data_cutoff_at
    assert deferred.champion_hash == original.champion_hash
    assert deferred.attempt_count == 0
