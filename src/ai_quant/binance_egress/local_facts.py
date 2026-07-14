"""Assemble startup evidence only from root-authenticated local measurements."""

from __future__ import annotations

import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ai_quant.binance_egress.startup import (
    StartupEvidenceExpectation,
    startup_measurement_hash,
)
from ai_quant.common.artifacts import (
    ArtifactBindingSource,
    ArtifactHashMode,
    verify_artifact_bindings,
)
from ai_quant.common.config import ConfigurationError, load_strict_document
from ai_quant.rate_budget.authorization import (
    AuthorizationDenied,
    assert_root_owned_0444,
    canonical_digest,
)

_ID = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_MEASURED_FIELDS = frozenset(
    {
        "stage",
        "enabled_environments",
        "enabled_authorities",
        "host_boot_id",
        "release_binding",
        "artifact_binding",
        "database_authority",
        "sockets",
        "network_boundary",
        "authority_observations",
        "nonce_permit_integrity",
        "bootstrap_chain",
        "readiness",
    }
)
_SOCKET_PATHS = {
    "rate_allocator": "/run/ai-quant-rate/rate.sock",
    "binance_gateway": "/run/ai-quant-egress/gateway.sock",
}
_RELEASE_HASH_FIELDS = frozenset(
    {
        "host_control_release_manifest_hash",
        "rate_allocator_compose_hash",
        "gateway_compose_hash",
        "gateway_config_hash",
    }
)


@dataclass(frozen=True, slots=True)
class LocalFactsExpectation:
    stage: str
    enabled_environments: frozenset[str]
    enabled_authorities: frozenset[str]
    migration_head: str
    host_boot_id_path: Path
    artifact_sources: Mapping[str, ArtifactBindingSource]
    release_sources: Mapping[str, ArtifactBindingSource]
    approved_artifact_roots: Sequence[Path]
    socket_sources: Mapping[str, Path]
    peer_acl_hashes: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class LocalFactsPlan:
    facts_path: Path
    trusted_facts_directory: Path
    expectation: LocalFactsExpectation


def _absolute_path(value: object) -> Path:
    if not isinstance(value, str):
        raise AuthorizationDenied("LOCAL_FACTS_PLAN_INVALID")
    path = Path(value)
    if not path.is_absolute():
        raise AuthorizationDenied("LOCAL_FACTS_PLAN_INVALID")
    return path


def _unique_strings(value: object) -> frozenset[str]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) for item in value)
        or len(set(value)) != len(value)
    ):
        raise AuthorizationDenied("LOCAL_FACTS_PLAN_INVALID")
    return frozenset(value)


def _artifact_sources(value: object) -> Mapping[str, ArtifactBindingSource]:
    raw = _mapping(value)
    sources: dict[str, ArtifactBindingSource] = {}
    for name, source_value in raw.items():
        source = _mapping(source_value)
        if not isinstance(name, str) or set(source) != {"path", "hash_mode"}:
            raise AuthorizationDenied("LOCAL_FACTS_PLAN_INVALID")
        hash_mode_value = source.get("hash_mode")
        if not isinstance(hash_mode_value, str):
            raise AuthorizationDenied("LOCAL_FACTS_PLAN_INVALID")
        try:
            hash_mode = ArtifactHashMode(hash_mode_value)
        except ValueError as exc:
            raise AuthorizationDenied("LOCAL_FACTS_PLAN_INVALID") from exc
        sources[name] = ArtifactBindingSource(
            path=_absolute_path(source.get("path")),
            hash_mode=hash_mode,
        )
    if not sources:
        raise AuthorizationDenied("LOCAL_FACTS_PLAN_INVALID")
    return sources


def _path_mapping(value: object) -> Mapping[str, Path]:
    raw = _mapping(value)
    if not all(isinstance(name, str) for name in raw):
        raise AuthorizationDenied("LOCAL_FACTS_PLAN_INVALID")
    return {name: _absolute_path(path) for name, path in raw.items()}


def load_local_facts_plan(
    plan_path: Path,
    *,
    trusted_plan_directory: Path,
) -> LocalFactsPlan:
    """Load the root-authorized stage, source and measurement-file plan."""
    assert_root_owned_0444(plan_path, trusted_directory=trusted_plan_directory)
    try:
        loaded = load_strict_document(plan_path)
    except ConfigurationError as exc:
        raise AuthorizationDenied("LOCAL_FACTS_PLAN_INVALID") from exc
    plan = _mapping(loaded)
    required = {
        "schema_version",
        "facts_path",
        "trusted_facts_directory",
        "stage",
        "enabled_environments",
        "enabled_authorities",
        "migration_head",
        "host_boot_id_path",
        "artifact_sources",
        "release_sources",
        "approved_artifact_roots",
        "socket_sources",
        "peer_acl_hashes",
    }
    if set(plan) != required or plan.get("schema_version") != "1.0.0":
        raise AuthorizationDenied("LOCAL_FACTS_PLAN_INVALID")
    stage = plan.get("stage")
    migration_head = plan.get("migration_head")
    roots = plan.get("approved_artifact_roots")
    peer_acl_hashes = _mapping(plan.get("peer_acl_hashes"))
    if (
        not isinstance(stage, str)
        or not isinstance(migration_head, str)
        or not _ID.fullmatch(migration_head)
        or not isinstance(roots, list)
        or not roots
        or not all(isinstance(value, str) for value in peer_acl_hashes.values())
        or not all(isinstance(name, str) for name in peer_acl_hashes)
        or any(
            len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in peer_acl_hashes.values()
        )
    ):
        raise AuthorizationDenied("LOCAL_FACTS_PLAN_INVALID")
    artifact_sources = _artifact_sources(plan.get("artifact_sources"))
    release_sources = _artifact_sources(plan.get("release_sources"))
    if set(release_sources) != _RELEASE_HASH_FIELDS:
        raise AuthorizationDenied("LOCAL_FACTS_PLAN_INVALID")
    trusted_facts_directory = _absolute_path(plan.get("trusted_facts_directory"))
    facts_path = _absolute_path(plan.get("facts_path"))
    if facts_path.parent != trusted_facts_directory:
        raise AuthorizationDenied("LOCAL_FACTS_PLAN_INVALID")
    return LocalFactsPlan(
        facts_path=facts_path,
        trusted_facts_directory=trusted_facts_directory,
        expectation=LocalFactsExpectation(
            stage=stage,
            enabled_environments=_unique_strings(plan.get("enabled_environments")),
            enabled_authorities=_unique_strings(plan.get("enabled_authorities")),
            migration_head=migration_head,
            host_boot_id_path=_absolute_path(plan.get("host_boot_id_path")),
            artifact_sources=artifact_sources,
            release_sources=release_sources,
            approved_artifact_roots=tuple(_absolute_path(root) for root in roots),
            socket_sources=_path_mapping(plan.get("socket_sources")),
            peer_acl_hashes=dict(peer_acl_hashes),
        ),
    )


def _time(value: object) -> datetime:
    if not isinstance(value, str):
        raise AuthorizationDenied("LOCAL_FACTS_TIME_INVALID")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AuthorizationDenied("LOCAL_FACTS_TIME_INVALID") from exc
    if parsed.tzinfo is None:
        raise AuthorizationDenied("LOCAL_FACTS_TIME_INVALID")
    return parsed.astimezone(UTC)


def _mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AuthorizationDenied("LOCAL_FACTS_INVALID")
    return value


def _host_boot_id(path: Path) -> str:
    if not path.is_absolute():
        raise AuthorizationDenied("LOCAL_BOOT_ID_UNSAFE")
    try:
        if path.is_symlink() or path.resolve(strict=True) != path:
            raise AuthorizationDenied("LOCAL_BOOT_ID_UNSAFE")
        value = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise AuthorizationDenied("LOCAL_BOOT_ID_UNSAFE") from exc
    if not _ID.fullmatch(value):
        raise AuthorizationDenied("LOCAL_BOOT_ID_INVALID")
    return value


def _verify_sockets(
    declared: object,
    sources: Mapping[str, Path],
    peer_acl_hashes: Mapping[str, str],
) -> None:
    socket_facts = _mapping(declared)
    if (
        set(socket_facts) != set(_SOCKET_PATHS)
        or set(sources) != set(_SOCKET_PATHS)
        or set(peer_acl_hashes) != set(_SOCKET_PATHS)
    ):
        raise AuthorizationDenied("LOCAL_SOCKET_COVERAGE_INVALID")
    for role, logical_path in _SOCKET_PATHS.items():
        source = sources[role]
        fact = _mapping(socket_facts[role])
        try:
            metadata = source.lstat()
        except OSError as exc:
            raise AuthorizationDenied("LOCAL_SOCKET_IDENTITY_INVALID") from exc
        if (
            not source.is_absolute()
            or not stat.S_ISSOCK(metadata.st_mode)
            or fact.get("path") != logical_path
            or fact.get("inode") != metadata.st_ino
            or fact.get("mode") != f"{stat.S_IMODE(metadata.st_mode):04o}"
            or fact.get("owner_uid") != metadata.st_uid
            or fact.get("owner_gid") != metadata.st_gid
            or fact.get("peer_acl_hash") != peer_acl_hashes[role]
            or fact.get("so_peercred_enforced") is not True
        ):
            raise AuthorizationDenied("LOCAL_SOCKET_IDENTITY_INVALID")


def assemble_startup_content_from_local_facts(
    facts_path: Path,
    *,
    trusted_facts_directory: Path,
    expectation: LocalFactsExpectation,
    evidence_id: str,
    now: datetime,
    ttl_seconds: int,
    maximum_facts_age_seconds: int = 5,
) -> tuple[Mapping[str, Any], StartupEvidenceExpectation]:
    """Rebuild a signable content document; never accept a caller-provided draft."""
    if (
        not _ID.fullmatch(evidence_id)
        or ttl_seconds < 1
        or ttl_seconds > 300
        or maximum_facts_age_seconds < 1
        or maximum_facts_age_seconds > 60
        or set(expectation.release_sources) != _RELEASE_HASH_FIELDS
    ):
        raise AuthorizationDenied("LOCAL_FACTS_CONFIGURATION_INVALID")
    assert_root_owned_0444(facts_path, trusted_directory=trusted_facts_directory)
    try:
        document = load_strict_document(facts_path)
    except ConfigurationError as exc:
        raise AuthorizationDenied("LOCAL_FACTS_INVALID") from exc
    outer = _mapping(document)
    if set(outer) != {"schema_version", "facts_hash", "facts"} or outer.get(
        "schema_version"
    ) != "1.0.0":
        raise AuthorizationDenied("LOCAL_FACTS_INVALID")
    facts = _mapping(outer.get("facts"))
    if set(facts) != _MEASURED_FIELDS | {"captured_at"}:
        raise AuthorizationDenied("LOCAL_FACTS_INVALID")
    if outer.get("facts_hash") != canonical_digest(facts).hex():
        raise AuthorizationDenied("LOCAL_FACTS_HASH_MISMATCH")

    utc_now = now.astimezone(UTC)
    captured_at = _time(facts.get("captured_at"))
    if not timedelta(0) <= utc_now - captured_at <= timedelta(
        seconds=maximum_facts_age_seconds
    ):
        raise AuthorizationDenied("LOCAL_FACTS_STALE")
    if (
        facts.get("stage") != expectation.stage
        or frozenset(facts.get("enabled_environments", ()))
        != expectation.enabled_environments
        or frozenset(facts.get("enabled_authorities", ()))
        != expectation.enabled_authorities
        or facts.get("host_boot_id") != _host_boot_id(expectation.host_boot_id_path)
    ):
        raise AuthorizationDenied("LOCAL_FACTS_BINDING_MISMATCH")

    artifact_binding = _mapping(facts.get("artifact_binding"))
    verify_artifact_bindings(
        artifact_binding,
        expectation.artifact_sources,
        approved_roots=expectation.approved_artifact_roots,
    )
    release_binding = _mapping(facts.get("release_binding"))
    release_hashes = {
        name: str(release_binding.get(name)) for name in expectation.release_sources
    }
    verify_artifact_bindings(
        release_hashes,
        expectation.release_sources,
        approved_roots=expectation.approved_artifact_roots,
    )
    database = _mapping(facts.get("database_authority"))
    if database.get("migration_head") != expectation.migration_head:
        raise AuthorizationDenied("LOCAL_DATABASE_BINDING_MISMATCH")
    _verify_sockets(
        facts.get("sockets"),
        expectation.socket_sources,
        expectation.peer_acl_hashes,
    )

    content: dict[str, Any] = {
        "evidence_id": evidence_id,
        "evidence_status": "SIGNED_READY",
        "issued_at": utc_now.isoformat().replace("+00:00", "Z"),
        "expires_at": (utc_now + timedelta(seconds=ttl_seconds))
        .isoformat()
        .replace("+00:00", "Z"),
    }
    content.update({field: facts[field] for field in _MEASURED_FIELDS})
    startup_expectation = StartupEvidenceExpectation(
        measurement_hash=startup_measurement_hash(content),
        stage=expectation.stage,
        enabled_environments=expectation.enabled_environments,
        enabled_authorities=expectation.enabled_authorities,
        host_boot_id=str(content["host_boot_id"]),
        fencing_epoch=int(database["fencing_epoch"]),
        fencing_owner_instance_id=str(database["fencing_owner_instance_id"]),
        artifact_binding=dict(artifact_binding),
        release_binding=dict(release_binding),
    )
    return content, startup_expectation
