#!/usr/bin/python3
"""Small, dependency-free host verification CLI for the M0 bootstrap boundary.

Unsupported commands fail closed.  Application lifecycle commands are added only
with the milestone that implements their authority and audit trail.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

VERSION = "0.1.0-m0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle, object_pairs_hook=reject_duplicate)


def artifact_sha256(args: argparse.Namespace) -> int:
    value = _sha256(args.input)
    print(value if args.raw else f"{value}  {args.input}")
    return 0


def bootstrap_verify_lock(args: argparse.Namespace) -> int:
    document = _load_json(args.lock)
    required = {
        "schema_version",
        "platform",
        "apt_sources",
        "packages",
        "artifacts",
        "required_commands",
    }
    if not isinstance(document, dict) or set(document) != required:
        raise SystemExit("toolchain lock is not a closed object")
    if document["schema_version"] != "1.0.0":
        raise SystemExit("unsupported toolchain lock schema")
    platform = document["platform"]
    if platform != {
        "architecture": "arm64",
        "distribution": "debian",
        "release": "12",
        "codename": "bookworm",
    }:
        raise SystemExit("toolchain lock platform mismatch")
    packages = document["packages"]
    if not isinstance(packages, list) or not packages:
        raise SystemExit("empty package lock")
    names: set[str] = set()
    for package in packages:
        if not isinstance(package, dict) or set(package) != {
            "name",
            "version",
            "architecture",
            "source",
            "filename",
            "sha256",
        }:
            raise SystemExit("package lock entry is not closed")
        name = package["name"]
        if not isinstance(name, str) or name in names:
            raise SystemExit("duplicate or invalid package name")
        names.add(name)
        if not re.fullmatch(r"[0-9a-f]{64}", package["sha256"]):
            raise SystemExit(f"invalid package digest: {name}")
    print(f"toolchain lock PASS packages={len(packages)} sha256={_sha256(args.lock)}")
    return 0


def compose_security_verify(args: argparse.Namespace) -> int:
    document = _load_json(args.rendered)
    services = document.get("services") if isinstance(document, dict) else None
    if not isinstance(services, dict):
        raise SystemExit("rendered compose services missing")
    findings: list[str] = []
    for name, service in services.items():
        if not isinstance(service, dict):
            findings.append(f"{name}: invalid service")
            continue
        if args.deny_host_network and service.get("network_mode") == "host":
            findings.append(f"{name}: host network")
        if args.deny_host_pid and service.get("pid") == "host":
            findings.append(f"{name}: host pid")
        if args.deny_host_ipc and service.get("ipc") == "host":
            findings.append(f"{name}: host ipc")
        if args.deny_privileged and service.get("privileged") is True:
            findings.append(f"{name}: privileged")
        mounts = service.get("volumes", [])
        if args.deny_docker_socket and any(
            "/var/run/docker.sock" in str(mount) for mount in mounts
        ):
            findings.append(f"{name}: docker socket")
        if args.require_read_only_rootfs and service.get("read_only") is not True:
            findings.append(f"{name}: writable rootfs")
        security_opt = service.get("security_opt", [])
        if args.require_no_new_privileges and "no-new-privileges:true" not in security_opt:
            findings.append(f"{name}: no-new-privileges missing")
    if findings:
        raise SystemExit("compose security FAIL " + "; ".join(findings))
    print(f"compose security PASS services={len(services)}")
    return 0


def secrets_inspect_permissions(args: argparse.Namespace) -> int:
    raw_paths: list[str] = list(args.path)
    if args.paths_from_env:
        raw_paths.extend(
            value
            for name, value in os.environ.items()
            if name.endswith(("_SECRET_FILE", "_PASSWORD_FILE", "_KEY_FILE")) and value
        )
    paths = sorted({Path(value) for value in raw_paths})
    if not paths:
        raise SystemExit("no secret paths supplied")
    findings: list[str] = []
    for path in paths:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            findings.append(f"{path}: missing")
            continue
        mode = stat.S_IMODE(metadata.st_mode)
        if not stat.S_ISREG(metadata.st_mode) or path.is_symlink() or mode != 0o400:
            findings.append(f"{path}: expected regular non-symlink mode 0400")
            continue
        label = path.name if args.redact else str(path)
        print(f"secret metadata PASS name={label} mode=0400 uid={metadata.st_uid}")
    if findings:
        raise SystemExit("secret permissions FAIL " + "; ".join(findings))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quantctl")
    parser.add_argument("--version", action="version", version=VERSION)
    commands = parser.add_subparsers(dest="command", required=True)

    artifact = commands.add_parser("artifact")
    artifact_commands = artifact.add_subparsers(dest="artifact_command", required=True)
    sha = artifact_commands.add_parser("sha256")
    sha.add_argument("--input", required=True, type=Path)
    sha.add_argument("--raw", action="store_true")
    sha.set_defaults(handler=artifact_sha256)

    bootstrap = commands.add_parser("bootstrap")
    bootstrap_commands = bootstrap.add_subparsers(dest="bootstrap_command", required=True)
    verify_lock = bootstrap_commands.add_parser("verify-lock")
    verify_lock.add_argument("--lock", required=True, type=Path)
    verify_lock.set_defaults(handler=bootstrap_verify_lock)

    compose = commands.add_parser("compose")
    compose_commands = compose.add_subparsers(dest="compose_command", required=True)
    security = compose_commands.add_parser("security-verify")
    security.add_argument("--rendered", required=True, type=Path)
    security.add_argument("--deny-host-network", action="store_true")
    security.add_argument("--deny-host-pid", action="store_true")
    security.add_argument("--deny-host-ipc", action="store_true")
    security.add_argument("--deny-privileged", action="store_true")
    security.add_argument("--deny-docker-socket", action="store_true")
    security.add_argument("--require-read-only-rootfs", action="store_true")
    security.add_argument("--require-no-new-privileges", action="store_true")
    security.set_defaults(handler=compose_security_verify)

    secrets = commands.add_parser("secrets")
    secrets_commands = secrets.add_subparsers(dest="secrets_command", required=True)
    inspect_permissions = secrets_commands.add_parser("inspect-permissions")
    inspect_permissions.add_argument("--path", action="append", default=[])
    inspect_permissions.add_argument("--paths-from-env", action="store_true")
    inspect_permissions.add_argument("--redact", action="store_true")
    inspect_permissions.set_defaults(handler=secrets_inspect_permissions)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    handler = getattr(args, "handler", None)
    if handler is None:
        raise SystemExit("unsupported command")
    return int(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
