from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ai_quant.orchestration.authority import (
    AuthorityController,
    CodexState,
    DecisionAuthority,
    FreshContextEvidence,
    scheduled_engines,
    validate_fresh_context,
)

NOW = datetime(2026, 7, 14, 10, tzinfo=UTC)


def evidence(**changes: object) -> FreshContextEvidence:
    values: dict[str, object] = {
        "analysis_run_id": "run-new",
        "thread_id": "thread-new",
        "workspace_id": "workspace-new",
        "ephemeral": True,
        "resume_used": False,
        "history_or_transcript_input": False,
        "memory_input": False,
        "prior_cycle_canary_seen": False,
        "duration": timedelta(seconds=30),
    }
    values.update(changes)
    return FreshContextEvidence(**values)  # type: ignore[arg-type]


def test_fresh_context_rejects_resume_canary_and_timeout() -> None:
    valid, reasons = validate_fresh_context(
        evidence(
            resume_used=True,
            prior_cycle_canary_seen=True,
            duration=timedelta(seconds=91),
        ),
        prior_run_ids=set(),
        prior_thread_ids=set(),
        prior_workspace_ids=set(),
    )
    assert not valid
    assert reasons == (
        "CODEX_EPHEMERAL_ISOLATION_FAILED",
        "CODEX_CANARY_LEAKED",
        "CODEX_ANALYSIS_TIMEOUT",
    )


def test_ai_failure_immediately_falls_back_and_needs_three_dry_runs() -> None:
    controller = AuthorityController()
    controller.record_ai_cycle(False)
    assert controller.authority is DecisionAuthority.RULE_FALLBACK
    assert controller.codex_state is CodexState.COOLDOWN

    controller.record_ai_cycle(True)
    controller.record_ai_cycle(True)
    assert controller.authority is DecisionAuthority.RULE_FALLBACK
    controller.record_ai_cycle(True)
    assert controller.authority is DecisionAuthority.CODEX_PRIMARY


def test_one_decision_epoch_cannot_be_acquired_twice() -> None:
    controller = AuthorityController()
    epoch = controller.acquire_epoch(NOW)
    assert epoch == NOW.isoformat()
    with pytest.raises(RuntimeError, match="ALREADY_ACQUIRED"):
        controller.acquire_epoch(NOW)


def test_schedule_has_rule_20m_and_codex_30m_cadence() -> None:
    assert scheduled_engines(NOW) == ("RULE", "CODEX")
    assert scheduled_engines(NOW.replace(minute=20)) == ("RULE",)
    assert scheduled_engines(NOW.replace(minute=30)) == ("CODEX",)
