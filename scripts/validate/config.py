#!/usr/bin/env python3
"""Validate copied immutable configuration examples with secret-value rejection."""

from __future__ import annotations

import argparse
from pathlib import Path

from ai_quant.common.config import ConfigurationError, validate_config

ROOT = Path(__file__).resolve().parents[2]


def schema_base(path: Path) -> str:
    base = path.name.replace(".example.json", "").replace(".example.yaml", "")
    return "verification-keyring" if base == "verification-keyring.host-control" else base


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only")
    args = parser.parse_args()
    candidates = sorted(ROOT.glob("config/*.example.json")) + sorted(
        ROOT.glob("config/*.example.yaml")
    )
    if args.only:
        candidates = [path for path in candidates if schema_base(path) == args.only]
        if not candidates:
            print(f"no example for {args.only}")
            return 1
    failures: list[str] = []
    for path in candidates:
        schema = ROOT / "config" / f"{schema_base(path)}.schema.json"
        try:
            validate_config(path, schema)
        except (ConfigurationError, OSError, ValueError) as exc:
            failures.append(f"{path.relative_to(ROOT)}: {exc}")
    if failures:
        print("\n".join(failures))
        return 1
    print(f"config PASS examples={len(candidates)} secrets=not-embedded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
