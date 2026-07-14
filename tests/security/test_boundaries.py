from pathlib import Path

import yaml

from ai_quant.common.runtime import RuntimeState, RuntimeStatus

ROOT = Path(__file__).resolve().parents[2]


def test_default_state_never_allows_new_entries() -> None:
    status = RuntimeStatus()
    assert status.state is RuntimeState.RISK_LOCKED
    assert not status.new_entries_allowed


def test_no_binance_client_implementation_outside_gateway() -> None:
    root = ROOT / "src/ai_quant"
    forbidden = ("httpx.", "requests.", "websockets.", "aiohttp.")
    findings: list[str] = []
    for path in root.rglob("*.py"):
        if "binance_egress" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                findings.append(f"{path}:{marker}")
    assert findings == []


def test_compose_identities_match_the_signed_trust_boundary() -> None:
    expected = {
        "realtime-engine": "11001:11001",
        "execution-service": "11002:11002",
        "binance-egress-gateway": "11005:11005",
        "rate-budget-service": "11006:11006",
        "host-attestation-signer": "11007:11007",
    }
    actual: dict[str, str] = {}
    for path in (ROOT / "deploy").glob("*.yaml"):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        for name, service in document.get("services", {}).items():
            if name in expected:
                actual[name] = service.get("user")
    assert actual == expected


def test_compose_services_receive_only_the_required_shared_socket_groups() -> None:
    expected = {
        "realtime-engine": ["11990", "11991"],
        "execution-service": ["11990", "11991"],
        "binance-egress-gateway": ["11990", "11991"],
        "rate-budget-service": ["11990"],
        "host-attestation-signer": ["11990"],
    }
    actual: dict[str, list[str]] = {}
    for path in (ROOT / "deploy").glob("*.yaml"):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        for name, service in document.get("services", {}).items():
            if name in expected:
                actual[name] = service.get("group_add")
    assert actual == expected


def test_attestation_private_key_is_granted_only_to_its_fixed_holder() -> None:
    document = yaml.safe_load(
        (ROOT / "deploy/host-control.compose.yaml").read_text(encoding="utf-8")
    )
    consumers = {
        name: service.get("secrets", [])
        for name, service in document["services"].items()
        if any(
            (grant.get("source") if isinstance(grant, dict) else grant)
            == "host_attestation_key"
            for grant in service.get("secrets", [])
        )
    }
    assert consumers == {
        "host-attestation-signer": [
            {
                "source": "host_attestation_key",
                "target": "host_attestation_key",
                "uid": "11007",
                "gid": "11007",
                "mode": 0o400,
            }
        ]
    }
