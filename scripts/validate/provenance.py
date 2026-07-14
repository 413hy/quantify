#!/usr/bin/env python3
"""Prove copied baseline directories remain byte-identical to immutable inputs."""

from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path("/root/quantify/reference-materials/vps-archive/vps")
SCOPES = ("contracts", "config", "runbooks", "diagrams")
OWNER_AMENDMENT = Path("docs/adr/0006-remove-time-based-position-exit.md")
OWNER_AMENDED_FILES = {
    Path("config/price-action.example.yaml"),
    Path("config/price-action.schema.json"),
    Path("contracts/examples/trade-plan-entry.json"),
    Path("contracts/trade-plan.schema.json"),
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    failures: list[str] = []
    count = 0
    amended = 0
    if not (ROOT / OWNER_AMENDMENT).is_file():
        failures.append(f"owner amendment missing: {OWNER_AMENDMENT}")
    for scope in SCOPES:
        source_files = {
            p.relative_to(SOURCE / scope) for p in (SOURCE / scope).rglob("*") if p.is_file()
        }
        copied_files = {
            p.relative_to(ROOT / scope) for p in (ROOT / scope).rglob("*") if p.is_file()
        }
        if source_files != copied_files:
            failures.append(f"{scope}: file set differs")
            continue
        for relative in sorted(source_files):
            count += 1
            if digest(SOURCE / scope / relative) != digest(ROOT / scope / relative):
                repository_path = Path(scope) / relative
                if repository_path in OWNER_AMENDED_FILES:
                    amended += 1
                else:
                    failures.append(f"{repository_path}: hash differs")
    if failures:
        print("\n".join(failures))
        return 1
    print(f"provenance PASS copied_files={count} owner_amendments={amended}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
