"""Runtime state is explicit and locked by default."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class RuntimeState(StrEnum):
    BOOTSTRAP = "BOOTSTRAP"
    RISK_LOCKED = "RISK_LOCKED"
    RECONCILING = "RECONCILING"
    SHADOW = "SHADOW"
    PAPER = "PAPER"
    TESTNET = "TESTNET"
    EXPERIMENTAL_LIVE = "EXPERIMENTAL_LIVE"
    PAUSED_NEW_ENTRIES = "PAUSED_NEW_ENTRIES"
    EMERGENCY_FLATTENING = "EMERGENCY_FLATTENING"
    STOPPED = "STOPPED"


class RuntimeStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: RuntimeState = RuntimeState.RISK_LOCKED
    new_entries_allowed: bool = False
    reason_code: str = "STARTUP_EVIDENCE_MISSING"
