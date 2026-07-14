from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from ai_quant.execution.classifier import SubmissionOutcome, classify_submission
from ai_quant.execution.orders import (
    OrderEvent,
    OrderState,
    OrderTransport,
    algo_update_event,
    client_order_id,
    project_order,
)
from ai_quant.execution.protection import evaluate_protection
from ai_quant.execution.reconciliation import (
    ReconciliationDecision,
    ReconciliationEvidence,
    reconcile_unknown,
)
from ai_quant.execution.simulator import SimulatedOrderType, simulate_fill
from tests.market_fixtures import BASE_TIME


def event(index: int, state: OrderState, filled: str = "0") -> OrderEvent:
    return OrderEvent(
        event_id=f"event-{index}",
        occurred_at=BASE_TIME + timedelta(milliseconds=index),
        state=state,
        cumulative_filled_quantity=Decimal(filled),
        order_id="order-1" if index >= 3 else None,
    )


def test_partial_fill_cancel_race_projects_final_fill_once() -> None:
    events = (
        event(0, OrderState.CREATED),
        event(1, OrderState.RISK_APPROVED),
        event(2, OrderState.SUBMITTING),
        event(3, OrderState.ACKNOWLEDGED),
        event(4, OrderState.PARTIALLY_FILLED, "0.4"),
        event(5, OrderState.CANCEL_PENDING, "0.4"),
        event(6, OrderState.FILLED, "1.0"),
    )
    projection = project_order("intent-1", OrderTransport.STANDARD, "aq-t-01", events)
    assert projection.state is OrderState.FILLED
    assert projection.cumulative_filled_quantity == Decimal("1.0")
    assert projection.event_count == 7


def test_fill_quantity_cannot_go_backwards() -> None:
    events = (
        event(0, OrderState.CREATED),
        event(1, OrderState.RISK_APPROVED),
        event(2, OrderState.SUBMITTING),
        event(3, OrderState.ACKNOWLEDGED),
        event(4, OrderState.PARTIALLY_FILLED, "0.4"),
        event(5, OrderState.PARTIALLY_FILLED, "0.3"),
    )
    with pytest.raises(ValueError, match="cannot decrease"):
        project_order("intent-1", OrderTransport.STANDARD, "aq-t-01", events)


def test_algo_trigger_without_actual_order_enters_reconciliation() -> None:
    update = algo_update_event(
        event_id="event-1",
        occurred_at=BASE_TIME,
        algo_status="TRIGGERED",
        algo_id="algo-1",
        actual_order_id="",
    )
    assert update.state is OrderState.RECONCILING
    assert update.actual_order_id is None
    assert update.reason_codes == ("EXEC_ALGO_ACTUAL_ORDER_RECONCILING",)


@pytest.mark.parametrize(
    ("status", "code", "message", "expected"),
    [
        (
            503,
            None,
            "Unknown error, please check your request or try again later.",
            SubmissionOutcome.UNKNOWN,
        ),
        (503, None, "Service Unavailable.", SubmissionOutcome.DEFINITE_FAILURE),
        (
            503,
            None,
            "Internal error; unable to process your request. Please try again.",
            SubmissionOutcome.DEFINITE_FAILURE,
        ),
        (503, -1008, "busy", SubmissionOutcome.DEFINITE_FAILURE),
        (429, None, "rate", SubmissionOutcome.DEFINITE_FAILURE),
    ],
)
def test_submission_classifier_distinguishes_unknown_from_definite_failure(
    status: int, code: int | None, message: str, expected: SubmissionOutcome
) -> None:
    assert classify_submission(http_status=status, binance_code=code, message=message) is expected


def test_unknown_never_becomes_not_found_before_visibility_window() -> None:
    evidence = ReconciliationEvidence(
        queried_at=BASE_TIME + timedelta(seconds=4),
        order_query_found=False,
        open_orders_found=False,
        recent_trade_found=False,
        position_increased=False,
        user_stream_found=False,
        all_queries_succeeded=True,
    )
    assert reconcile_unknown(BASE_TIME, evidence) is ReconciliationDecision.KEEP_RECONCILING
    assert (
        reconcile_unknown(
            BASE_TIME, replace_evidence(evidence, queried_at=BASE_TIME + timedelta(seconds=5))
        )
        is ReconciliationDecision.NOT_FOUND_CONFIRMED
    )


def replace_evidence(evidence: ReconciliationEvidence, **changes: object) -> ReconciliationEvidence:
    from dataclasses import replace

    return replace(evidence, **changes)


def test_protection_deadline_and_coverage_are_fail_closed() -> None:
    pending = evaluate_protection(
        position_quantity=Decimal("2"),
        protected_quantity=Decimal("0"),
        first_fill_at=BASE_TIME,
        now=BASE_TIME + timedelta(milliseconds=999),
        exchange_confirmed=False,
        direction_correct=True,
        reduce_only=True,
    )
    assert pending.action == "CREATE_OR_ADJUST_PROTECTION"
    failed = evaluate_protection(
        position_quantity=Decimal("2"),
        protected_quantity=Decimal("1"),
        first_fill_at=BASE_TIME,
        now=BASE_TIME + timedelta(milliseconds=1001),
        exchange_confirmed=True,
        direction_correct=True,
        reduce_only=True,
    )
    assert failed.action == "CANCEL_ENTRY_RETRY_PROTECTION_THEN_FLATTEN"


def test_maker_requires_provable_queue_consumption_and_taker_walks_book() -> None:
    maker = simulate_fill(
        side="BUY",
        quantity=Decimal("2"),
        order_type=SimulatedOrderType.MAKER,
        limit_price=Decimal("100"),
        visible_levels=(),
        queue_ahead=Decimal("5"),
        traded_at_limit_after_order=Decimal("6"),
    )
    assert maker.filled_quantity == Decimal("1")
    taker = simulate_fill(
        side="BUY",
        quantity=Decimal("3"),
        order_type=SimulatedOrderType.TAKER,
        limit_price=None,
        visible_levels=((Decimal("101"), Decimal("1")), (Decimal("102"), Decimal("2"))),
    )
    assert taker.filled_quantity == Decimal("3")
    assert taker.average_price == Decimal(305) / Decimal(3)


def test_client_ids_keep_standard_and_algo_namespaces_separate() -> None:
    assert client_order_id("testnet", "01ABC", OrderTransport.STANDARD) == "aq-t-01ABC"
    assert client_order_id("testnet", "01ABC", OrderTransport.ALGO) == "aqa-t-01ABC"
