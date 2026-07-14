#!/usr/bin/env python3
"""Static Compose safety policy for M0 topology."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
FILES = sorted((ROOT / "deploy").glob("*.yaml"))
DIGEST = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")


def main() -> int:
    failures: list[str] = []
    gateway_count = 0
    production_secret_consumers: list[str] = []
    for path in FILES:
        document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        for name, service in document.get("services", {}).items():
            image = service.get("image")
            is_required_digest_variable = isinstance(image, str) and image.startswith(
                "${AIQ_APP_IMAGE:?"
            )
            if not is_required_digest_variable and (
                not isinstance(image, str) or not DIGEST.fullmatch(image)
            ):
                failures.append(f"{path.name}:{name}: image is not digest-pinned")
            if name == "binance-egress-gateway":
                gateway_count += 1
            elif "binance_egress_net" in service.get("networks", []):
                failures.append(f"{path.name}:{name}: non-gateway joins Binance egress network")
            if service.get("privileged") or service.get("network_mode") == "host":
                failures.append(f"{path.name}:{name}: privileged/host network forbidden")
            mounts = service.get("volumes", [])
            if any("docker.sock" in str(mount) for mount in mounts):
                failures.append(f"{path.name}:{name}: Docker socket mount forbidden")
            for port in service.get("ports", []):
                if not str(port).startswith(("127.0.0.1:", "[::1]:")):
                    failures.append(f"{path.name}:{name}: non-loopback published port {port}")
            if "binance_production_api_secret" in service.get("secrets", []):
                production_secret_consumers.append(name)
        text = path.read_text(encoding="utf-8").lower()
        if "binance_api_secret" in text or "openai_api_key:" in text:
            failures.append(f"{path.name}: direct secret variable forbidden")
    if gateway_count != 1:
        failures.append(f"expected exactly one gateway definition, found {gateway_count}")
    if production_secret_consumers != ["execution-service"]:
        failures.append(
            "production Binance secret consumers must be exactly execution-service, got "
            f"{production_secret_consumers}"
        )
    if failures:
        print("\n".join(failures))
        return 1
    print(f"compose policy PASS files={len(FILES)} gateway_definitions={gateway_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
