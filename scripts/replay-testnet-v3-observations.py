#!/usr/bin/env python3
"""Run a small, predeclared V3 parameter sweep over append-only observations."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from ai_quant.research.testnet_observation_replay import (
    ReplayParameters,
    replay_observations,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--observations", required=True, type=Path)
    parser.add_argument("--campaign-state", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    state = json.loads(arguments.campaign_state.read_text(encoding="utf-8"))
    documents = [
        observation
        for line in arguments.observations.read_text(encoding="utf-8").splitlines()
        if isinstance((observation := json.loads(line)), dict)
    ]
    variants = (
        ReplayParameters("CURRENT_V3"),
        ReplayParameters("ACTIVITY_1", minimum_activity_ratio=Decimal("1.00")),
        ReplayParameters("ACTIVITY_2", minimum_activity_ratio=Decimal("2.00")),
        ReplayParameters("CONFIRM_3", confirmation_rounds=3),
        ReplayParameters("SPREAD_5", maximum_spread_bps=Decimal("5.00")),
        ReplayParameters(
            "ACTIVITY_2_CONFIRM_3",
            confirmation_rounds=3,
            minimum_activity_ratio=Decimal("2.00"),
        ),
        ReplayParameters(
            "ACTIVITY_2_SPREAD_5",
            minimum_activity_ratio=Decimal("2.00"),
            maximum_spread_bps=Decimal("5.00"),
        ),
        ReplayParameters(
            "QUALITY_4_ACTIVITY_2",
            minimum_quality_score=Decimal("4.00"),
            minimum_activity_ratio=Decimal("2.00"),
        ),
        ReplayParameters("FIXED_TARGET_12", target_bps_override=Decimal("12")),
        ReplayParameters("FIXED_TARGET_15", target_bps_override=Decimal("15")),
        ReplayParameters("FIXED_TARGET_18", target_bps_override=Decimal("18")),
        ReplayParameters("FIXED_TARGET_20", target_bps_override=Decimal("20")),
        ReplayParameters("FIXED_TARGET_22", target_bps_override=Decimal("22")),
        ReplayParameters("FIXED_TARGET_25", target_bps_override=Decimal("25")),
        ReplayParameters("FIXED_TARGET_30", target_bps_override=Decimal("30")),
        ReplayParameters("FIXED_TARGET_35", target_bps_override=Decimal("35")),
        ReplayParameters(
            "FIXED_TARGET_20_ACTIVITY_2",
            minimum_activity_ratio=Decimal("2.00"),
            target_bps_override=Decimal("20"),
        ),
        ReplayParameters(
            "FIXED_TARGET_20_ACTIVITY_2_SPREAD_5",
            minimum_activity_ratio=Decimal("2.00"),
            maximum_spread_bps=Decimal("5.00"),
            target_bps_override=Decimal("20"),
        ),
        ReplayParameters(
            "FIXED_TARGET_20_ACTIVITY_2_CONFIRM_3",
            confirmation_rounds=3,
            minimum_activity_ratio=Decimal("2.00"),
            target_bps_override=Decimal("20"),
        ),
        ReplayParameters(
            "FIXED_TARGET_20_ACTIVITY_2_CONFIRM_3_SPREAD_5",
            confirmation_rounds=3,
            minimum_activity_ratio=Decimal("2.00"),
            maximum_spread_bps=Decimal("5.00"),
            target_bps_override=Decimal("20"),
        ),
        ReplayParameters("TARGET_50", minimum_target_bps=Decimal("50")),
        ReplayParameters("TARGET_60", minimum_target_bps=Decimal("60")),
        ReplayParameters(
            "TARGET_50_ACTIVITY_2",
            minimum_activity_ratio=Decimal("2.00"),
            minimum_target_bps=Decimal("50"),
        ),
        ReplayParameters(
            "TARGET_50_ACTIVITY_2_CONFIRM_3",
            confirmation_rounds=3,
            minimum_activity_ratio=Decimal("2.00"),
            minimum_target_bps=Decimal("50"),
        ),
        ReplayParameters("PA_BOTH", minimum_pa_alignment_count=2),
    )
    start_at = datetime.fromisoformat(str(state["started_at"]).replace("Z", "+00:00"))
    reports = [replay_observations(documents, variant, start_at=start_at) for variant in variants]
    ranked = sorted(
        reports,
        key=lambda item: (
            Decimal(str(item["net_bps"])),
            int(item["closed_trades"]),
        ),
        reverse=True,
    )
    document: dict[str, Any] = {
        "schema_version": "1.0.0",
        "report": "TESTNET_V3_CAUSAL_OBSERVATION_REPLAY",
        "strategy": "TESTNET_EXPERIMENT_OF_PA_V3",
        "campaign_started_at": state["started_at"],
        "production_endpoint_requests": 0,
        "execution_semantics": {
            "entry": "recorded causal plan entry reference after replayed temporal gates",
            "exit": "first later 10-second recorded mid crossing structure target or stop",
            "elapsed_time_exit": False,
            "round_trip_fee_bps": "8",
            "adverse_exit_slippage_bps": "2",
            "comparison_notional": "50 USDT",
        },
        "limitations": [
            "Ten-second mids can miss intrainterval target/stop touches and cannot "
            "reproduce fills.",
            "This is an in-sample parameter comparison over one short Testnet campaign.",
            "Open positions are excluded from net results and no variant qualifies production.",
        ],
        "variants": reports,
        "ranked_variant_names": [item["parameters"]["name"] for item in ranked],
    }
    _atomic_write(arguments.output, document)
    print(json.dumps(document, sort_keys=True, separators=(",", ":")))
    return 0


def _atomic_write(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
