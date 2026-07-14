"""Derive the closed startup readiness section from independently measured gates."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ai_quant.rate_budget.authorization import AuthorizationDenied


def measure_readiness(
    *,
    rate_allocator_probe_ready: bool,
    gateway_probe_ready: bool,
    catalog_runtime_signed: bool,
    artifacts_not_expired: bool,
    enabled_authorities: frozenset[str],
    authority_observations: Sequence[Mapping[str, Any]],
    nonce_permit_integrity: Mapping[str, Any],
    active_authority_blocks: frozenset[str],
) -> Mapping[str, Any]:
    """Return READY only when every independently supplied gate is closed and healthy."""
    observed = {
        item.get("endpoint_authority")
        for item in authority_observations
        if isinstance(item, Mapping)
    }
    integrity_zero = all(
        nonce_permit_integrity.get(field) == 0
        for field in (
            "duplicate_capability_nonce_count",
            "consumed_without_gateway_count",
            "outcome_missing_past_deadline_count",
        )
    )
    blocked = enabled_authorities & active_authority_blocks
    if (
        not enabled_authorities
        or observed != set(enabled_authorities)
        or not rate_allocator_probe_ready
        or not gateway_probe_ready
        or not catalog_runtime_signed
        or not artifacts_not_expired
        or not integrity_zero
        or blocked
    ):
        raise AuthorizationDenied("STARTUP_READINESS_NOT_READY")
    return {
        "rate_allocator_ready": True,
        "gateway_ready": True,
        "catalog_runtime_signed": True,
        "artifacts_not_expired": True,
        "blocked_authority_count": 0,
        "result": "READY",
    }
