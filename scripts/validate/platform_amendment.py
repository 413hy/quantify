#!/usr/bin/env python3
"""Ensure mutable project guidance selects only the owner-approved platform."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FROZEN_SCOPES = {"config", "contracts", "runbooks", "diagrams"}
MUTABLE_SCOPES = (
    "README.md",
    "IMPLEMENTATION_STATUS.md",
    "HANDOFF_STATE.md",
    "Makefile",
    "chat",
    "deploy",
    "docker",
    "docs",
    "evidence",
    "migrations",
    "scripts",
    "src",
    "tests",
    "tools",
)
LEGACY_PLATFORM_TERM = "ubu" "ntu"


def files_in_scope() -> list[Path]:
    files: list[Path] = []
    for name in MUTABLE_SCOPES:
        path = ROOT / name
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(item for item in path.rglob("*") if item.is_file())
    return files


def main() -> int:
    amendment = ROOT / "docs/adr/0004-debian-12-sole-platform.md"
    deployment = ROOT / "docs/deployment/debian-12-platform.md"
    failures: list[str] = []
    for required in (amendment, deployment):
        if not required.is_file():
            failures.append(f"missing platform authority: {required.relative_to(ROOT)}")
    for path in files_in_scope():
        relative = path.relative_to(ROOT)
        if relative.parts and relative.parts[0] in FROZEN_SCOPES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if LEGACY_PLATFORM_TERM in text.lower():
            failures.append(f"legacy platform term in mutable file: {relative}")
    if failures:
        print("\n".join(failures))
        return 1
    print("platform amendment PASS target=debian-12-bookworm-aarch64")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
