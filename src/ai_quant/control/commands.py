"""Idempotent local commands and one-use emergency-flatten confirmation challenges."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum


class CommandConflictError(RuntimeError):
    pass


class ChallengeError(RuntimeError):
    pass


class CommandState(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class CommandRecord:
    command_id: str
    command_type: str
    request_hash: str
    idempotency_key: str
    actor_id: str
    state: CommandState
    accepted_at: datetime
    updated_at: datetime
    audit_event_id: str
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FlattenChallenge:
    challenge_id: str
    actor_id: str
    positions_digest: str
    confirmation_phrase: str
    expires_at: datetime
    consumed: bool = False


class InMemoryCommandStore:
    def __init__(self) -> None:
        self.commands: dict[str, CommandRecord] = {}
        self.by_idempotency: dict[str, str] = {}
        self.challenges: dict[str, FlattenChallenge] = {}

    def accept(
        self,
        *,
        command_type: str,
        idempotency_key: str,
        actor_id: str,
        payload: dict[str, object],
        now: datetime,
    ) -> CommandRecord:
        request_hash = _hash(payload)
        existing_id = self.by_idempotency.get(idempotency_key)
        if existing_id:
            existing = self.commands[existing_id]
            if existing.request_hash != request_hash or existing.command_type != command_type:
                raise CommandConflictError("IDEMPOTENCY_CONFLICT")
            return existing
        identity = hashlib.sha256(
            f"{command_type}|{idempotency_key}|{request_hash}".encode()
        ).hexdigest()
        record = CommandRecord(
            command_id=identity[:32],
            command_type=command_type,
            request_hash=request_hash,
            idempotency_key=idempotency_key,
            actor_id=actor_id,
            state=CommandState.ACCEPTED,
            accepted_at=now,
            updated_at=now,
            audit_event_id=identity[32:],
        )
        self.commands[record.command_id] = record
        self.by_idempotency[idempotency_key] = record.command_id
        return record

    def find_idempotent(
        self,
        *,
        command_type: str,
        idempotency_key: str,
        payload: dict[str, object],
    ) -> CommandRecord | None:
        existing_id = self.by_idempotency.get(idempotency_key)
        if existing_id is None:
            return None
        existing = self.commands[existing_id]
        if existing.command_type != command_type or existing.request_hash != _hash(payload):
            raise CommandConflictError("IDEMPOTENCY_CONFLICT")
        return existing

    def complete(self, command_id: str, now: datetime) -> CommandRecord:
        record = replace(
            self.commands[command_id],
            state=CommandState.COMPLETED,
            updated_at=now,
        )
        self.commands[command_id] = record
        return record

    def prepare_flatten(
        self,
        *,
        actor_id: str,
        positions_digest: str,
        idempotency_key: str,
        now: datetime,
    ) -> FlattenChallenge:
        identity = hashlib.sha256(
            f"flatten|{actor_id}|{positions_digest}|{idempotency_key}".encode()
        ).hexdigest()
        challenge = self.challenges.get(identity[:32])
        if challenge:
            return challenge
        challenge = FlattenChallenge(
            challenge_id=identity[:32],
            actor_id=actor_id,
            positions_digest=positions_digest,
            confirmation_phrase=f"FLATTEN-{identity[32:40].upper()}",
            expires_at=now + timedelta(seconds=60),
        )
        self.challenges[challenge.challenge_id] = challenge
        return challenge

    def consume_flatten(
        self,
        *,
        challenge_id: str,
        actor_id: str,
        positions_digest: str,
        confirmation_phrase: str,
        now: datetime,
    ) -> FlattenChallenge:
        challenge = self.challenges.get(challenge_id)
        if challenge is None:
            raise ChallengeError("CONFIRMATION_REQUIRED")
        if challenge.consumed:
            raise ChallengeError("CONFIRMATION_USED")
        if now > challenge.expires_at:
            raise ChallengeError("CONFIRMATION_EXPIRED")
        if (
            challenge.actor_id != actor_id
            or challenge.positions_digest != positions_digest
            or challenge.confirmation_phrase != confirmation_phrase
        ):
            raise ChallengeError("CONFIRMATION_REQUIRED")
        consumed = replace(challenge, consumed=True)
        self.challenges[challenge_id] = consumed
        return consumed


def _hash(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
