"""STANDARD/ALGO order projection from immutable events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum


class OrderTransport(StrEnum):
    STANDARD = "STANDARD"
    ALGO = "ALGO"


class OrderState(StrEnum):
    CREATED = "CREATED"
    RISK_APPROVED = "RISK_APPROVED"
    SUBMITTING = "SUBMITTING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    TRIGGER_PENDING = "TRIGGER_PENDING"
    TRIGGERING = "TRIGGERING"
    TRIGGERED = "TRIGGERED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    UNKNOWN = "UNKNOWN"
    RECONCILING = "RECONCILING"
    REJECTED = "REJECTED"
    NOT_FOUND_CONFIRMED = "NOT_FOUND_CONFIRMED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    FILLED = "FILLED"


TERMINAL_STATES = {
    OrderState.REJECTED,
    OrderState.NOT_FOUND_CONFIRMED,
    OrderState.CANCELED,
    OrderState.EXPIRED,
    OrderState.FILLED,
}

_TRANSITIONS: dict[OrderState, set[OrderState]] = {
    OrderState.CREATED: {OrderState.RISK_APPROVED},
    OrderState.RISK_APPROVED: {OrderState.SUBMITTING},
    OrderState.SUBMITTING: {
        OrderState.ACKNOWLEDGED,
        OrderState.TRIGGER_PENDING,
        OrderState.REJECTED,
        OrderState.UNKNOWN,
    },
    OrderState.UNKNOWN: {
        OrderState.ACKNOWLEDGED,
        OrderState.TRIGGER_PENDING,
        OrderState.TRIGGERING,
        OrderState.TRIGGERED,
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.NOT_FOUND_CONFIRMED,
        OrderState.RECONCILING,
    },
    OrderState.RECONCILING: {
        OrderState.ACKNOWLEDGED,
        OrderState.TRIGGER_PENDING,
        OrderState.TRIGGERING,
        OrderState.TRIGGERED,
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.NOT_FOUND_CONFIRMED,
    },
    OrderState.ACKNOWLEDGED: {
        OrderState.PARTIALLY_FILLED,
        OrderState.CANCEL_PENDING,
        OrderState.EXPIRED,
        OrderState.FILLED,
    },
    OrderState.TRIGGER_PENDING: {
        OrderState.TRIGGERING,
        OrderState.TRIGGERED,
        OrderState.RECONCILING,
        OrderState.CANCEL_PENDING,
        OrderState.CANCELED,
        OrderState.EXPIRED,
        OrderState.REJECTED,
    },
    OrderState.TRIGGERING: {
        OrderState.TRIGGERED,
        OrderState.RECONCILING,
        OrderState.REJECTED,
    },
    OrderState.TRIGGERED: {
        OrderState.RECONCILING,
        OrderState.ACKNOWLEDGED,
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
    },
    OrderState.PARTIALLY_FILLED: {
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCEL_PENDING,
        OrderState.EXPIRED,
    },
    OrderState.CANCEL_PENDING: {
        OrderState.CANCELED,
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.UNKNOWN,
    },
}


@dataclass(frozen=True, slots=True)
class OrderEvent:
    event_id: str
    occurred_at: datetime
    state: OrderState
    cumulative_filled_quantity: Decimal = Decimal(0)
    order_id: str | None = None
    algo_id: str | None = None
    actual_order_id: str | None = None
    algo_status: str | None = None
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OrderProjection:
    intent_id: str
    transport: OrderTransport
    client_id: str
    state: OrderState
    cumulative_filled_quantity: Decimal
    order_id: str | None
    algo_id: str | None
    actual_order_id: str | None
    event_count: int
    reconciliation_required: bool


def project_order(
    intent_id: str,
    transport: OrderTransport,
    client_id: str,
    events: tuple[OrderEvent, ...],
) -> OrderProjection:
    if not events or events[0].state is not OrderState.CREATED:
        raise ValueError("order stream must begin with CREATED")
    if len({event.event_id for event in events}) != len(events):
        raise ValueError("duplicate order event id")
    previous = events[0]
    _validate_event(previous)
    order_id = previous.order_id
    algo_id = previous.algo_id
    actual_order_id = previous.actual_order_id
    for event in events[1:]:
        _validate_event(event)
        allowed = _TRANSITIONS.get(previous.state, set())
        if event.state not in allowed:
            raise ValueError("EXEC_ORDER_STATE_INVARIANT_BREACH")
        if event.cumulative_filled_quantity < previous.cumulative_filled_quantity:
            raise ValueError("cumulative filled quantity cannot decrease")
        if order_id and event.order_id and event.order_id != order_id:
            raise ValueError("ordinary order id changed")
        if algo_id and event.algo_id and event.algo_id != algo_id:
            raise ValueError("algo id changed")
        if actual_order_id and event.actual_order_id and event.actual_order_id != actual_order_id:
            raise ValueError("actual order id changed")
        order_id = event.order_id or order_id
        algo_id = event.algo_id or algo_id
        actual_order_id = event.actual_order_id or actual_order_id
        if transport is OrderTransport.STANDARD and (algo_id or actual_order_id):
            raise ValueError("STANDARD intent cannot bind Algo identifiers")
        if event.state is OrderState.TRIGGERED and not actual_order_id:
            raise ValueError("TRIGGERED requires actual order id")
        previous = event
    return OrderProjection(
        intent_id=intent_id,
        transport=transport,
        client_id=client_id,
        state=previous.state,
        cumulative_filled_quantity=previous.cumulative_filled_quantity,
        order_id=order_id,
        algo_id=algo_id,
        actual_order_id=actual_order_id,
        event_count=len(events),
        reconciliation_required=previous.state in {OrderState.UNKNOWN, OrderState.RECONCILING},
    )


def client_order_id(environment: str, intent_ulid: str, transport: OrderTransport) -> str:
    environment_code = {"testnet": "t", "shadow": "s", "production": "l"}.get(environment)
    if environment_code is None or not intent_ulid.isalnum():
        raise ValueError("invalid order id inputs")
    prefix = "aqa" if transport is OrderTransport.ALGO else "aq"
    value = f"{prefix}-{environment_code}-{intent_ulid}"
    if len(value) > 36:
        raise ValueError("client order id exceeds exchange maximum")
    return value


def algo_update_event(
    *,
    event_id: str,
    occurred_at: datetime,
    algo_status: str,
    algo_id: str | None,
    actual_order_id: str | None,
    cumulative_filled_quantity: Decimal = Decimal(0),
) -> OrderEvent:
    normalized_actual_id = actual_order_id or None
    state_by_status = {
        "NEW": OrderState.TRIGGER_PENDING,
        "TRIGGERING": OrderState.TRIGGERING,
        "CANCELED": OrderState.CANCELED,
        "REJECTED": OrderState.REJECTED,
        "EXPIRED": OrderState.EXPIRED,
    }
    reason_codes: tuple[str, ...] = ()
    if algo_status == "TRIGGERED":
        if normalized_actual_id:
            state = OrderState.TRIGGERED
        else:
            state = OrderState.RECONCILING
            reason_codes = ("EXEC_ALGO_ACTUAL_ORDER_RECONCILING",)
    elif algo_status == "FINISHED":
        state = OrderState.RECONCILING
        reason_codes = ("EXEC_ALGO_CHILD_RECONCILING",)
    elif algo_status in state_by_status:
        state = state_by_status[algo_status]
    else:
        state = OrderState.UNKNOWN
        reason_codes = ("EXEC_ALGO_STATUS_UNKNOWN",)
    return OrderEvent(
        event_id=event_id,
        occurred_at=occurred_at,
        state=state,
        cumulative_filled_quantity=cumulative_filled_quantity,
        algo_id=algo_id,
        actual_order_id=normalized_actual_id,
        algo_status=algo_status
        if algo_status in state_by_status or algo_status in {"TRIGGERED", "FINISHED"}
        else "UNKNOWN",
        reason_codes=reason_codes,
    )


def _validate_event(event: OrderEvent) -> None:
    if event.occurred_at.tzinfo is None or event.occurred_at.utcoffset() != UTC.utcoffset(
        event.occurred_at
    ):
        raise ValueError("order event time must be timezone-aware UTC")
    if event.cumulative_filled_quantity < 0:
        raise ValueError("filled quantity cannot be negative")
