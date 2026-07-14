#!/usr/bin/env python3
"""Run one bounded Testnet micro-scalp lifecycle and write redacted evidence."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from ai_quant.binance_egress.micro_scalp import run_testnet_micro_scalp
from ai_quant.binance_egress.testnet_probe import TestnetProbeError


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
    parser.add_argument("--symbol", default="SOLUSDT")
    parser.add_argument("--margin-budget", type=Decimal, default=Decimal("1"))
    parser.add_argument("--target-net-profit", type=Decimal, default=Decimal("0.1"))
    parser.add_argument("--maximum-net-loss", type=Decimal, default=Decimal("0.1"))
    parser.add_argument("--maximum-holding-seconds", type=int, default=30)
    parser.add_argument("--adverse-exit-slippage-bps", type=Decimal, default=Decimal("2"))
    args = parser.parse_args()
    try:
        evidence = run_testnet_micro_scalp(
            api_key_file=args.api_key_file,
            api_secret_file=args.api_secret_file,
            repository_root=args.repository_root,
            symbol=args.symbol,
            margin_budget=args.margin_budget,
            target_net_profit=args.target_net_profit,
            maximum_net_loss=args.maximum_net_loss,
            maximum_holding_seconds=args.maximum_holding_seconds,
            adverse_exit_slippage_bps=args.adverse_exit_slippage_bps,
        )
    except (TestnetProbeError, ValueError) as exc:
        _write_evidence(
            args.output,
            {
                "schema_version": "1.0.0",
                "probe": "BINANCE_USDS_M_FUTURES_TESTNET_MICRO_SCALP",
                "completed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "result": "FAIL_CLOSED",
                "reason_code": str(exc),
                "production_endpoint_requests": 0,
            },
        )
        print(f"testnet micro scalp FAIL_CLOSED reason={exc}")
        return 2
    _write_evidence(args.output, evidence)
    print(
        "testnet micro scalp PASS "
        f"symbol={evidence['symbol']} exit={evidence['exit_reason']} "
        f"net_pnl={evidence['net_pnl']} flat=true"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
