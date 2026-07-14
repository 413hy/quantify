"""FastAPI control surface; intentionally has no arm, resume, unlock, or secret routes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_quant.control.commands import (
    ChallengeError,
    CommandConflictError,
    InMemoryCommandStore,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CommandRequest(ApiModel):
    actor_id: str = Field(min_length=1, max_length=120)
    source: Literal["LOCAL_CLI", "INTERNAL_API", "SYSTEM"]
    reason: str = Field(min_length=5, max_length=1000)
    requested_at: datetime
    expires_at: datetime
    nonce: str = Field(min_length=16, max_length=128)

    @model_validator(mode="after")
    def validity_window(self) -> CommandRequest:
        if self.expires_at <= self.requested_at:
            raise ValueError("command expires_at must follow requested_at")
        return self


class CancelOrdersRequest(CommandRequest):
    symbol: str | None = Field(default=None, pattern=r"^[A-Z0-9_]{2,30}$")


class FlattenPrepareRequest(CommandRequest):
    source: Literal["LOCAL_CLI", "INTERNAL_API"]
    scope: Literal["ALL_POSITIONS"]


class FlattenConfirmRequest(CommandRequest):
    source: Literal["LOCAL_CLI", "INTERNAL_API"]
    challenge_id: str
    positions_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation_phrase: str = Field(min_length=8, max_length=64)


@dataclass(frozen=True, slots=True)
class SessionContext:
    principal: str
    role: Literal["viewer", "operator_limited", "operator_local"]
    channel: Literal["LOCAL_CLI", "INTERNAL_API"]


@dataclass(slots=True)
class ControlState:
    release_id: str = "development"
    runtime_state: str = "RISK_LOCKED"
    environment: str = "paper"
    risk_multiplier: str = "0.10"
    new_entries_allowed: bool = False
    positions: dict[str, str] = field(default_factory=dict)
    dependencies_ready: bool = True
    store: InMemoryCommandStore = field(default_factory=InMemoryCommandStore)

    def positions_digest(self) -> str:
        encoded = json.dumps(self.positions, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


def create_control_app(
    state: ControlState,
    *,
    sessions: dict[str, SessionContext] | None = None,
) -> FastAPI:
    session_map = sessions or {}
    app = FastAPI(title="AI Quant Internal Control API", version="1.2.0")

    def authenticate(
        authorization: str | None = Header(default=None),
    ) -> SessionContext:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "AUTHENTICATION_FAILED")
        session = session_map.get(authorization.removeprefix("Bearer "))
        if session is None:
            raise HTTPException(401, "AUTHENTICATION_FAILED")
        return session

    def operator(
        session: SessionContext = Depends(authenticate),  # noqa: B008
    ) -> SessionContext:
        if session.role not in {"operator_limited", "operator_local"}:
            raise HTTPException(403, "AUTHORIZATION_FAILED")
        return session

    def validate_context(request: CommandRequest, session: SessionContext) -> datetime:
        now = datetime.now(UTC)
        if request.actor_id != session.principal or request.source != session.channel:
            raise HTTPException(403, "AUTH_CONTEXT_MISMATCH")
        if request.requested_at.tzinfo is None or request.expires_at.tzinfo is None:
            raise HTTPException(422, "UTC_TIMESTAMPS_REQUIRED")
        if now > request.expires_at:
            raise HTTPException(410, "COMMAND_EXPIRED")
        return now

    def key(
        value: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> str:
        if value is None or not 16 <= len(value) <= 128:
            raise HTTPException(422, "IDEMPOTENCY_KEY_REQUIRED")
        return value

    @app.get("/health/live")
    def liveness() -> dict[str, object]:
        return {
            "status": "UP",
            "observed_at": datetime.now(UTC),
            "can_open_new_positions": False,
            "reasons": ["RISK_LOCKED"],
        }

    @app.get("/health/ready")
    def readiness() -> dict[str, object]:
        return {
            "status": "UP" if state.dependencies_ready else "DEGRADED",
            "observed_at": datetime.now(UTC),
            "can_open_new_positions": state.new_entries_allowed,
            "reasons": [] if state.dependencies_ready else ["DEPENDENCY_UNAVAILABLE"],
        }

    @app.get("/v1/status")
    def status(
        _: SessionContext = Depends(authenticate),  # noqa: B008
    ) -> dict[str, object]:
        observed_at = datetime.now(UTC)
        dependency = {
            "state": "HEALTHY" if state.dependencies_ready else "UNAVAILABLE",
            "observed_at": observed_at,
            "stale_for_ms": 0,
            "blocks_new_entries": not state.dependencies_ready,
            "reason_codes": [] if state.dependencies_ready else ["DEPENDENCY_UNAVAILABLE"],
        }
        dependency_names = (
            "postgres",
            "host_rate_control",
            "binance_egress_gateway",
            "market_data",
            "codex_orchestrator",
            "execution_api",
            "user_data_stream",
            "clock",
            "disk",
            "native_protection",
            "external_heartbeat",
            "redis",
            "archive_sync",
        )
        return {
            "release_id": state.release_id,
            "runtime_state": state.runtime_state,
            "strategy_version": None,
            "environment": state.environment,
            "experimental_live": False,
            "risk_multiplier": state.risk_multiplier,
            "risk_locked": state.runtime_state == "RISK_LOCKED",
            "new_entries_allowed": state.new_entries_allowed,
            "decision_authority": "RULE_FALLBACK",
            "codex_state": "UNAVAILABLE",
            "reconciled_at": None,
            "dependencies": {name: dependency for name in dependency_names},
        }

    def accept(
        command_type: str,
        request: CommandRequest,
        session: SessionContext,
        idempotency_key: str,
    ) -> dict[str, object]:
        now = validate_context(request, session)
        try:
            record = state.store.accept(
                command_type=command_type,
                idempotency_key=idempotency_key,
                actor_id=request.actor_id,
                payload=request.model_dump(mode="json"),
                now=now,
            )
        except CommandConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(503, "DATABASE_UNWRITABLE") from exc
        return {
            "command_id": record.command_id,
            "accepted": True,
            "state": record.state,
            "accepted_at": record.accepted_at,
            "audit_event_id": record.audit_event_id,
        }

    def receipt(record: object) -> dict[str, object]:
        from ai_quant.control.commands import CommandRecord

        if not isinstance(record, CommandRecord):
            raise TypeError("invalid command record")
        return {
            "command_id": record.command_id,
            "accepted": True,
            "state": record.state,
            "accepted_at": record.accepted_at,
            "audit_event_id": record.audit_event_id,
        }

    @app.post("/v1/commands/pause-new-entries", status_code=202)
    def pause(
        request: CommandRequest,
        session: SessionContext = Depends(operator),  # noqa: B008
        idempotency_key: str = Depends(key),
    ) -> dict[str, object]:
        response = accept("PAUSE_NEW_ENTRIES", request, session, idempotency_key)
        state.runtime_state = "PAUSED_NEW_ENTRIES"
        state.new_entries_allowed = False
        return response

    @app.post("/v1/commands/cancel-open-orders", status_code=202)
    def cancel(
        request: CancelOrdersRequest,
        session: SessionContext = Depends(operator),  # noqa: B008
        idempotency_key: str = Depends(key),
    ) -> dict[str, object]:
        return accept("CANCEL_OPEN_ORDERS", request, session, idempotency_key)

    @app.post("/v1/commands/emergency-flatten/prepare")
    def prepare(
        request: FlattenPrepareRequest,
        session: SessionContext = Depends(operator),  # noqa: B008
        idempotency_key: str = Depends(key),
    ) -> dict[str, object]:
        now = validate_context(request, session)
        try:
            challenge = state.store.prepare_flatten(
                actor_id=request.actor_id,
                positions_digest=state.positions_digest(),
                idempotency_key=idempotency_key,
                now=now,
            )
        except OSError as exc:
            raise HTTPException(503, "DATABASE_UNWRITABLE") from exc
        return {
            "challenge_id": challenge.challenge_id,
            "expires_at": challenge.expires_at,
            "summary": f"Flatten {len(state.positions)} position(s)",
            "positions_digest": challenge.positions_digest,
            "confirmation_phrase": challenge.confirmation_phrase,
        }

    @app.post("/v1/commands/emergency-flatten/confirm", status_code=202)
    def confirm(
        request: FlattenConfirmRequest,
        session: SessionContext = Depends(operator),  # noqa: B008
        idempotency_key: str = Depends(key),
    ) -> dict[str, object]:
        now = validate_context(request, session)
        payload = request.model_dump(mode="json")
        try:
            existing = state.store.find_idempotent(
                command_type="EMERGENCY_FLATTEN",
                idempotency_key=idempotency_key,
                payload=payload,
            )
            if existing is not None:
                return receipt(existing)
            state.store.consume_flatten(
                challenge_id=request.challenge_id,
                actor_id=request.actor_id,
                positions_digest=request.positions_digest,
                confirmation_phrase=request.confirmation_phrase,
                now=now,
            )
        except ChallengeError as exc:
            raise HTTPException(410, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(503, "DATABASE_UNWRITABLE") from exc
        response = accept("EMERGENCY_FLATTEN", request, session, idempotency_key)
        state.runtime_state = "EMERGENCY_FLATTENING"
        state.new_entries_allowed = False
        return response

    return app
