#!/usr/bin/env python3
"""Static validation for inactive Debian host deployment artifacts."""

from __future__ import annotations

import hashlib
import re
import stat
from pathlib import Path

import yaml

from ai_quant.binance_egress.nftables_policy import render_nftables_policy
from ai_quant.common.config import load_strict_document

ROOT = Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_bootstrap_bundle() -> None:
    lock_path = ROOT / "deploy/host-toolchain.lock.yaml"
    lock = load_strict_document(lock_path)
    required = {
        "schema_version",
        "platform",
        "apt_sources",
        "packages",
        "artifacts",
        "required_commands",
    }
    if not isinstance(lock, dict) or set(lock) != required:
        raise SystemExit("bootstrap toolchain lock is not closed")
    if lock["schema_version"] != "1.0.0" or lock["platform"] != {
        "architecture": "arm64",
        "distribution": "debian",
        "release": "12",
        "codename": "bookworm",
    }:
        raise SystemExit("bootstrap toolchain platform mismatch")
    expected_sources = {"debian-bookworm-snapshot", "docker-debian-bookworm"}
    sources = lock["apt_sources"]
    if not isinstance(sources, list) or {
        item.get("id") for item in sources if isinstance(item, dict)
    } != expected_sources:
        raise SystemExit("bootstrap apt source set mismatch")
    packages = lock["packages"]
    expected_packages = {
        "age",
        "ca-certificates",
        "chrony",
        "containerd.io",
        "curl",
        "docker-ce",
        "docker-ce-cli",
        "docker-compose-plugin",
        "jq",
        "nftables",
        "openssh-server",
        "openssl",
        "postgresql-client-15",
        "postgresql-client-common",
    }
    if (
        not isinstance(packages, list)
        or {item.get("name") for item in packages} != expected_packages
    ):
        raise SystemExit("bootstrap exact package coverage mismatch")
    for package in packages:
        if (
            package.get("source") not in expected_sources
            or not re.fullmatch(r"[0-9a-f]{64}", package.get("sha256", ""))
            or "latest" in package.get("version", "").lower()
        ):
            raise SystemExit(f"unsafe bootstrap package lock: {package.get('name')}")
    artifacts = {item.get("name"): item for item in lock["artifacts"]}
    if set(artifacts) != {"cosign", "quantctl"}:
        raise SystemExit("controlled bootstrap artifacts missing")
    quantctl = ROOT / artifacts["quantctl"]["path"]
    if _sha256(quantctl) != artifacts["quantctl"]["sha256"]:
        raise SystemExit("controlled quantctl hash mismatch")
    if not artifacts["cosign"]["url"].startswith(
        "https://github.com/sigstore/cosign/releases/download/v3.0.6/"
    ):
        raise SystemExit("cosign release source is not fixed")
    hardening = ROOT / "deploy/host-hardening"
    manifest = hardening / "manifest.sha256"
    entries: dict[str, str] = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if line:
            digest, relative = line.split("  ", 1)
            entries[relative] = digest
    for relative, digest in entries.items():
        target = (hardening / relative).resolve()
        if not target.is_relative_to(hardening.resolve()) or _sha256(target) != digest:
            raise SystemExit(f"hardening manifest mismatch: {relative}")
    required_hardening = {
        "chrony/ai-quant.sources",
        "docker/daemon.json",
        "journald/99-ai-quant.conf",
        "limits/99-ai-quant.conf",
        "nftables/ai-quant-host-input.nft.template",
        "sshd/99-ai-quant.conf.template",
        "sysctl/99-ai-quant.conf",
        "systemd/aiq-host-input-firewall.service",
    }
    if not required_hardening <= set(entries):
        raise SystemExit("hardening file set is incomplete")
    for script in (
        ROOT / "scripts/bootstrap-host.sh",
        ROOT / "scripts/bootstrap_host.py",
        ROOT / "scripts/create_bootstrap_approval.py",
    ):
        if stat.S_IMODE(script.stat().st_mode) != 0o755:
            raise SystemExit(f"bootstrap script is not executable: {script.name}")
    implementation = (ROOT / "scripts/bootstrap_host.py").read_text(encoding="utf-8")
    for boundary in (
        "command_plan",
        "command_apply",
        "command_prove_ssh",
        "command_verify",
        "verify_approval",
        "validate_ssh_proof",
    ):
        if f"def {boundary}" not in implementation:
            raise SystemExit(f"bootstrap boundary missing: {boundary}")
    if "curl |" in implementation or "PermitRootLogin yes" in implementation:
        raise SystemExit("unsafe bootstrap implementation token")


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


def _validate_baseline_unit() -> None:
    path = ROOT / "deploy/systemd/aiq-deployment-baseline.service"
    text = path.read_text(encoding="utf-8")
    required = {
        "User=root",
        "Group=root",
        "NoNewPrivileges=yes",
        "ProtectSystem=strict",
        "ProtectHome=yes",
        "PrivateDevices=yes",
        "ProtectKernelTunables=yes",
        "ProtectKernelModules=yes",
        "ProtectControlGroups=yes",
        "RestrictNamespaces=yes",
        "RestrictSUIDSGID=yes",
        "MemoryDenyWriteExecute=yes",
        "ReadWritePaths=/var/lib/ai-quant/preflight",
    }
    if not required <= set(text.splitlines()):
        raise SystemExit("deployment baseline unit hardening is incomplete")
    expected = (
        "ExecStart=/opt/ai-quant/scripts/collect_deployment_baseline.py collect "
        "--output /var/lib/ai-quant/preflight/deployment-baseline.jsonl "
        "--duration-seconds 86400 --interval-seconds 60"
    )
    if expected not in text.splitlines() or any(
        token in text for token in ("/bin/sh", "bash -c", "curl ", "wget ")
    ):
        raise SystemExit("deployment baseline unit command is unsafe")


def main() -> int:
    _validate_bootstrap_bundle()
    _validate_baseline_unit()
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
    print(
        "deployment static policy PASS units=2 postgres_tcp=none "
        "nft_table=ai_quant_egress bootstrap=debian12-locked"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
