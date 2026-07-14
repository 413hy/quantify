"""Signed endpoint-catalog semantic verification for the rate authority."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from ai_quant.common.config import validate_config
from ai_quant.rate_budget.authorization import (
    AuthorizationDenied,
    assert_root_owned_0444,
    canonical_digest,
    verify_signed_config_document,
)


@dataclass(frozen=True, slots=True)
class CostTier:
    minimum: int
    maximum: int
    cost: int


@dataclass(frozen=True, slots=True)
class CostRule:
    mode: str
    fixed_cost: int | None
    parameter_name: str | None
    tiers: tuple[CostTier, ...]


@dataclass(frozen=True, slots=True)
class EndpointPolicy:
    endpoint_id: str
    authority: str
    transport: str
    method: str
    path: str
    market_stream_role: str | None
    control_frame_type: str | None
    allowed_operation_classes: frozenset[str]
    causal_role_class_map: Mapping[str, str]
    request_weight_rule: CostRule
    order_count_rule: CostRule
    websocket_control_rule: CostRule
    connection_attempt_rule: CostRule
    parameter_policy: str
    allowed_parameter_names: frozenset[str]
    required_parameter_names: frozenset[str]
    forbidden_parameter_names: frozenset[str]
    request_schema_sha256: str
    contract_payload: Mapping[str, Any] = field(default_factory=dict)
    contract_hash: str = ""


@dataclass(frozen=True, slots=True)
class RuntimeEndpointCatalog:
    catalog_id: str
    catalog_hash: str
    checked_at: datetime
    valid_until: datetime
    endpoints: Mapping[tuple[str, str], EndpointPolicy]


def _time(value: object, reason: str) -> datetime:
    if not isinstance(value, str):
        raise AuthorizationDenied(reason)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AuthorizationDenied(reason) from exc
    if parsed.tzinfo is None:
        raise AuthorizationDenied(reason)
    return parsed.astimezone(UTC)


def _mapping(value: object, reason: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AuthorizationDenied(reason)
    return value


def _strings(value: object, reason: str, *, allow_empty: bool = False) -> frozenset[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AuthorizationDenied(reason)
    result = frozenset(value)
    if len(result) != len(value) or (not allow_empty and not result):
        raise AuthorizationDenied(reason)
    return result


def _cost_rule(value: object) -> CostRule:
    raw = _mapping(value, "ENDPOINT_COST_RULE_INVALID")
    mode = raw.get("mode")
    fixed_cost = raw.get("fixed_cost")
    parameter_name = raw.get("parameter_name")
    raw_tiers = raw.get("tiers")
    if not isinstance(mode, str) or not isinstance(raw_tiers, list):
        raise AuthorizationDenied("ENDPOINT_COST_RULE_INVALID")
    tiers: list[CostTier] = []
    previous_max: int | None = None
    for item in raw_tiers:
        tier = _mapping(item, "ENDPOINT_COST_TIERS_INVALID")
        minimum = tier.get("min_inclusive")
        maximum = tier.get("max_inclusive")
        cost = tier.get("cost")
        if not all(
            isinstance(number, int) and not isinstance(number, bool)
            for number in (minimum, maximum, cost)
        ):
            raise AuthorizationDenied("ENDPOINT_COST_TIERS_INVALID")
        minimum = cast(int, minimum)
        maximum = cast(int, maximum)
        cost = cast(int, cost)
        if minimum < 0 or maximum < minimum or cost < 0:
            raise AuthorizationDenied("ENDPOINT_COST_TIERS_INVALID")
        if previous_max is not None and minimum != previous_max + 1:
            raise AuthorizationDenied("ENDPOINT_COST_TIERS_INVALID")
        previous_max = maximum
        tiers.append(CostTier(minimum=minimum, maximum=maximum, cost=cost))
    if mode == "FIXED":
        if not isinstance(fixed_cost, int) or fixed_cost < 0 or parameter_name is not None or tiers:
            raise AuthorizationDenied("ENDPOINT_COST_RULE_INVALID")
    elif mode == "NOT_APPLICABLE":
        if fixed_cost != 0 or parameter_name is not None or tiers:
            raise AuthorizationDenied("ENDPOINT_COST_RULE_INVALID")
    elif mode == "SIGNED_PARAMETER_TIERS":
        if fixed_cost is not None or not isinstance(parameter_name, str) or not tiers:
            raise AuthorizationDenied("ENDPOINT_COST_RULE_INVALID")
    else:
        raise AuthorizationDenied("ENDPOINT_COST_RULE_INVALID")
    return CostRule(
        mode=mode,
        fixed_cost=fixed_cost,
        parameter_name=parameter_name,
        tiers=tuple(tiers),
    )


def verify_endpoint_catalog(
    document: Mapping[str, Any],
    keyring: Mapping[str, Any],
    *,
    expected_keyring_hash: str,
    request_schema_path: Path,
    source_artifact_root: Path,
    now: datetime,
) -> RuntimeEndpointCatalog:
    """Verify signature, expiry, source closure, uniqueness and tier semantics."""
    verified = verify_signed_config_document(
        document,
        keyring,
        expected_keyring_hash=expected_keyring_hash,
        hash_field="catalog_hash",
        status_field="catalog_status",
        now=now,
    )
    content = verified.content
    checked_at = _time(content.get("checked_at"), "ENDPOINT_CATALOG_TIME_INVALID")
    valid_until = _time(content.get("valid_until"), "ENDPOINT_CATALOG_TIME_INVALID")
    utc_now = now.astimezone(UTC)
    if not checked_at <= verified.signed_at <= utc_now < valid_until:
        raise AuthorizationDenied("ENDPOINT_CATALOG_EXPIRED")
    expected_schema_hash = hashlib.sha256(request_schema_path.read_bytes()).hexdigest()

    raw_sources = content.get("source_documents")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise AuthorizationDenied("ENDPOINT_SOURCE_INVALID")
    source_hashes: set[str] = set()
    for item in raw_sources:
        source = _mapping(item, "ENDPOINT_SOURCE_INVALID")
        relative = source.get("artifact_path")
        expected_hash = source.get("sha256")
        if not isinstance(relative, str) or not isinstance(expected_hash, str):
            raise AuthorizationDenied("ENDPOINT_SOURCE_INVALID")
        artifact = (source_artifact_root / relative).resolve()
        root = source_artifact_root.resolve()
        if not artifact.is_relative_to(root) or not artifact.is_file():
            raise AuthorizationDenied("ENDPOINT_SOURCE_INVALID")
        if hashlib.sha256(artifact.read_bytes()).hexdigest() != expected_hash:
            raise AuthorizationDenied("ENDPOINT_SOURCE_HASH_MISMATCH")
        if expected_hash in source_hashes:
            raise AuthorizationDenied("ENDPOINT_SOURCE_INVALID")
        source_hashes.add(expected_hash)

    raw_contracts = content.get("endpoint_contracts")
    if not isinstance(raw_contracts, list) or not raw_contracts:
        raise AuthorizationDenied("ENDPOINT_CATALOG_INVALID")
    identities: set[tuple[object, ...]] = set()
    endpoints: dict[tuple[str, str], EndpointPolicy] = {}
    for item in raw_contracts:
        contract = _mapping(item, "ENDPOINT_CATALOG_INVALID")
        endpoint_id = contract.get("endpoint_id")
        authorities = _strings(contract.get("endpoint_authorities"), "ENDPOINT_CATALOG_INVALID")
        transport = contract.get("transport")
        method = contract.get("method")
        path = contract.get("path")
        source_hash = contract.get("source_document_sha256")
        if (
            not isinstance(endpoint_id, str)
            or not isinstance(transport, str)
            or not isinstance(method, str)
            or not isinstance(path, str)
            or source_hash not in source_hashes
            or contract.get("request_schema_sha256") != expected_schema_hash
        ):
            raise AuthorizationDenied("ENDPOINT_CATALOG_INVALID")
        allowed_classes = _strings(
            contract.get("allowed_operation_classes"), "ENDPOINT_CATALOG_INVALID"
        )
        causal_map = _mapping(contract.get("causal_role_class_map"), "ENDPOINT_CATALOG_INVALID")
        if not causal_map or not all(
            isinstance(key, str) and isinstance(value, str) and value in allowed_classes
            for key, value in causal_map.items()
        ):
            raise AuthorizationDenied("ENDPOINT_CAUSAL_MAP_INVALID")
        allowed_parameters = _strings(
            contract.get("allowed_parameter_names"), "ENDPOINT_CATALOG_INVALID", allow_empty=True
        )
        required_parameters = _strings(
            contract.get("required_parameter_names"), "ENDPOINT_CATALOG_INVALID", allow_empty=True
        )
        forbidden_parameters = _strings(
            contract.get("forbidden_parameter_names"), "ENDPOINT_CATALOG_INVALID", allow_empty=True
        )
        if (
            not required_parameters <= allowed_parameters
            or allowed_parameters & forbidden_parameters
        ):
            raise AuthorizationDenied("ENDPOINT_PARAMETER_POLICY_INVALID")
        policy = EndpointPolicy(
            endpoint_id=endpoint_id,
            authority="",
            transport=transport,
            method=method,
            path=path,
            market_stream_role=contract.get("market_stream_role"),
            control_frame_type=contract.get("control_frame_type"),
            allowed_operation_classes=allowed_classes,
            causal_role_class_map=dict(causal_map),
            request_weight_rule=_cost_rule(contract.get("request_weight_rule")),
            order_count_rule=_cost_rule(contract.get("order_count_rule")),
            websocket_control_rule=_cost_rule(contract.get("websocket_control_rule")),
            connection_attempt_rule=_cost_rule(contract.get("connection_attempt_rule")),
            parameter_policy=str(contract.get("parameter_policy")),
            allowed_parameter_names=allowed_parameters,
            required_parameter_names=required_parameters,
            forbidden_parameter_names=forbidden_parameters,
            request_schema_sha256=expected_schema_hash,
            contract_payload=dict(contract),
            contract_hash=canonical_digest(contract).hex(),
        )
        for authority in authorities:
            identity = (
                authority,
                transport,
                method,
                path,
                policy.market_stream_role,
                policy.control_frame_type,
            )
            key = (authority, endpoint_id)
            if identity in identities or key in endpoints:
                raise AuthorizationDenied("ENDPOINT_IDENTITY_DUPLICATE")
            identities.add(identity)
            endpoints[key] = replace(policy, authority=authority)

    bootstrap = _mapping(content.get("bootstrap"), "ENDPOINT_BOOTSTRAP_INVALID")
    bootstrap_ids = _strings(bootstrap.get("allowed_endpoint_ids"), "ENDPOINT_BOOTSTRAP_INVALID")
    present_ids = {endpoint_id for _, endpoint_id in endpoints}
    if not bootstrap_ids <= present_ids:
        raise AuthorizationDenied("ENDPOINT_BOOTSTRAP_INVALID")
    catalog_id = content.get("catalog_id")
    if not isinstance(catalog_id, str) or content.get("unknown_endpoint_policy") != "DENY":
        raise AuthorizationDenied("ENDPOINT_CATALOG_INVALID")
    return RuntimeEndpointCatalog(
        catalog_id=catalog_id,
        catalog_hash=verified.content_hash,
        checked_at=checked_at,
        valid_until=valid_until,
        endpoints=endpoints,
    )


def load_runtime_endpoint_catalog(
    catalog_path: Path,
    catalog_schema_path: Path,
    keyring_path: Path,
    keyring_schema_path: Path,
    *,
    trusted_root_directory: Path,
    expected_keyring_hash: str,
    request_schema_path: Path,
    source_artifact_root: Path,
    now: datetime,
) -> RuntimeEndpointCatalog:
    """Validate closed schemas and load a signed catalog from the trusted filesystem boundary."""
    assert_root_owned_0444(keyring_path, trusted_directory=trusted_root_directory)
    catalog = validate_config(catalog_path, catalog_schema_path)
    keyring = validate_config(keyring_path, keyring_schema_path)
    if not isinstance(catalog, dict) or not isinstance(keyring, dict):
        raise AuthorizationDenied("ENDPOINT_CATALOG_INVALID")
    return verify_endpoint_catalog(
        catalog,
        keyring,
        expected_keyring_hash=expected_keyring_hash,
        request_schema_path=request_schema_path,
        source_artifact_root=source_artifact_root,
        now=now,
    )
