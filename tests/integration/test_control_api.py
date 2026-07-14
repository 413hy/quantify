from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from ai_quant.control.api import ControlState, SessionContext, create_control_app
from ai_quant.control.commands import InMemoryCommandStore


def request(actor: str = "operator-1", source: str = "LOCAL_CLI") -> dict[str, str]:
    now = datetime.now(UTC)
    return {
        "actor_id": actor,
        "source": source,
        "reason": "controlled integration test",
        "requested_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=1)).isoformat(),
        "nonce": "0123456789abcdef",
    }


def client(state: ControlState | None = None) -> TestClient:
    app = create_control_app(
        state or ControlState(),
        sessions={
            "operator-token": SessionContext("operator-1", "operator_local", "LOCAL_CLI"),
            "viewer-token": SessionContext("viewer-1", "viewer", "INTERNAL_API"),
        },
    )
    return TestClient(app)


def headers(token: str | None = None, key: str = "idempotency-key-01") -> dict[str, str]:
    session_token = token or "operator-token"
    return {"Authorization": f"Bearer {session_token}", "Idempotency-Key": key}


def test_health_is_public_but_status_requires_opaque_session() -> None:
    api = client()
    assert api.get("/health/live").status_code == 200
    assert api.get("/v1/status").status_code == 401
    status = api.get("/v1/status", headers={"Authorization": "Bearer viewer-token"})
    assert status.status_code == 200
    assert status.json()["risk_locked"] is True
    assert len(status.json()["dependencies"]) == 13


def test_pause_is_idempotent_and_caller_context_is_bound() -> None:
    state = ControlState()
    api = client(state)
    body = request()

    first = api.post("/v1/commands/pause-new-entries", headers=headers(), json=body)
    second = api.post("/v1/commands/pause-new-entries", headers=headers(), json=body)

    assert first.status_code == second.status_code == 202
    assert first.json()["command_id"] == second.json()["command_id"]
    assert state.runtime_state == "PAUSED_NEW_ENTRIES"
    mismatched = api.post(
        "/v1/commands/pause-new-entries",
        headers=headers(key="idempotency-key-02"),
        json=request(actor="forged-actor"),
    )
    assert mismatched.status_code == 403


def test_viewer_cannot_issue_commands() -> None:
    response = client().post(
        "/v1/commands/pause-new-entries",
        headers=headers("viewer-token"),
        json=request(actor="viewer-1", source="INTERNAL_API"),
    )
    assert response.status_code == 403


def test_emergency_flatten_requires_matching_one_use_challenge() -> None:
    state = ControlState(positions={"BTCUSDT": "1"})
    api = client(state)
    prepare_body = {**request(), "scope": "ALL_POSITIONS"}
    prepared = api.post(
        "/v1/commands/emergency-flatten/prepare",
        headers=headers(key="flatten-prepare-01"),
        json=prepare_body,
    )
    assert prepared.status_code == 200
    challenge = prepared.json()
    confirm_body = {
        **request(),
        "challenge_id": challenge["challenge_id"],
        "positions_digest": challenge["positions_digest"],
        "confirmation_phrase": challenge["confirmation_phrase"],
    }
    confirmed = api.post(
        "/v1/commands/emergency-flatten/confirm",
        headers=headers(key="flatten-confirm-01"),
        json=confirm_body,
    )
    assert confirmed.status_code == 202
    assert state.runtime_state == "EMERGENCY_FLATTENING"
    idempotent_replay = api.post(
        "/v1/commands/emergency-flatten/confirm",
        headers=headers(key="flatten-confirm-01"),
        json=confirm_body,
    )
    assert idempotent_replay.status_code == 202
    assert idempotent_replay.json()["command_id"] == confirmed.json()["command_id"]
    replay = api.post(
        "/v1/commands/emergency-flatten/confirm",
        headers=headers(key="flatten-confirm-02"),
        json=confirm_body,
    )
    assert replay.status_code == 410


class FailingStore(InMemoryCommandStore):
    def accept(self, **_: object) -> object:
        raise OSError("database unavailable")


def test_database_unwritable_fails_closed() -> None:
    state = ControlState(store=FailingStore())
    response = client(state).post(
        "/v1/commands/pause-new-entries", headers=headers(), json=request()
    )
    assert response.status_code == 503
    assert state.runtime_state == "RISK_LOCKED"
    assert not state.new_entries_allowed
