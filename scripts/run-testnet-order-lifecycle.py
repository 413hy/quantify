#!/usr/bin/env python3
"""Run one bounded real Testnet place/query/cancel lifecycle."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from ai_quant.binance_egress.testnet_probe import (
    TestnetProbeError,
    run_testnet_order_lifecycle,
)


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
    args = parser.parse_args()
    try:
        evidence = run_testnet_order_lifecycle(
            api_key_file=args.api_key_file,
            api_secret_file=args.api_secret_file,
            repository_root=args.repository_root,
            symbol=args.symbol,
        )
    except TestnetProbeError as exc:
        _write_evidence(
            args.output,
            {
                "schema_version": "1.0.0",
                "probe": "BINANCE_USDS_M_FUTURES_TESTNET_ORDER_LIFECYCLE",
                "completed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "result": "FAIL_CLOSED",
                "reason_code": str(exc),
                "production_endpoint_requests": 0,
            },
        )
        print(f"testnet order lifecycle FAIL_CLOSED reason={exc}")
        return 2
    _write_evidence(args.output, evidence)
    print(
        "testnet order lifecycle PASS "
        f"symbol={evidence['symbol']} final_status={evidence['final_status']} "
        "open_orders=0 positions=0"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
