"""Continuous runner for the strategy-agnostic automatic trade engine."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from ai_quant.automation.engine import (
    AutomaticTradeEngine,
    AutomaticTradeIntent,
    AutomaticTradeOutcome,
    AutomationSnapshot,
)


class AutomationSnapshotSource(Protocol):
    def capture(self, *, now: datetime) -> AutomationSnapshot: ...


class AutomaticIntentSource(Protocol):
    """Project-owned decision adapter; the framework supplies no default implementation."""

    def fetch(
        self,
        *,
        snapshot: AutomationSnapshot,
        now: datetime,
    ) -> tuple[AutomaticTradeIntent, ...]: ...


class AutomationOutcomeSink(Protocol):
    def record(
        self,
        *,
        snapshot: AutomationSnapshot,
        outcomes: tuple[AutomaticTradeOutcome, ...],
        completed_at: datetime,
    ) -> None: ...


class AutomaticTradeRunner:
    """Poll a decision source and process its intents until shutdown is requested."""

    def __init__(
        self,
        *,
        engine: AutomaticTradeEngine,
        snapshot_source: AutomationSnapshotSource,
        intent_source: AutomaticIntentSource,
        outcome_sink: AutomationOutcomeSink,
    ) -> None:
        self._engine = engine
        self._snapshot_source = snapshot_source
        self._intent_source = intent_source
        self._outcome_sink = outcome_sink

    def run_cycle(self, *, now: datetime) -> tuple[AutomaticTradeOutcome, ...]:
        _require_utc(now)
        snapshot = self._snapshot_source.capture(now=now)
        intents = self._intent_source.fetch(snapshot=snapshot, now=now)
        outcomes = self._engine.process_cycle(intents=intents, snapshot=snapshot, now=now)
        self._outcome_sink.record(snapshot=snapshot, outcomes=outcomes, completed_at=now)
        return outcomes

    def run_forever(
        self,
        *,
        interval_seconds: int,
        stop_requested: Callable[[], bool],
        sleep: Callable[[float], None],
        utc_now: Callable[[], datetime] | None = None,
    ) -> None:
        if not 1 <= interval_seconds <= 3_600:
            raise ValueError("automatic evaluation interval must be within [1,3600] seconds")
        clock = utc_now or (lambda: datetime.now(UTC))
        while not stop_requested():
            self.run_cycle(now=clock())
            if not stop_requested():
                sleep(float(interval_seconds))


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("runner time must be timezone-aware UTC")
