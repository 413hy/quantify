"""Closed-schema gateway IPC wrapper around the exact-wire send pipeline."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from ai_quant.binance_egress.gateway import GatewayDenied, GatewaySendApplication
from ai_quant.rate_budget.authorization import PeerCredentials


def _inline_request_schema(value: Any, request_schema: Mapping[str, Any]) -> Any:
    if isinstance(value, dict):
        if value == {"$ref": "binance-gateway-request.schema.json"}:
            return copy.deepcopy(request_schema)
        return {key: _inline_request_schema(child, request_schema) for key, child in value.items()}
    if isinstance(value, list):
        return [_inline_request_schema(child, request_schema) for child in value]
    return value


class GatewayProtocolApplication:
    """Validate the full gateway IPC contract before and after security decisions."""

    def __init__(
        self,
        *,
        ipc_schema_path: Path,
        request_schema_path: Path,
        send_application: GatewaySendApplication,
    ) -> None:
        ipc_schema = json.loads(ipc_schema_path.read_text(encoding="utf-8"))
        request_schema = json.loads(request_schema_path.read_text(encoding="utf-8"))
        schema = _inline_request_schema(ipc_schema, request_schema)
        Draft202012Validator.check_schema(schema)
        self._validator = Draft202012Validator(schema, format_checker=FormatChecker())
        self._send_application = send_application

    def __call__(
        self,
        message: Mapping[str, Any],
        peer: PeerCredentials,
    ) -> Mapping[str, Any]:
        if list(self._validator.iter_errors(message)):
            raise GatewayDenied("GATEWAY_PROTOCOL_SCHEMA_INVALID")
        if message.get("message_type") != "GatewaySendRequest":
            raise GatewayDenied("GATEWAY_PROTOCOL_DIRECTION_INVALID")
        response = self._send_application(message, peer)
        if list(self._validator.iter_errors(response)):
            raise GatewayDenied("GATEWAY_RESPONSE_SCHEMA_INVALID")
        return response
