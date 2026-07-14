"""Root-only host measurement collector for startup attestation."""

from __future__ import annotations

import os
import signal
import time
from datetime import UTC, datetime
from pathlib import Path

from ai_quant.binance_egress.local_facts import (
    collect_and_publish_local_facts,
    load_local_facts_plan,
)

TRUSTED_PLAN_DIRECTORY = Path("/etc/ai-quant/trust")
REFRESH_INTERVAL_SECONDS = 1


def _path(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required file setting: {name}")
    path = Path(value)
    if not path.is_absolute():
        raise RuntimeError(f"file setting must be absolute: {name}")
    return path


def run() -> None:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise RuntimeError("local facts collector must run as root")

    def stop_on_signal(_signal_number: int, _frame: object) -> None:
        raise SystemExit(0)

    signal.signal(signal.SIGINT, stop_on_signal)
    signal.signal(signal.SIGTERM, stop_on_signal)
    plan_path = _path("AIQ_LOCAL_FACTS_PLAN_FILE")
    plan = load_local_facts_plan(
        plan_path,
        trusted_plan_directory=TRUSTED_PLAN_DIRECTORY,
    )
    plan.facts_path.unlink(missing_ok=True)
    try:
        while True:
            collect_and_publish_local_facts(plan, now=datetime.now(UTC))
            time.sleep(REFRESH_INTERVAL_SECONDS)
    finally:
        # Stop/failure removes the root snapshot immediately so the signer cannot
        # refresh from facts that merely remain within the short freshness window.
        plan.facts_path.unlink(missing_ok=True)


def main() -> None:
    run()


if __name__ == "__main__":
    main()
