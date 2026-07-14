#!/usr/bin/env python3
"""Validate copied immutable contracts, examples, hashes, and OpenAPI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from openapi_spec_validator import validate_spec  # noqa: E402

from tools.preflight_audit import (  # noqa: E402
    load_yaml,
    validate_jcs_hashes,
    validate_schemas_and_examples,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only")
    args = parser.parse_args()
    result = validate_schemas_and_examples(ROOT)
    failures = list(result["failures"])
    hashes = validate_jcs_hashes(ROOT)
    failures.extend(hashes["failures"])
    try:
        validate_spec(load_yaml(ROOT / "contracts/openapi.yaml"))
    except Exception as exc:
        failures.append(f"openapi: {exc}")
    if args.only:
        expected = ROOT / "contracts" / f"{args.only}.schema.json"
        if not expected.exists():
            failures.append(f"unknown contract: {args.only}")
    if failures:
        print("\n".join(failures))
        return 1
    print(
        f"contracts PASS schemas={result['schema_count']} "
        f"instances={result['contract_instance_count']} jcs={hashes['check_count']} openapi=1"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
