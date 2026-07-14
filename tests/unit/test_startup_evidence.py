from __future__ import annotations

import base64
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ai_quant.binance_egress.startup import (
    StartupEvidenceExpectation,
    verify_startup_evidence,
)
from ai_quant.rate_budget.authorization import AuthorizationDenied, canonical_digest
from tests.unit.test_endpoint_policy import _signed_documents

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 7, 14, tzinfo=UTC)


def _signed_ready(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any], str]:
    _, keyring, keyring_hash, signer = _signed_documents(tmp_path)
    evidence = json.loads(
        (ROOT / "contracts/examples/host-rate-startup-evidence.json").read_text(
            encoding="utf-8"
        )
    )
    content = evidence["content"]
    content["evidence_status"] = "SIGNED_READY"
    content["issued_at"] = "2026-07-13T23:59:00Z"
    content["expires_at"] = "2026-07-14T00:04:00Z"
    for observation in content["authority_observations"]:
        observation["observed_at"] = "2026-07-14T00:00:00Z"
    digest = canonical_digest(content)
    evidence["evidence_hash"] = digest.hex()
    evidence["signature"] = {
        "algorithm": "Ed25519",
        "key_id": "host-config-signing-test",
        "signed_at": "2026-07-14T00:00:00Z",
        "nonce": "startup-signature-nonce-0001",
        "signature_base64": base64.b64encode(signer.sign(digest)).decode(),
    }
    return evidence, keyring, keyring_hash


def _expectation(content: dict[str, Any]) -> StartupEvidenceExpectation:
    database = content["database_authority"]
    return StartupEvidenceExpectation(
        stage=content["stage"],
        enabled_environments=frozenset(content["enabled_environments"]),
        enabled_authorities=frozenset(content["enabled_authorities"]),
        host_boot_id=content["host_boot_id"],
        fencing_epoch=database["fencing_epoch"],
        fencing_owner_instance_id=database["fencing_owner_instance_id"],
        artifact_binding=content["artifact_binding"],
        release_binding=content["release_binding"],
    )


def test_signed_startup_evidence_is_exactly_bound_and_short_lived(tmp_path: Path) -> None:
    evidence, keyring, keyring_hash = _signed_ready(tmp_path)
    verified = verify_startup_evidence(
        evidence,
        keyring,
        expected_keyring_hash=keyring_hash,
        expectation=_expectation(evidence["content"]),
        now=NOW,
    )
    assert verified.content_hash == evidence["evidence_hash"]


def test_startup_evidence_from_other_boot_is_rejected(tmp_path: Path) -> None:
    evidence, keyring, keyring_hash = _signed_ready(tmp_path)
    expectation = _expectation(evidence["content"])
    changed = replace(expectation, host_boot_id="different-host-boot-0001")
    with pytest.raises(AuthorizationDenied, match="STARTUP_EVIDENCE_BINDING_MISMATCH"):
        verify_startup_evidence(
            evidence,
            keyring,
            expected_keyring_hash=keyring_hash,
            expectation=changed,
            now=NOW,
        )
