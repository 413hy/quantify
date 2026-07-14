from __future__ import annotations

import base64
import copy
import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ai_quant.binance_egress.startup import (
    StartupEvidenceExpectation,
    StartupEvidenceMonitor,
    issue_startup_evidence,
    load_attestation_private_key,
    publish_startup_evidence,
    startup_measurement_hash,
    verify_startup_evidence,
)
from ai_quant.rate_budget.authorization import (
    AuthorizationDenied,
    RuntimeTrustBundle,
    canonical_digest,
    verify_runtime_trust_bundle,
)
from tests.unit.test_authorization import _signed_policy

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 7, 14, tzinfo=UTC)


def _signed_ready(
    tmp_path: Path,
) -> tuple[dict[str, Any], RuntimeTrustBundle, Ed25519PrivateKey]:
    del tmp_path
    signer = Ed25519PrivateKey.generate()
    bundle_document, keyring, keyring_hash, _ = _signed_policy(
        attestation_signer=signer
    )
    bundle = verify_runtime_trust_bundle(
        bundle_document,
        keyring,
        expected_keyring_hash=keyring_hash,
        now=NOW,
    )
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
        "key_id": "host-attestation-2026q3",
        "signed_at": "2026-07-14T00:00:00Z",
        "nonce": "startup-signature-nonce-0001",
        "signature_base64": base64.b64encode(signer.sign(digest)).decode(),
    }
    return evidence, bundle, signer


def _expectation(content: dict[str, Any]) -> StartupEvidenceExpectation:
    database = content["database_authority"]
    return StartupEvidenceExpectation(
        measurement_hash=startup_measurement_hash(content),
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
    evidence, bundle, _ = _signed_ready(tmp_path)
    verified = verify_startup_evidence(
        evidence,
        bundle,
        expectation=_expectation(evidence["content"]),
        now=NOW,
    )
    assert verified.content_hash == evidence["evidence_hash"]


def test_startup_evidence_from_other_boot_is_rejected(tmp_path: Path) -> None:
    evidence, bundle, _ = _signed_ready(tmp_path)
    expectation = _expectation(evidence["content"])
    changed = replace(expectation, host_boot_id="different-host-boot-0001")
    with pytest.raises(AuthorizationDenied, match="STARTUP_EVIDENCE_BINDING_MISMATCH"):
        verify_startup_evidence(
            evidence,
            bundle,
            expectation=changed,
            now=NOW,
        )


@pytest.mark.parametrize(
    ("section", "field", "wrong_value"),
    [
        ("database_authority", "migration_head", "0009_wrong_head"),
        ("database_authority", "wal_recovery_point", "wrong-wal-point-0001"),
        ("sockets", "rate_allocator", None),
        ("network_boundary", "effective_policy_hash", "f" * 64),
        ("nonce_permit_integrity", "integrity_query_hash", "e" * 64),
    ],
)
def test_signed_but_locally_mismatched_measurements_are_rejected(
    tmp_path: Path,
    section: str,
    field: str,
    wrong_value: Any,
) -> None:
    evidence, bundle, signer = _signed_ready(tmp_path)
    expectation = _expectation(evidence["content"])
    changed = copy.deepcopy(evidence)
    if section == "sockets":
        changed["content"][section][field]["inode"] += 1
    else:
        changed["content"][section][field] = wrong_value
    digest = canonical_digest(changed["content"])
    changed["evidence_hash"] = digest.hex()
    changed["signature"]["signature_base64"] = base64.b64encode(
        signer.sign(digest)
    ).decode()
    with pytest.raises(AuthorizationDenied, match="STARTUP_EVIDENCE_BINDING_MISMATCH"):
        verify_startup_evidence(
            changed,
            bundle,
            expectation=expectation,
            now=NOW,
        )


def test_startup_evidence_signed_by_an_untrusted_key_is_rejected(
    tmp_path: Path,
) -> None:
    evidence, bundle, _ = _signed_ready(tmp_path)
    forged = copy.deepcopy(evidence)
    wrong_signer = Ed25519PrivateKey.generate()
    forged["signature"]["signature_base64"] = base64.b64encode(
        wrong_signer.sign(canonical_digest(forged["content"]))
    ).decode()
    with pytest.raises(AuthorizationDenied, match="STARTUP_EVIDENCE_SIGNATURE_INVALID"):
        verify_startup_evidence(
            forged,
            bundle,
            expectation=_expectation(forged["content"]),
            now=NOW,
        )


def test_issuer_signs_only_a_draft_that_reverifies_with_attestation_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence, bundle, signer = _signed_ready(tmp_path)
    signer_policy = bundle.attestation_signers["host-attestation-2026q3"]
    monkeypatch.setattr(
        "ai_quant.binance_egress.startup.os.geteuid",
        lambda: signer_policy.holder_uid,
    )
    monkeypatch.setattr(
        "ai_quant.binance_egress.startup.os.getegid",
        lambda: signer_policy.holder_gid,
    )
    issued = issue_startup_evidence(
        evidence["content"],
        bundle,
        signer,
        key_id="host-attestation-2026q3",
        nonce="startup-issuance-nonce-0001",
        expectation=_expectation(evidence["content"]),
        evidence_schema_path=ROOT / "contracts/host-rate-startup-evidence.schema.json",
        now=NOW,
    )
    assert issued["evidence_hash"] == canonical_digest(issued["content"]).hex()


def test_issuer_rejects_a_process_outside_the_frozen_holder_identity(
    tmp_path: Path,
) -> None:
    evidence, bundle, signer = _signed_ready(tmp_path)
    with pytest.raises(AuthorizationDenied, match="ATTESTATION_SIGNER_NOT_AUTHORIZED"):
        issue_startup_evidence(
            evidence["content"],
            bundle,
            signer,
            key_id="host-attestation-2026q3",
            nonce="startup-issuance-nonce-0001",
            expectation=_expectation(evidence["content"]),
            evidence_schema_path=ROOT
            / "contracts/host-rate-startup-evidence.schema.json",
            now=NOW,
        )


def test_private_attestation_key_must_be_owner_only_and_outside_repo(
    tmp_path: Path,
) -> None:
    signer = Ed25519PrivateKey.generate()
    key_path = tmp_path / "attestation-key.pem"
    key_path.write_bytes(
        signer.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o400)
    loaded = load_attestation_private_key(
        key_path,
        forbidden_repository_root=ROOT,
    )
    assert loaded.public_key().public_bytes_raw() == signer.public_key().public_bytes_raw()
    key_path.chmod(0o440)
    with pytest.raises(AuthorizationDenied, match="ATTESTATION_PRIVATE_KEY_UNSAFE"):
        load_attestation_private_key(key_path, forbidden_repository_root=ROOT)


def test_atomic_publication_and_monitor_reverify_the_current_file(
    tmp_path: Path,
) -> None:
    evidence, bundle, _ = _signed_ready(tmp_path)
    key_id = "host-attestation-2026q3"
    local_policy = replace(
        bundle.attestation_signers[key_id],
        holder_uid=os.geteuid(),
        holder_gid=os.getegid(),
    )
    local_bundle = replace(bundle, attestation_signers={key_id: local_policy})
    output_directory = tmp_path / "attestation"
    output_directory.mkdir()
    output_directory.chmod(0o2775)
    output = output_directory / "host-rate-startup-evidence.json"
    expectation = _expectation(evidence["content"])
    publish_startup_evidence(
        evidence,
        output,
        local_bundle,
        expectation=expectation,
        evidence_schema_path=ROOT / "contracts/host-rate-startup-evidence.schema.json",
        forbidden_repository_root=ROOT,
        now=NOW,
    )
    assert output.stat().st_mode & 0o7777 == 0o444
    monitor = StartupEvidenceMonitor(
        output,
        ROOT / "contracts/host-rate-startup-evidence.schema.json",
        local_bundle,
    )
    assert monitor.require_ready(
        expectation=expectation,
        now=NOW,
    ).content_hash == evidence["evidence_hash"]
    with pytest.raises(AuthorizationDenied, match="STARTUP_EVIDENCE_BINDING_MISMATCH"):
        monitor.require_ready(
            expectation=replace(expectation, measurement_hash="0" * 64),
            now=NOW,
        )
    with pytest.raises(AuthorizationDenied, match="STARTUP_EVIDENCE_BINDING_MISMATCH"):
        monitor.require_ready(
            expectation=expectation,
            now=datetime(2026, 7, 14, 0, 5, tzinfo=UTC),
        )
