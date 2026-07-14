import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from ai_quant.binance_egress.gateway import GatewayDenied, request_document_hash, send_once
from ai_quant.contracts.models import ClosedGatewayRequest, ConsumeGranted

H = "a" * 64


def make_request(
    now: datetime, *, host: str = "fapi.binance.com", environment: str = "production"
) -> ClosedGatewayRequest:
    return ClosedGatewayRequest.model_validate(
        {
            "schema_version": "1.0.0",
            "request_id": "gateway-request-1",
            "created_at": now,
            "expires_at": now + timedelta(seconds=1),
            "subject_caller_service": "execution-service",
            "subject_caller_instance_id": "execution-1",
            "environment": environment,
            "endpoint_authority": "BINANCE_PRODUCTION_FAPI",
            "endpoint_id": "REST_QUERY_TIME",
            "endpoint_catalog_hash": H,
            "endpoint_request_schema_hash": H,
            "gateway_connection_id": None,
            "request_kind": "STRUCTURED_UNSIGNED",
            "contains_sensitive_material": False,
            "transport": "REST",
            "method": "GET",
            "scheme": "https",
            "host": host,
            "port": 443,
            "path": "/fapi/v1/time",
            "parameters": [],
            "body_base64": None,
            "websocket_frame": None,
            "immutable_wire_bytes_base64": None,
            "parameter_hash": H,
            "canonical_request_hash": H,
            "wire_bytes_hash": H,
            "persistence_allowed": False,
            "logging_allowed": False,
        }
    )


def make_grant(request: ClosedGatewayRequest, now: datetime) -> ConsumeGranted:
    return ConsumeGranted(
        permit_id="permit-1",
        fencing_epoch=1,
        send_deadline=now + timedelta(milliseconds=50),
        canonical_request_hash=request.canonical_request_hash,
        gateway_derived_parameter_hash=request.parameter_hash,
        gateway_derived_wire_bytes_hash=request.wire_bytes_hash,
        gateway_derived_operation_facts_hash=H,
        causal_capability_payload_hash=H,
        gateway_request_document_hash=request_document_hash(request),
    )


def test_gateway_invokes_transport_exactly_once() -> None:
    now = datetime(2026, 7, 14, tzinfo=UTC)
    calls: list[ClosedGatewayRequest] = []

    def transport(request: ClosedGatewayRequest) -> dict[str, Any]:
        calls.append(request)
        return {"ok": True}

    request = make_request(now)
    grant = make_grant(request, now)
    assert send_once(request, grant, transport, now=now) == {"ok": True}
    assert len(calls) == 1


def test_pydantic_gateway_model_conforms_to_immutable_json_schema() -> None:
    now = datetime(2026, 7, 14, tzinfo=UTC)
    document = make_request(now).model_dump(mode="json")
    root = Path(__file__).resolve().parents[2]
    schema = json.loads(
        (root / "contracts/binance-gateway-request.schema.json").read_text(encoding="utf-8")
    )
    errors = list(Draft202012Validator(schema).iter_errors(document))
    assert [error.message for error in errors] == []


def test_non_allowlisted_production_host_never_calls_transport() -> None:
    now = datetime(2026, 7, 14, tzinfo=UTC)
    calls = 0

    def transport(_: ClosedGatewayRequest) -> None:
        nonlocal calls
        calls += 1

    request = make_request(now, host="example.com")
    grant = make_grant(request, now)
    with pytest.raises(GatewayDenied, match="DESTINATION_NOT_ALLOWLISTED"):
        send_once(request, grant, transport, now=now)
    assert calls == 0


def test_testnet_is_frozen_pending_endpoint_adr() -> None:
    now = datetime(2026, 7, 14, tzinfo=UTC)
    request = make_request(now, host="demo-fstream.binance.com", environment="testnet")
    grant = make_grant(request, now)
    with pytest.raises(GatewayDenied, match="TESTNET_ENDPOINT_BASELINE_CONFLICT"):
        send_once(request, grant, lambda _: None, now=now)


def test_grant_for_different_document_never_calls_transport() -> None:
    now = datetime(2026, 7, 14, tzinfo=UTC)
    request = make_request(now)
    grant = make_grant(request, now).model_copy(update={"gateway_request_document_hash": "b" * 64})
    calls = 0

    def transport(_: ClosedGatewayRequest) -> None:
        nonlocal calls
        calls += 1

    with pytest.raises(GatewayDenied, match="CONSUME_GRANT_BINDING_MISMATCH"):
        send_once(request, grant, transport, now=now)
    assert calls == 0
