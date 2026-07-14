from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from ai_quant.binance_egress.bootstrap_measurements import measure_bootstrap_chain
from ai_quant.rate_budget.authorization import AuthorizationDenied, canonical_digest

ROOT = Path(__file__).resolve().parents[2]


def _example(name: str) -> dict[str, Any]:
    return json.loads((ROOT / f"contracts/examples/{name}").read_text(encoding="utf-8"))


def _trace(suffix: str) -> dict[str, dict[str, Any]]:
    reserve_request = _example("rate-reserve-request.json")
    reserve_decision = _example("rate-reserve-decision.json")
    consume_request = _example("rate-permit-consume-request.json")
    consume_decision = _example("rate-permit-consume-decision.json")
    send_outcome = _example("rate-send-outcome.json")
    observation = _example("rate-server-time-observation.json")
    correlation_id = f"bootstrap-correlation-{suffix}"
    permit_id = f"bootstrap-permit-{suffix}"
    reserve_request["message_id"] = f"reserve-request-{suffix}"
    reserve_request["correlation_id"] = correlation_id
    reserve_decision["message_id"] = f"reserve-decision-{suffix}"
    reserve_decision["request_message_id"] = reserve_request["message_id"]
    reserve_decision["correlation_id"] = correlation_id
    reserve_decision["permit_id"] = permit_id
    consume_request["message_id"] = f"consume-request-{suffix}"
    consume_request["correlation_id"] = correlation_id
    consume_request["permit_id"] = permit_id
    consume_decision["message_id"] = f"consume-decision-{suffix}"
    consume_decision["request_message_id"] = consume_request["message_id"]
    consume_decision["correlation_id"] = correlation_id
    consume_decision["permit_id"] = permit_id
    send_outcome["message_id"] = f"send-outcome-{suffix}"
    send_outcome["correlation_id"] = correlation_id
    send_outcome["permit_id"] = permit_id
    observation["message_id"] = f"server-observation-{suffix}"
    observation["correlation_id"] = correlation_id
    observation["permit_id"] = permit_id
    gateway_request = {
        "request_id": f"gateway-request-{suffix}",
        "canonical_request_hash": reserve_request["canonical_request_hash"],
        "parameter_hash": reserve_request["parameter_hash"],
        "wire_bytes_hash": reserve_request["wire_bytes_hash"],
    }
    gateway_hash = canonical_digest(gateway_request).hex()
    for document in (
        reserve_request,
        reserve_decision,
        consume_request,
        consume_decision,
    ):
        document["gateway_request_document_hash"] = gateway_hash
    return {
        "reserve_request": reserve_request,
        "reserve_decision": reserve_decision,
        "gateway_request": gateway_request,
        "consume_request": consume_request,
        "consume_decision": consume_decision,
        "send_outcome": send_outcome,
        "observation": observation,
    }


def test_bootstrap_chain_hashes_two_closed_causal_traces() -> None:
    measured = measure_bootstrap_chain([_trace("0001"), _trace("0002")])
    assert set(measured) == {
        "reserve_request_hashes",
        "reserve_decision_hashes",
        "gateway_request_hashes",
        "consume_request_hashes",
        "consume_decision_hashes",
        "send_outcome_hashes",
        "observation_hashes",
    }
    assert all(len(pair) == 2 and pair[0] != pair[1] for pair in measured.values())


def test_bootstrap_chain_rejects_broken_permit_binding() -> None:
    first = _trace("0001")
    second = _trace("0002")
    second["send_outcome"]["permit_id"] = "different-bootstrap-permit"
    with pytest.raises(
        AuthorizationDenied,
        match="BOOTSTRAP_MEASUREMENT_CAUSAL_MISMATCH",
    ):
        measure_bootstrap_chain([first, second])


def test_bootstrap_chain_rejects_trace_replay() -> None:
    trace = _trace("0001")
    with pytest.raises(AuthorizationDenied, match="BOOTSTRAP_MEASUREMENT_REPLAYED"):
        measure_bootstrap_chain([trace, copy.deepcopy(trace)])
