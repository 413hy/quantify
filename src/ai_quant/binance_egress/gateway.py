"""Closed gateway enforcement without implementing a live transport."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime

import rfc8785

from ai_quant.contracts.models import ClosedGatewayRequest, ConsumeGranted

PRODUCTION_HOSTS = frozenset({"fapi.binance.com", "fstream.binance.com", "ws-fapi.binance.com"})
TESTNET_HOSTS_BLOCKED_PENDING_ADR = True


class GatewayDenied(RuntimeError):
    """Gateway refused a request before any transport call."""


def request_document_hash(request: ClosedGatewayRequest) -> str:
    payload = request.model_dump(mode="json")
    return hashlib.sha256(rfc8785.dumps(payload)).hexdigest()


def validate_destination(request: ClosedGatewayRequest) -> None:
    if request.environment == "testnet" and TESTNET_HOSTS_BLOCKED_PENDING_ADR:
        raise GatewayDenied("TESTNET_ENDPOINT_BASELINE_CONFLICT")
    if request.environment == "production" and request.host not in PRODUCTION_HOSTS:
        raise GatewayDenied("DESTINATION_NOT_ALLOWLISTED")
    if request.environment == "production" and request.scheme not in {"https", "wss"}:
        raise GatewayDenied("SCHEME_NOT_ALLOWLISTED")


def send_once[T](
    request: ClosedGatewayRequest,
    grant: ConsumeGranted,
    transport: Callable[[ClosedGatewayRequest], T],
    *,
    now: datetime,
) -> T:
    """Invoke a supplied transport once only after a matching, unexpired consume grant."""
    validate_destination(request)
    utc_now = now.astimezone(UTC)
    if request.expires_at.astimezone(UTC) <= utc_now:
        raise GatewayDenied("GATEWAY_REQUEST_EXPIRED")
    if grant.send_deadline.astimezone(UTC) <= utc_now:
        raise GatewayDenied("SEND_DEADLINE_EXPIRED")
    if not grant.permit_id:
        raise GatewayDenied("PERMIT_ID_MISSING")
    bindings = (
        (request.canonical_request_hash, grant.canonical_request_hash),
        (request.parameter_hash, grant.gateway_derived_parameter_hash),
        (request.wire_bytes_hash, grant.gateway_derived_wire_bytes_hash),
        (request_document_hash(request), grant.gateway_request_document_hash),
    )
    if any(observed != expected for observed, expected in bindings):
        raise GatewayDenied("CONSUME_GRANT_BINDING_MISMATCH")
    return transport(request)
