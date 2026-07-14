"""Signed startup-evidence verification shared by the gateway activation gate."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ai_quant.common.config import validate_config
from ai_quant.rate_budget.authorization import (
    AuthorizationDenied,
    VerifiedSignedDocument,
    assert_root_owned_0444,
    verify_signed_config_document,
)


@dataclass(frozen=True, slots=True)
class StartupEvidenceExpectation:
    stage: str
    enabled_environments: frozenset[str]
    enabled_authorities: frozenset[str]
    host_boot_id: str
    fencing_epoch: int
    fencing_owner_instance_id: str
    artifact_binding: Mapping[str, str]
    release_binding: Mapping[str, str]


def _time(value: object) -> datetime:
    if not isinstance(value, str):
        raise AuthorizationDenied("STARTUP_EVIDENCE_TIME_INVALID")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AuthorizationDenied("STARTUP_EVIDENCE_TIME_INVALID") from exc
    if parsed.tzinfo is None:
        raise AuthorizationDenied("STARTUP_EVIDENCE_TIME_INVALID")
    return parsed.astimezone(UTC)


def verify_startup_evidence(
    document: Mapping[str, Any],
    keyring: Mapping[str, Any],
    *,
    expected_keyring_hash: str,
    expectation: StartupEvidenceExpectation,
    now: datetime,
) -> VerifiedSignedDocument:
    verified = verify_signed_config_document(
        document,
        keyring,
        expected_keyring_hash=expected_keyring_hash,
        hash_field="evidence_hash",
        status_field="evidence_status",
        expected_status="SIGNED_READY",
        now=now,
    )
    content = verified.content
    issued_at = _time(content.get("issued_at"))
    expires_at = _time(content.get("expires_at"))
    utc_now = now.astimezone(UTC)
    if (
        not issued_at <= verified.signed_at <= utc_now < expires_at
        or expires_at - issued_at > timedelta(seconds=300)
        or content.get("stage") != expectation.stage
        or frozenset(content.get("enabled_environments", ()))
        != expectation.enabled_environments
        or frozenset(content.get("enabled_authorities", ()))
        != expectation.enabled_authorities
        or content.get("host_boot_id") != expectation.host_boot_id
        or content.get("artifact_binding") != expectation.artifact_binding
        or content.get("release_binding") != expectation.release_binding
    ):
        raise AuthorizationDenied("STARTUP_EVIDENCE_BINDING_MISMATCH")
    database = content.get("database_authority")
    if not isinstance(database, Mapping) or (
        database.get("fencing_epoch") != expectation.fencing_epoch
        or database.get("fencing_owner_instance_id")
        != expectation.fencing_owner_instance_id
    ):
        raise AuthorizationDenied("STARTUP_EVIDENCE_BINDING_MISMATCH")
    observations = content.get("authority_observations")
    if not isinstance(observations, list) or {
        item.get("endpoint_authority")
        for item in observations
        if isinstance(item, Mapping)
    } != set(expectation.enabled_authorities):
        raise AuthorizationDenied("STARTUP_EVIDENCE_OBSERVATIONS_INVALID")
    if any(
        not issued_at <= _time(item.get("observed_at")) <= utc_now
        for item in observations
        if isinstance(item, Mapping)
    ):
        raise AuthorizationDenied("STARTUP_EVIDENCE_OBSERVATIONS_INVALID")
    if expectation.stage == "validation" and (
        expectation.enabled_environments != {"shadow", "testnet"}
        or expectation.enabled_authorities
        != {
            "BINANCE_PRODUCTION_FAPI",
            "BINANCE_PRODUCTION_FSTREAM",
            "BINANCE_TESTNET_FAPI",
            "BINANCE_TESTNET_FSTREAM",
        }
    ):
        raise AuthorizationDenied("STARTUP_EVIDENCE_STAGE_INVALID")
    return verified


def load_startup_evidence(
    evidence_path: Path,
    evidence_schema_path: Path,
    keyring_path: Path,
    keyring_schema_path: Path,
    *,
    expected_keyring_hash: str,
    expectation: StartupEvidenceExpectation,
    now: datetime,
) -> VerifiedSignedDocument:
    assert_root_owned_0444(keyring_path)
    evidence = validate_config(evidence_path, evidence_schema_path)
    keyring = validate_config(keyring_path, keyring_schema_path)
    if not isinstance(evidence, dict) or not isinstance(keyring, dict):
        raise AuthorizationDenied("STARTUP_EVIDENCE_INVALID")
    return verify_startup_evidence(
        evidence,
        keyring,
        expected_keyring_hash=expected_keyring_hash,
        expectation=expectation,
        now=now,
    )
