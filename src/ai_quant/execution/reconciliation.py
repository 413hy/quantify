"""Evidence-based resolution of an UNKNOWN order after a visibility window."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum


class ReconciliationDecision(StrEnum):
    EXISTS = "EXISTS"
    NOT_FOUND_CONFIRMED = "NOT_FOUND_CONFIRMED"
    KEEP_RECONCILING = "KEEP_RECONCILING"
    FLATTEN_AND_LOCK = "FLATTEN_AND_LOCK"


@dataclass(frozen=True, slots=True)
class ReconciliationEvidence:
    queried_at: datetime
    order_query_found: bool
    open_orders_found: bool
    recent_trade_found: bool
    position_increased: bool
    user_stream_found: bool
    all_queries_succeeded: bool


def reconcile_unknown(
    unknown_since: datetime,
    evidence: ReconciliationEvidence,
    *,
    visibility_window: timedelta = timedelta(seconds=5),
) -> ReconciliationDecision:
    for value in (unknown_since, evidence.queried_at):
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("reconciliation time must be timezone-aware UTC")
    if any(
        (
            evidence.order_query_found,
            evidence.open_orders_found,
            evidence.recent_trade_found,
            evidence.position_increased,
            evidence.user_stream_found,
        )
    ):
        return ReconciliationDecision.EXISTS
    elapsed = evidence.queried_at - unknown_since
    if elapsed < visibility_window:
        return ReconciliationDecision.KEEP_RECONCILING
    if evidence.all_queries_succeeded:
        return ReconciliationDecision.NOT_FOUND_CONFIRMED
    return ReconciliationDecision.FLATTEN_AND_LOCK
