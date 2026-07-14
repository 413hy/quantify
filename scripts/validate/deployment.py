#!/usr/bin/env python3
"""Static validation for inactive Debian host deployment artifacts."""

from __future__ import annotations

from pathlib import Path

import yaml

from ai_quant.binance_egress.nftables_policy import render_nftables_policy
from ai_quant.common.config import load_strict_document

ROOT = Path(__file__).resolve().parents[2]


def _validate_unit(name: str, expected_start: str, *, private_network: bool) -> None:
    path = ROOT / "deploy/systemd" / name
    text = path.read_text(encoding="utf-8")
    required = {
        "User=root",
        "Group=root",
        f"ExecStart={expected_start}",
        "NoNewPrivileges=yes",
        "ProtectSystem=strict",
        "ProtectHome=yes",
        "ProtectKernelTunables=yes",
        "ProtectKernelModules=yes",
        "ProtectControlGroups=yes",
        "RestrictNamespaces=yes",
        "RestrictSUIDSGID=yes",
        "MemoryDenyWriteExecute=yes",
        "SystemCallArchitectures=native",
    }
    missing = sorted(item for item in required if item not in text.splitlines())
    forbidden = ("/bin/sh", "bash -c", "curl ", "wget ", "sudo ", "ExecStartPre=")
    if missing or any(token in text for token in forbidden):
        raise SystemExit(f"unsafe systemd unit {name}: missing={missing}")
    if private_network != ("PrivateNetwork=yes" in text.splitlines()):
        raise SystemExit(f"unexpected network namespace policy in {name}")
    if name == "aiq-measurement-cycle.service" and not {
        "RuntimeDirectory=ai-quant-measurements ai-quant-facts",
        "RuntimeDirectoryMode=0750",
        "RuntimeDirectoryPreserve=yes",
    } <= set(text.splitlines()):
        raise SystemExit("measurement output runtime directories are not fixed")


def _validate_compose_socket() -> None:
    document = yaml.safe_load(
        (ROOT / "deploy/host-control.compose.yaml").read_text(encoding="utf-8")
    )
    postgres = document["services"]["host-control-postgres"]
    if postgres.get("ports"):
        raise SystemExit("host-control PostgreSQL TCP publication is forbidden")
    mounts = {
        (mount.get("source"), mount.get("target"))
        for mount in postgres.get("volumes", [])
        if isinstance(mount, dict)
    }
    if (
        "/run/ai-quant-host-postgres",
        "/var/run/postgresql",
    ) not in mounts:
        raise SystemExit("fixed host measurement PostgreSQL Unix socket is missing")


def _validate_measurement_plan_example() -> None:
    document = load_strict_document(ROOT / "deploy/measurement-cycle-plan.example.json")
    expected = {
        "schema_version",
        "local_facts_plan_file",
        "database_password_file",
        "network_policy_file",
        "network_policy_schema_file",
        "connection_contract_file",
        "connection_contract_schema_file",
        "connection_source_artifact_root",
        "endpoint_catalog_file",
        "endpoint_catalog_schema_file",
        "endpoint_source_artifact_root",
        "gateway_request_schema_file",
        "keyring_file",
        "keyring_schema_file",
        "keyring_hash_file",
        "bootstrap_traces_file",
    }
    if (
        not isinstance(document, dict)
        or set(document) != expected
        or document.get("schema_version") != "1.0.0"
        or any(
            not isinstance(value, str) or not value.startswith("/")
            for key, value in document.items()
            if key != "schema_version"
        )
    ):
        raise SystemExit("measurement-cycle plan example is not closed")


def main() -> int:
    _validate_unit(
        "aiq-measurement-cycle.service",
        "/opt/ai-quant/.venv/bin/python -m ai_quant.services.measurement_cycle",
        private_network=False,
    )
    _validate_unit(
        "aiq-local-facts-collector.service",
        "/opt/ai-quant/.venv/bin/python -m ai_quant.services.local_facts_collector",
        private_network=True,
    )
    _validate_compose_socket()
    _validate_measurement_plan_example()
    example = load_strict_document(
        ROOT / "deploy/host-hardening/ai-quant-egress.example.json"
    )
    if not isinstance(example, dict):
        raise SystemExit("nftables example is not an object")
    render_nftables_policy(example)
    print("deployment static policy PASS units=2 postgres_tcp=none nft_table=ai_quant_egress")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
