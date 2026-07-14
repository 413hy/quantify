"""Executable root-only six-source measurement refresh loop."""

from __future__ import annotations

import os
import signal
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from ai_quant.binance_egress.local_facts import load_local_facts_plan
from ai_quant.binance_egress.measurement_cycle import (
    TRUSTED_PLAN_DIRECTORY,
    invalidate_measurement_outputs,
    load_measurement_cycle_plan,
    run_measurement_cycle_once,
)

REFRESH_INTERVAL_SECONDS = 1


def _plan_path() -> Path:
    raw = os.environ.get("AIQ_MEASUREMENT_CYCLE_PLAN_FILE")
    if not raw or not Path(raw).is_absolute():
        raise RuntimeError("AIQ_MEASUREMENT_CYCLE_PLAN_FILE must be absolute")
    return Path(raw)


def run() -> None:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise RuntimeError("measurement cycle must run as root")

    def stop_on_signal(_signal_number: int, _frame: object) -> None:
        raise SystemExit(0)

    signal.signal(signal.SIGINT, stop_on_signal)
    signal.signal(signal.SIGTERM, stop_on_signal)
    plan_path = _plan_path()
    last_plan = load_measurement_cycle_plan(
        plan_path,
        trusted_plan_directory=TRUSTED_PLAN_DIRECTORY,
    )
    last_local_plan = load_local_facts_plan(
        last_plan.local_facts_plan_file,
        trusted_plan_directory=TRUSTED_PLAN_DIRECTORY,
    )
    invalidate_measurement_outputs(last_local_plan)
    try:
        while True:
            last_plan = load_measurement_cycle_plan(
                plan_path,
                trusted_plan_directory=TRUSTED_PLAN_DIRECTORY,
            )
            candidate_local_plan = load_local_facts_plan(
                last_plan.local_facts_plan_file,
                trusted_plan_directory=TRUSTED_PLAN_DIRECTORY,
            )
            if candidate_local_plan != last_local_plan:
                invalidate_measurement_outputs(last_local_plan)
                last_local_plan = candidate_local_plan
            try:
                run_measurement_cycle_once(
                    last_plan,
                    local_plan=last_local_plan,
                    now=datetime.now(UTC),
                )
            except Exception as exc:
                invalidate_measurement_outputs(last_local_plan)
                print(
                    f"measurement cycle failed closed: {type(exc).__name__}",
                    file=sys.stderr,
                    flush=True,
                )
            time.sleep(REFRESH_INTERVAL_SECONDS)
    finally:
        invalidate_measurement_outputs(last_local_plan)


def main() -> None:
    run()


if __name__ == "__main__":
    main()
