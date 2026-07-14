from __future__ import annotations

import copy
from typing import Any

import pytest

from ai_quant.binance_egress.nftables_policy import render_nftables_policy
from ai_quant.rate_budget.authorization import AuthorizationDenied


def _plan() -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "policy_id": "aiq-egress-production-0001",
        "gateway_ipv4": "172.31.0.2",
        "business_ipv4_subnets": ["172.32.0.0/16"],
        "binance_ipv4": ["192.0.2.10", "192.0.2.11"],
        "gateway_ipv6": None,
        "business_ipv6_subnets": [],
        "binance_ipv6": [],
    }


def test_policy_renders_only_dedicated_destination_table() -> None:
    rendered = render_nftables_policy(_plan())
    assert rendered.startswith("table inet ai_quant_egress")
    assert "hook input" not in rendered
    assert "flush ruleset" not in rendered
    assert 'comment "aiq:gateway-binance-allow"' in rendered
    assert 'comment "aiq:business-binance-deny"' in rendered
    assert 'comment "aiq:default-deny"' in rendered


def test_policy_rejects_source_overlap_or_partial_ipv6() -> None:
    overlap = _plan()
    overlap["business_ipv4_subnets"] = ["172.31.0.0/16"]
    with pytest.raises(AuthorizationDenied, match="NFT_POLICY_SOURCE_OVERLAP"):
        render_nftables_policy(overlap)

    partial = copy.deepcopy(_plan())
    partial["gateway_ipv6"] = "2001:db8::2"
    with pytest.raises(AuthorizationDenied, match="NFT_POLICY_IPV6_COVERAGE_INVALID"):
        render_nftables_policy(partial)
