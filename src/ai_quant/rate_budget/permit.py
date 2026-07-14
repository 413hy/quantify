"""One-shot permit state machine; PostgreSQL persistence is added by migrations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from ai_quant.contracts.models import ConsumeDenied, ConsumeGranted, PermitConsumeRequest


class PermitStatus(StrEnum):
    RESERVED = "RESERVED"
    CONSUMED = "CONSUMED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class PermitRecord:
    permit_id: str
    canonical_request_hash: str
    parameter_hash: str
    wire_bytes_hash: str
    operation_facts_hash: str
    capability_payload_hash: str
    gateway_request_document_hash: str
    fencing_epoch: int
    expires_at: datetime
    status: PermitStatus = PermitStatus.RESERVED


@dataclass(frozen=True, slots=True)
class ConsumeResult:
    permit: PermitRecord
    decision: ConsumeGranted | ConsumeDenied


def consume_permit(
    permit: PermitRecord,
    request: PermitConsumeRequest,
    *,
    send_window: timedelta = timedelta(milliseconds=50),
) -> ConsumeResult:
    """Atomically modeled validation: any mismatch denies and leaves the permit unchanged."""
    now = request.occurred_at.astimezone(UTC)
    if permit.status is not PermitStatus.RESERVED:
        return ConsumeResult(
            permit, ConsumeDenied(permit_id=permit.permit_id, reason_code="PERMIT_NOT_RESERVED")
        )
    if now >= permit.expires_at.astimezone(UTC):
        expired = replace(permit, status=PermitStatus.EXPIRED)
        return ConsumeResult(
            expired, ConsumeDenied(permit_id=permit.permit_id, reason_code="PERMIT_EXPIRED")
        )
    comparisons = (
        (request.permit_id, permit.permit_id, "PERMIT_ID_MISMATCH"),
        (request.canonical_request_hash, permit.canonical_request_hash, "CANONICAL_HASH_MISMATCH"),
        (request.parameter_hash, permit.parameter_hash, "PARAMETER_HASH_MISMATCH"),
        (request.wire_bytes_hash, permit.wire_bytes_hash, "WIRE_HASH_MISMATCH"),
        (
            request.operation_facts_hash,
            permit.operation_facts_hash,
            "OPERATION_FACTS_HASH_MISMATCH",
        ),
        (
            request.capability_payload_hash,
            permit.capability_payload_hash,
            "CAPABILITY_HASH_MISMATCH",
        ),
        (
            request.gateway_request_document_hash,
            permit.gateway_request_document_hash,
            "GATEWAY_DOCUMENT_HASH_MISMATCH",
        ),
        (request.fencing_epoch, permit.fencing_epoch, "FENCING_EPOCH_MISMATCH"),
    )
    for observed, expected, reason in comparisons:
        if observed != expected:
            return ConsumeResult(
                permit, ConsumeDenied(permit_id=permit.permit_id, reason_code=reason)
            )
    consumed = replace(permit, status=PermitStatus.CONSUMED)
    return ConsumeResult(
        consumed,
        ConsumeGranted(
            permit_id=permit.permit_id,
            fencing_epoch=permit.fencing_epoch,
            send_deadline=now + send_window,
            canonical_request_hash=permit.canonical_request_hash,
            gateway_derived_parameter_hash=permit.parameter_hash,
            gateway_derived_wire_bytes_hash=permit.wire_bytes_hash,
            gateway_derived_operation_facts_hash=permit.operation_facts_hash,
            causal_capability_payload_hash=permit.capability_payload_hash,
            gateway_request_document_hash=permit.gateway_request_document_hash,
        ),
    )
