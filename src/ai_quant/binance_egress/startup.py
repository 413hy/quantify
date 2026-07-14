"""Signed startup-evidence verification shared by the gateway activation gate."""

from __future__ import annotations

import base64
import binascii
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jsonschema import Draft202012Validator, FormatChecker

from ai_quant.common.config import validate_config
from ai_quant.rate_budget.authorization import (
    AuthorizationDenied,
    RuntimeTrustBundle,
    VerifiedSignedDocument,
    canonical_digest,
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


def load_attestation_private_key(
    path: Path,
    *,
    forbidden_repository_root: Path,
) -> Ed25519PrivateKey:
    """Load an owner-only Ed25519 PEM key outside the repository tree."""
    if path.is_symlink():
        raise AuthorizationDenied("ATTESTATION_PRIVATE_KEY_UNSAFE")
    metadata = path.stat()
    resolved = path.resolve()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o400
        or metadata.st_uid != os.geteuid()
        or resolved.is_relative_to(forbidden_repository_root.resolve())
    ):
        raise AuthorizationDenied("ATTESTATION_PRIVATE_KEY_UNSAFE")
    try:
        loaded = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except (ValueError, TypeError) as exc:
        raise AuthorizationDenied("ATTESTATION_PRIVATE_KEY_INVALID") from exc
    if not isinstance(loaded, Ed25519PrivateKey):
        raise AuthorizationDenied("ATTESTATION_PRIVATE_KEY_INVALID")
    return loaded


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
    trust_bundle: RuntimeTrustBundle,
    *,
    expectation: StartupEvidenceExpectation,
    now: datetime,
) -> VerifiedSignedDocument:
    content = document.get("content")
    if not isinstance(content, Mapping) or content.get("evidence_status") != "SIGNED_READY":
        raise AuthorizationDenied("STARTUP_EVIDENCE_INVALID")
    digest = canonical_digest(content)
    if document.get("evidence_hash") != digest.hex():
        raise AuthorizationDenied("STARTUP_EVIDENCE_HASH_MISMATCH")
    signature = document.get("signature")
    if not isinstance(signature, Mapping) or signature.get("algorithm") != "Ed25519":
        raise AuthorizationDenied("STARTUP_EVIDENCE_SIGNATURE_INVALID")
    key_id = signature.get("key_id")
    if not isinstance(key_id, str):
        raise AuthorizationDenied("STARTUP_EVIDENCE_SIGNATURE_INVALID")
    signer = trust_bundle.attestation_signers.get(key_id)
    if signer is None:
        raise AuthorizationDenied("STARTUP_EVIDENCE_SIGNATURE_INVALID")
    signed_at = _time(signature.get("signed_at"))
    encoded_signature = signature.get("signature_base64")
    if not isinstance(encoded_signature, str):
        raise AuthorizationDenied("STARTUP_EVIDENCE_SIGNATURE_INVALID")
    try:
        signature_bytes = base64.b64decode(encoded_signature, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise AuthorizationDenied("STARTUP_EVIDENCE_SIGNATURE_INVALID") from exc
    utc_now = now.astimezone(UTC)
    if (
        len(signature_bytes) != 64
        or not trust_bundle.issued_at <= signed_at <= utc_now < trust_bundle.valid_until
        or not signer.not_before <= signed_at <= utc_now < signer.not_after
    ):
        raise AuthorizationDenied("STARTUP_EVIDENCE_SIGNATURE_INVALID")
    try:
        signer.public_key.verify(signature_bytes, digest)
    except InvalidSignature as exc:
        raise AuthorizationDenied("STARTUP_EVIDENCE_SIGNATURE_INVALID") from exc
    verified = VerifiedSignedDocument(
        content=content,
        content_hash=digest.hex(),
        signed_at=signed_at,
    )
    issued_at = _time(content.get("issued_at"))
    expires_at = _time(content.get("expires_at"))
    if (
        not issued_at <= verified.signed_at <= utc_now < expires_at
        or expires_at - issued_at
        > timedelta(seconds=signer.max_evidence_ttl_seconds)
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
    if (
        not isinstance(observations, list)
        or not all(isinstance(item, Mapping) for item in observations)
        or len(observations) != len(expectation.enabled_authorities)
        or {item.get("endpoint_authority") for item in observations}
        != set(expectation.enabled_authorities)
    ):
        raise AuthorizationDenied("STARTUP_EVIDENCE_OBSERVATIONS_INVALID")
    if any(
        not issued_at <= _time(item.get("observed_at")) <= utc_now
        for item in observations
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


def issue_startup_evidence(
    content: Mapping[str, Any],
    trust_bundle: RuntimeTrustBundle,
    signer_key: Ed25519PrivateKey,
    *,
    key_id: str,
    nonce: str,
    expectation: StartupEvidenceExpectation,
    evidence_schema_path: Path,
    now: datetime,
) -> Mapping[str, Any]:
    """Sign only a schema-valid READY draft that re-verifies against local expectations."""
    signer = trust_bundle.attestation_signers.get(key_id)
    utc_now = now.astimezone(UTC)
    if (
        signer is None
        or not trust_bundle.issued_at <= utc_now < trust_bundle.valid_until
        or not signer.not_before <= utc_now < signer.not_after
        or os.geteuid() != signer.holder_uid
        or os.getegid() != signer.holder_gid
    ):
        raise AuthorizationDenied("ATTESTATION_SIGNER_NOT_AUTHORIZED")
    digest = canonical_digest(content)
    document: Mapping[str, Any] = {
        "schema_version": "1.0.0",
        "evidence_hash": digest.hex(),
        "content": dict(content),
        "signature": {
            "algorithm": "Ed25519",
            "key_id": key_id,
            "signed_at": utc_now.isoformat().replace("+00:00", "Z"),
            "nonce": nonce,
            "signature_base64": base64.b64encode(signer_key.sign(digest)).decode(),
        },
    }
    schema = json.loads(evidence_schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    if list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document)):
        raise AuthorizationDenied("STARTUP_EVIDENCE_SCHEMA_INVALID")
    verify_startup_evidence(
        document,
        trust_bundle,
        expectation=expectation,
        now=utc_now,
    )
    return document


def load_startup_evidence(
    evidence_path: Path,
    evidence_schema_path: Path,
    trust_bundle: RuntimeTrustBundle,
    *,
    expectation: StartupEvidenceExpectation,
    now: datetime,
) -> VerifiedSignedDocument:
    evidence = validate_config(evidence_path, evidence_schema_path)
    if not isinstance(evidence, dict):
        raise AuthorizationDenied("STARTUP_EVIDENCE_INVALID")
    return verify_startup_evidence(
        evidence,
        trust_bundle,
        expectation=expectation,
        now=now,
    )
