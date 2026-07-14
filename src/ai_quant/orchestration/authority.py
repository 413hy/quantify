"""Fresh-context validation and deterministic AI-to-rule authority failover."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum


class DecisionAuthority(StrEnum):
    CODEX_PRIMARY = "CODEX_PRIMARY"
    RULE_FALLBACK = "RULE_FALLBACK"
    NONE = "NONE"


class CodexState(StrEnum):
    HEALTHY = "HEALTHY"
    COOLDOWN = "COOLDOWN"
    DRY_RUN_RECOVERY = "DRY_RUN_RECOVERY"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class FreshContextEvidence:
    analysis_run_id: str
    thread_id: str
    workspace_id: str
    ephemeral: bool
    resume_used: bool
    history_or_transcript_input: bool
    memory_input: bool
    prior_cycle_canary_seen: bool
    duration: timedelta


def validate_fresh_context(
    evidence: FreshContextEvidence,
    *,
    prior_run_ids: set[str],
    prior_thread_ids: set[str],
    prior_workspace_ids: set[str],
) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    if (
        evidence.analysis_run_id in prior_run_ids
        or evidence.thread_id in prior_thread_ids
        or evidence.workspace_id in prior_workspace_ids
    ):
        reasons.append("CODEX_CONTEXT_REUSED")
    if not evidence.ephemeral or evidence.resume_used:
        reasons.append("CODEX_EPHEMERAL_ISOLATION_FAILED")
    if evidence.history_or_transcript_input or evidence.memory_input:
        reasons.append("CODEX_PRIOR_CONTEXT_PRESENT")
    if evidence.prior_cycle_canary_seen:
        reasons.append("CODEX_CANARY_LEAKED")
    if evidence.duration > timedelta(seconds=90):
        reasons.append("CODEX_ANALYSIS_TIMEOUT")
    return not reasons, tuple(reasons)


class AuthorityController:
    def __init__(self) -> None:
        self.authority = DecisionAuthority.CODEX_PRIMARY
        self.codex_state = CodexState.HEALTHY
        self._successful_recovery_dry_runs = 0
        self._active_epoch: str | None = None

    def acquire_epoch(self, scheduled_at: datetime) -> str:
        if scheduled_at.tzinfo is None or scheduled_at.utcoffset() != UTC.utcoffset(scheduled_at):
            raise ValueError("schedule must use UTC")
        epoch = scheduled_at.replace(second=0, microsecond=0).isoformat()
        if self._active_epoch == epoch:
            raise RuntimeError("DECISION_EPOCH_ALREADY_ACQUIRED")
        self._active_epoch = epoch
        return epoch

    def record_ai_cycle(
        self,
        valid: bool,
        *,
        quota_available: bool = True,
        tool_available: bool = True,
    ) -> None:
        if valid and quota_available and tool_available:
            if self.authority is DecisionAuthority.RULE_FALLBACK:
                self.codex_state = CodexState.DRY_RUN_RECOVERY
                self._successful_recovery_dry_runs += 1
                if self._successful_recovery_dry_runs >= 3:
                    self.authority = DecisionAuthority.CODEX_PRIMARY
                    self.codex_state = CodexState.HEALTHY
                    self._successful_recovery_dry_runs = 0
            else:
                self.codex_state = CodexState.HEALTHY
            return
        self.authority = DecisionAuthority.RULE_FALLBACK
        self.codex_state = CodexState.COOLDOWN if quota_available else CodexState.UNAVAILABLE
        self._successful_recovery_dry_runs = 0


def scheduled_engines(at: datetime) -> tuple[str, ...]:
    """Return due engines. At :00 both share one snapshot and one epoch lease."""
    if at.tzinfo is None or at.utcoffset() != UTC.utcoffset(at):
        raise ValueError("schedule must use UTC")
    minute = at.minute
    due: list[str] = []
    if minute in {0, 20, 40}:
        due.append("RULE")
    if minute in {0, 30}:
        due.append("CODEX")
    return tuple(due)
