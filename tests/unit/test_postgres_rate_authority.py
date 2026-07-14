from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from ai_quant.rate_budget.authorization import (
    AuthorizationDenied,
    PeerCredentials,
    VerifiedCapability,
    verify_runtime_trust_bundle,
)
from ai_quant.rate_budget.postgres import (
    PostgresRateAuthority,
    RuntimeCost,
    load_database_dsn,
)
from tests.unit.test_authorization import _signed_policy
from tests.unit.test_endpoint_policy import _signed_documents, _verify

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 7, 14, tzinfo=UTC)


class FakeCursor:
    def __init__(self, responses: list[tuple[dict[str, Any] | None, list[dict[str, Any]]]]) -> None:
        self._responses = responses
        self._current: tuple[dict[str, Any] | None, list[dict[str, Any]]] = (None, [])
        self.executions: list[tuple[str, object]] = []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, query: str, params: object) -> None:
        self.executions.append((query, params))
        self._current = self._responses.pop(0)

    def fetchone(self) -> dict[str, Any] | None:
        return self._current[0]

    def fetchall(self) -> list[dict[str, Any]]:
        return self._current[1]


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return self._cursor


def _example(name: str) -> dict[str, Any]:
    value = json.loads((ROOT / "contracts/examples" / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _validator() -> Draft202012Validator:
    schema = json.loads(
        (ROOT / "contracts/rate-budget-uds.schema.json").read_text(encoding="utf-8")
    )
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _authority(cursor: FakeCursor) -> PostgresRateAuthority:
    authority = PostgresRateAuthority(
        dsn="postgresql://unused",
        instance_id="rate-allocator-01",
        clock=lambda: NOW,
    )
    authority._connect = lambda: FakeConnection(cursor)  # type: ignore[method-assign]
    return authority


def test_private_dsn_file_is_required(tmp_path: Path) -> None:
    path = tmp_path / "host-dsn"
    path.write_text("postgresql://host-control", encoding="utf-8")
    path.chmod(0o600)
    assert load_database_dsn(path) == "postgresql://host-control"
    path.chmod(0o640)
    with pytest.raises(ValueError, match="RATE_DATABASE_DSN_FILE_UNSAFE"):
        load_database_dsn(path)


def test_reserve_uses_v2_and_returns_contract_complete_allocations() -> None:
    cursor = FakeCursor(
        [
            (
                {
                    "decision": "GRANTED",
                    "reason_code": "RATE_GRANTED",
                    "permit_id": "rate-permit-existing-0001",
                    "derived_operation_class": "PROTECTION_CREATE_REPLACE",
                    "fencing_epoch": 7,
                    "expires_at": NOW + timedelta(seconds=1),
                },
                [],
            ),
            (
                None,
                [
                    {
                        "budget_id": "rate-budget-0001",
                        "rate_limit_type": "REQUEST_WEIGHT",
                        "scope_key_hash": "3" * 64,
                        "interval_name": "MINUTE_1",
                        "window_start": NOW,
                        "window_end": NOW + timedelta(minutes=1),
                        "cost": 1,
                        "effective_used_before": 20,
                        "effective_used_after": 21,
                        "class_ceiling": 90,
                    }
                ],
            ),
        ]
    )
    request = _example("rate-reserve-request.json")
    capability = VerifiedCapability(
        capability_id="rate-capability-000001",
        payload_hash=request["causal_capability"]["payload_hash"],
        nonce=request["causal_capability"]["signed_payload"]["nonce"],
        issuer="RISK_AUTHORITY",
        operation_class="PROTECTION_CREATE_REPLACE",
        expires_at=NOW + timedelta(seconds=2),
    )
    response = _authority(cursor).reserve(
        request,
        capability,
        "PROTECTION_CREATE_REPLACE",
        PeerCredentials(pid=1, uid=11002, gid=11002),
    )
    assert response["decision"] == "GRANTED"
    assert response["allocations"][0]["effective_used_after"] == 21
    assert "reserve_permit_v2" in cursor.executions[0][0]
    assert list(_validator().iter_errors(response)) == []


def test_consume_uses_complete_v2_binding_and_returns_contract_decision() -> None:
    cursor = FakeCursor(
        [
            (
                {
                    "decision": "CONSUME_GRANTED",
                    "reason_code": "RATE_PERMIT_CONSUMED",
                    "send_deadline": NOW + timedelta(milliseconds=50),
                },
                [],
            )
        ]
    )
    request = _example("rate-permit-consume-request.json")
    response = _authority(cursor).handle_gateway_message(
        request, PeerCredentials(pid=1, uid=11005, gid=11005)
    )
    assert response is not None
    assert response["decision"] == "CONSUME_GRANTED"
    assert "consume_permit_v2" in cursor.executions[0][0]
    assert list(_validator().iter_errors(response)) == []


def test_one_way_gateway_event_is_recorded_without_response() -> None:
    cursor = FakeCursor(
        [
            (
                {
                    "decision": "RECORDED",
                    "reason_code": "RATE_GATEWAY_EVENT_RECORDED",
                },
                [],
            )
        ]
    )
    response = _authority(cursor).handle_gateway_message(
        _example("rate-send-outcome.json"),
        PeerCredentials(pid=1, uid=11005, gid=11005),
    )
    assert response is None
    assert "record_gateway_message" in cursor.executions[0][0]


def test_verified_catalog_is_ingested_with_exact_multiclass_runtime_shape(
    tmp_path: Path,
) -> None:
    catalog_document, keyring, keyring_hash, _ = _signed_documents(tmp_path)
    catalog = _verify(catalog_document, keyring, keyring_hash, tmp_path)
    bundle_document, bundle_keyring, bundle_keyring_hash, _ = _signed_policy()
    bundle = verify_runtime_trust_bundle(
        bundle_document,
        bundle_keyring,
        expected_keyring_hash=bundle_keyring_hash,
        now=NOW,
    )
    cursor = FakeCursor(
        [
            ({"policy_payload_hash": "inserted"}, []),
            ({"policy_payload_hash": "inserted"}, []),
        ]
    )
    costs = {
        key: {
            "HOST_RATE_CONTROL": (
                RuntimeCost(
                    scope_key_hash="3" * 64,
                    rate_limit_type="REQUEST_WEIGHT",
                    interval_name="MINUTE_1",
                    cost=1,
                    ceiling_units=2,
                ),
            )
        }
        for key in catalog.endpoints
    }
    _authority(cursor).ingest_runtime_policies(catalog, bundle, costs)
    assert len(cursor.executions) == 2
    assert all("endpoint_runtime_policies" in query for query, _ in cursor.executions)


def test_policy_ingestion_rejects_cost_not_bound_to_signed_catalog(tmp_path: Path) -> None:
    catalog_document, keyring, keyring_hash, _ = _signed_documents(tmp_path)
    catalog = _verify(catalog_document, keyring, keyring_hash, tmp_path)
    bundle_document, bundle_keyring, bundle_keyring_hash, _ = _signed_policy()
    bundle = verify_runtime_trust_bundle(
        bundle_document,
        bundle_keyring,
        expected_keyring_hash=bundle_keyring_hash,
        now=NOW,
    )
    cursor = FakeCursor([])
    costs = {
        key: {
            "HOST_RATE_CONTROL": (
                RuntimeCost(
                    scope_key_hash="3" * 64,
                    rate_limit_type="REQUEST_WEIGHT",
                    interval_name="MINUTE_1",
                    cost=2,
                    ceiling_units=2,
                ),
            )
        }
        for key in catalog.endpoints
    }
    with pytest.raises(AuthorizationDenied, match="RATE_POLICY_COST_INVALID"):
        _authority(cursor).ingest_runtime_policies(catalog, bundle, costs)
    assert cursor.executions == []
