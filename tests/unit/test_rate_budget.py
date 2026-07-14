from datetime import UTC, datetime, timedelta

from ai_quant.contracts.models import ConsumeDenied, ConsumeGranted, PermitConsumeRequest
from ai_quant.rate_budget.permit import PermitRecord, PermitStatus, consume_permit

HASHES = [f"{value:064x}" for value in range(1, 6)]


def make_record(now: datetime) -> PermitRecord:
    return PermitRecord(
        permit_id="permit-1",
        canonical_request_hash=HASHES[0],
        parameter_hash=HASHES[1],
        wire_bytes_hash=HASHES[2],
        operation_facts_hash=HASHES[3],
        capability_payload_hash=HASHES[4],
        gateway_request_document_hash="6" * 64,
        fencing_epoch=7,
        expires_at=now + timedelta(seconds=1),
    )


def make_request(now: datetime, **changes: object) -> PermitConsumeRequest:
    values: dict[str, object] = {
        "permit_id": "permit-1",
        "canonical_request_hash": HASHES[0],
        "parameter_hash": HASHES[1],
        "wire_bytes_hash": HASHES[2],
        "operation_facts_hash": HASHES[3],
        "capability_payload_hash": HASHES[4],
        "gateway_request_document_hash": "6" * 64,
        "fencing_epoch": 7,
        "occurred_at": now,
    }
    values.update(changes)
    return PermitConsumeRequest.model_validate(values)


def test_consume_is_one_shot() -> None:
    now = datetime(2026, 7, 14, tzinfo=UTC)
    first = consume_permit(make_record(now), make_request(now))
    assert first.permit.status is PermitStatus.CONSUMED
    assert isinstance(first.decision, ConsumeGranted)
    second = consume_permit(first.permit, make_request(now))
    assert isinstance(second.decision, ConsumeDenied)
    assert second.decision.reason_code == "PERMIT_NOT_RESERVED"


def test_hash_mismatch_never_consumes() -> None:
    now = datetime(2026, 7, 14, tzinfo=UTC)
    result = consume_permit(make_record(now), make_request(now, wire_bytes_hash="f" * 64))
    assert result.permit.status is PermitStatus.RESERVED
    assert isinstance(result.decision, ConsumeDenied)
    assert result.decision.reason_code == "WIRE_HASH_MISMATCH"


def test_expired_permit_is_denied() -> None:
    now = datetime(2026, 7, 14, tzinfo=UTC)
    record = make_record(now)
    result = consume_permit(record, make_request(now + timedelta(seconds=2)))
    assert result.permit.status is PermitStatus.EXPIRED
    assert isinstance(result.decision, ConsumeDenied)
