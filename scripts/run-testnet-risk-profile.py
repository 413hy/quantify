#!/usr/bin/env python3
"""Set the bounded Testnet leverage profile and write redacted evidence."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from ai_quant.binance_egress.testnet_probe import TestnetProbeError, run_testnet_risk_profile


def _write_evidence(path: Path, evidence: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(evidence, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key-file", required=True, type=Path)
    parser.add_argument("--api-secret-file", required=True, type=Path)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--project-leverage-cap", type=int, default=10)
    args = parser.parse_args()
    try:
        evidence = run_testnet_risk_profile(
            api_key_file=args.api_key_file,
            api_secret_file=args.api_secret_file,
            repository_root=args.repository_root,
            symbol=args.symbol,
            project_leverage_cap=args.project_leverage_cap,
        )
    except TestnetProbeError as exc:
        _write_evidence(
            args.output,
            {
                "schema_version": "1.0.0",
                "probe": "BINANCE_USDS_M_FUTURES_TESTNET_RISK_PROFILE",
                "completed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "result": "FAIL_CLOSED",
                "reason_code": str(exc),
                "production_endpoint_requests": 0,
                "matching_engine_orders_created": 0,
            },
        )
        print(f"testnet risk profile FAIL_CLOSED reason={exc}")
        return 2
    _write_evidence(args.output, evidence)
    print(
        "testnet risk profile PASS "
        f"symbol={evidence['symbol']} leverage={evidence['selected_initial_leverage']}x"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
