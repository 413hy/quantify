"""Property checks for the one-shot permit boundary."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from ai_quant.contracts.models import PermitConsumeRequest
from ai_quant.rate_budget.permit import PermitRecord, PermitStatus, consume_permit

SHA256 = st.text(alphabet="0123456789abcdef", min_size=64, max_size=64)


def permit(now: datetime) -> PermitRecord:
    return PermitRecord(
        permit_id="permit-1",
        canonical_request_hash="a" * 64,
        parameter_hash="b" * 64,
        wire_bytes_hash="c" * 64,
        operation_facts_hash="d" * 64,
        capability_payload_hash="e" * 64,
        gateway_request_document_hash="f" * 64,
        fencing_epoch=7,
        expires_at=now + timedelta(seconds=1),
    )


def request(now: datetime) -> PermitConsumeRequest:
    return PermitConsumeRequest(
        permit_id="permit-1",
        canonical_request_hash="a" * 64,
        parameter_hash="b" * 64,
        wire_bytes_hash="c" * 64,
        operation_facts_hash="d" * 64,
        capability_payload_hash="e" * 64,
        gateway_request_document_hash="f" * 64,
        fencing_epoch=7,
        occurred_at=now,
    )


@given(
    field=st.sampled_from(
        [
            "canonical_request_hash",
            "parameter_hash",
            "wire_bytes_hash",
            "operation_facts_hash",
            "capability_payload_hash",
            "gateway_request_document_hash",
        ]
    ),
    replacement_hash=SHA256.filter(
        lambda value: value not in {character * 64 for character in "abcdef"}
    ),
)
def test_any_hash_binding_change_denies_without_consuming(
    field: str, replacement_hash: str
) -> None:
    now = datetime(2026, 7, 14, tzinfo=UTC)
    original = permit(now)
    changed = request(now).model_copy(update={field: replacement_hash})

    result = consume_permit(original, changed)

    assert result.decision.decision == "CONSUME_DENIED"
    assert result.permit == original
    assert result.permit.status is PermitStatus.RESERVED


@given(replays=st.integers(min_value=1, max_value=20))
def test_consumed_permit_never_grants_again(replays: int) -> None:
    now = datetime(2026, 7, 14, tzinfo=UTC)
    first = consume_permit(permit(now), request(now))
    assert first.decision.decision == "CONSUME_GRANTED"

    current = first.permit
    for _ in range(replays):
        replay = consume_permit(current, request(now))
        assert replay.decision.decision == "CONSUME_DENIED"
        assert replay.permit.status is PermitStatus.CONSUMED
        current = replay.permit


@given(offset_microseconds=st.integers(min_value=0, max_value=5_000_000))
def test_expired_permit_never_grants(offset_microseconds: int) -> None:
    now = datetime(2026, 7, 14, tzinfo=UTC)
    expired = replace(permit(now), expires_at=now - timedelta(microseconds=offset_microseconds))

    result = consume_permit(expired, request(now))

    assert result.decision.decision == "CONSUME_DENIED"
    assert result.permit.status is PermitStatus.EXPIRED
