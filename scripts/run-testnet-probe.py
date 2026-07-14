#!/usr/bin/env python3
"""Run the bounded Testnet capability probe and write redacted evidence."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from ai_quant.binance_egress.testnet_probe import (
    TESTNET_REST_BASE,
    TESTNET_STREAM_HOST,
    TESTNET_WS_API_HOST,
    TestnetProbeError,
    run_safe_testnet_probe,
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
        evidence = run_safe_testnet_probe(
            api_key_file=args.api_key_file,
            api_secret_file=args.api_secret_file,
            repository_root=args.repository_root,
            symbol=args.symbol,
        )
    except TestnetProbeError as exc:
        failed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        _write_evidence(
            args.output,
            {
                "schema_version": "1.0.0",
                "probe": "BINANCE_USDS_M_FUTURES_TESTNET_SAFE_CAPABILITY",
                "completed_at": failed_at,
                "result": "FAIL_CLOSED",
                "reason_code": str(exc),
                "production_endpoint_requests": 0,
                "matching_engine_orders_created": 0,
                "endpoints": {
                    "rest": TESTNET_REST_BASE,
                    "streams": f"wss://{TESTNET_STREAM_HOST}",
                    "websocket_api": f"wss://{TESTNET_WS_API_HOST}/ws-fapi/v1",
                },
            },
        )
        print(f"testnet safe capability probe FAIL_CLOSED reason={exc}")
        return 2
    _write_evidence(args.output, evidence)
    print(
        "testnet safe capability probe PASS "
        f"symbols={evidence['testnet_symbol_count']} "
        f"clock_offset_ms={evidence['clock_offset_ms']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
