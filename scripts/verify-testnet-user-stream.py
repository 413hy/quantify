#!/usr/bin/env python3
"""Verify closed Testnet user-data evidence and its observer state."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ai_quant.binance_egress.testnet_user_stream import verify_user_data_evidence


def _write(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--require-event-types",
        default="ORDER_TRADE_UPDATE,ACCOUNT_UPDATE,ALGO_UPDATE",
    )
    arguments = parser.parse_args()
    summary = verify_user_data_evidence(arguments.events)
    state = json.loads(arguments.state.read_text(encoding="utf-8"))
    required_types = {
        value.strip() for value in arguments.require_event_types.split(",") if value.strip()
    }
    counts = summary["event_type_counts"]
    valid_state = (
        isinstance(state, dict)
        and state.get("schema_version") == "1.0.0"
        and state.get("environment") == "testnet"
        and state.get("production_endpoint_requests") == 0
        and state.get("accepted_event_count") == summary["record_count"]
        and state.get("event_type_counts") == summary["event_type_counts"]
        and state.get("last_record_sha256") == summary["final_record_sha256"]
        and isinstance(state.get("connection_attempt_count"), int)
        and isinstance(state.get("connection_count"), int)
        and isinstance(state.get("reconnect_count"), int)
        and 0 <= state.get("connection_count", -1) <= state.get("connection_attempt_count", -1)
        and 0 <= state.get("reconnect_count", -1) <= state.get("connection_attempt_count", -1)
    )
    observed_types = set(counts) if isinstance(counts, dict) else set()
    result = "PASS" if valid_state and required_types <= observed_types else "FAIL_CLOSED"
    evidence = {
        **summary,
        "result": result,
        "verified_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "required_event_types": sorted(required_types),
        "missing_event_types": sorted(required_types - observed_types),
        "state_status": state.get("status") if isinstance(state, dict) else None,
        "connection_attempt_count": (
            state.get("connection_attempt_count") if isinstance(state, dict) else None
        ),
        "connection_count": state.get("connection_count") if isinstance(state, dict) else None,
        "reconnect_count": state.get("reconnect_count") if isinstance(state, dict) else None,
        "state_consistent": valid_state,
    }
    _write(arguments.output, evidence)
    print(
        f"testnet user stream {result} records={summary['record_count']} "
        f"types={','.join(sorted(observed_types))}"
    )
    return 0 if result == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
