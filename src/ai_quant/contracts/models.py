"""Closed, immutable models for the security-critical M0 boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Sha256 = str


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OperationFacts(StrictModel):
    semantic_action: str
    transport: Literal["REST", "WS_API", "MARKET_STREAM_CONTROL"]
    order_role: str | None = None
    reduce_only: bool | None = None
    close_position: bool | None = None


class GatewayParameter(StrictModel):
    location: Literal["QUERY", "HEADER", "FORM", "WS_JSON"]
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
    value_base64: str
    value_hash: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    sensitivity_class: Literal[
        "NONE", "API_KEY", "LISTEN_KEY", "SIGNED_QUERY", "PRIVATE_STREAM_URL"
    ]


class GatewayWebSocketFrame(StrictModel):
    frame_type: Literal["JSON_SUBSCRIBE", "JSON_UNSUBSCRIBE", "PING", "PONG"]
    payload_base64: str
    payload_hash: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")


class ClosedGatewayRequest(StrictModel):
    schema_version: Literal["1.0.0"]
    request_id: str
    created_at: datetime
    expires_at: datetime
    subject_caller_service: str
    subject_caller_instance_id: str
    environment: Literal["shadow", "paper", "testnet", "production"]
    endpoint_authority: str
    endpoint_id: str
    endpoint_catalog_hash: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    endpoint_request_schema_hash: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    gateway_connection_id: str | None
    request_kind: Literal["STRUCTURED_UNSIGNED", "PRESIGNED_IMMUTABLE"]
    contains_sensitive_material: bool
    transport: Literal["REST", "WS_API", "MARKET_STREAM_CONTROL"]
    method: Literal["GET", "POST", "PUT", "DELETE", "CONNECT", "SEND"]
    scheme: Literal["https", "wss"]
    host: str
    port: Literal[443]
    path: str
    parameters: tuple[GatewayParameter, ...] = Field(max_length=64)
    body_base64: str | None
    websocket_frame: GatewayWebSocketFrame | None
    immutable_wire_bytes_base64: str | None
    parameter_hash: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_request_hash: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    wire_bytes_hash: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    persistence_allowed: Literal[False]
    logging_allowed: Literal[False]

    @model_validator(mode="after")
    def validity_window_is_positive(self) -> ClosedGatewayRequest:
        if self.expires_at <= self.created_at:
            raise ValueError("request expiry must be after creation")
        return self


class PermitConsumeRequest(StrictModel):
    permit_id: str
    canonical_request_hash: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    parameter_hash: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    wire_bytes_hash: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    operation_facts_hash: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    capability_payload_hash: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    gateway_request_document_hash: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    fencing_epoch: int = Field(ge=1)
    occurred_at: datetime


class ConsumeGranted(StrictModel):
    decision: Literal["CONSUME_GRANTED"] = "CONSUME_GRANTED"
    permit_id: str
    fencing_epoch: int
    send_deadline: datetime
    canonical_request_hash: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    gateway_derived_parameter_hash: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    gateway_derived_wire_bytes_hash: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    gateway_derived_operation_facts_hash: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    causal_capability_payload_hash: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    gateway_request_document_hash: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")


class ConsumeDenied(StrictModel):
    decision: Literal["CONSUME_DENIED"] = "CONSUME_DENIED"
    permit_id: str
    reason_code: str
