from __future__ import annotations

from datetime import timedelta

from ai_quant.execution.reconciliation import (
    ReconciliationDecision,
    ReconciliationEvidence,
    reconcile_unknown,
)
from tests.market_fixtures import BASE_TIME


def test_failed_queries_after_five_seconds_require_flatten_and_lock() -> None:
    evidence = ReconciliationEvidence(
        queried_at=BASE_TIME + timedelta(seconds=5),
        order_query_found=False,
        open_orders_found=False,
        recent_trade_found=False,
        position_increased=False,
        user_stream_found=False,
        all_queries_succeeded=False,
    )
    assert reconcile_unknown(BASE_TIME, evidence) is ReconciliationDecision.FLATTEN_AND_LOCK


def test_any_late_fill_evidence_prevents_duplicate_resubmission() -> None:
    evidence = ReconciliationEvidence(
        queried_at=BASE_TIME + timedelta(seconds=10),
        order_query_found=False,
        open_orders_found=False,
        recent_trade_found=True,
        position_increased=True,
        user_stream_found=False,
        all_queries_succeeded=True,
    )
    assert reconcile_unknown(BASE_TIME, evidence) is ReconciliationDecision.EXISTS
