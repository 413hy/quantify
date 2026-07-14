"""Mechanical 72h/24h gate evaluation without manufacturing missing evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise


@dataclass(frozen=True, slots=True)
class GateObservation:
    observed_at: datetime
    release_hash: str
    runtime_state: str
    open_p0_p1: int
    order_discrepancies: int
    duplicate_orders: int
    unprotected_positions: int


@dataclass(frozen=True, slots=True)
class GateResult:
    passed: bool
    observed_duration: timedelta
    reason_codes: tuple[str, ...]


def evaluate_continuous_gate(
    observations: list[GateObservation],
    *,
    required_duration: timedelta,
    maximum_gap: timedelta,
    allowed_runtime_states: set[str],
) -> GateResult:
    if len(observations) < 2:
        return GateResult(False, timedelta(0), ("INSUFFICIENT_OBSERVATION",))
    ordered = sorted(observations, key=lambda item: item.observed_at)
    reasons: list[str] = []
    releases = {item.release_hash for item in ordered}
    if len(releases) != 1:
        reasons.append("RELEASE_CHANGED_DURING_GATE")
    if any(
        later.observed_at - earlier.observed_at > maximum_gap
        for earlier, later in pairwise(ordered)
    ):
        reasons.append("OBSERVABILITY_GAP")
    if any(item.runtime_state not in allowed_runtime_states for item in ordered):
        reasons.append("RUNTIME_STATE_INVALID_DURING_GATE")
    if any(item.open_p0_p1 for item in ordered):
        reasons.append("OPEN_P0_P1")
    if any(item.order_discrepancies for item in ordered):
        reasons.append("ORDER_DISCREPANCY")
    if any(item.duplicate_orders for item in ordered):
        reasons.append("DUPLICATE_ORDER")
    if any(item.unprotected_positions for item in ordered):
        reasons.append("UNPROTECTED_POSITION")
    duration = ordered[-1].observed_at - ordered[0].observed_at
    if duration < required_duration:
        reasons.append("INSUFFICIENT_DURATION")
    return GateResult(not reasons, duration, tuple(dict.fromkeys(reasons)))
