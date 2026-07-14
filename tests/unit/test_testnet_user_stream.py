from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType
from typing import Any

import pytest

from ai_quant.binance_egress.testnet_probe import (
    TESTNET_STREAM_HOST,
)
from ai_quant.binance_egress.testnet_probe import (
    TestnetProbeError as ProbeError,
)
from ai_quant.binance_egress.testnet_stream import open_testnet_stream_websocket
from ai_quant.binance_egress.testnet_user_stream import (
    TestnetUserDataStream as UserDataStreamObserver,
)
from ai_quant.binance_egress.testnet_user_stream import (
    UserDataEventJournal,
    verify_user_data_evidence,
)


def order_event(order_id: int = 42) -> dict[str, Any]:
    return {
        "e": "ORDER_TRADE_UPDATE",
        "E": 1_800_000_000_000,
        "T": 1_800_000_000_001,
        "o": {
            "s": "BTCUSDT",
            "i": order_id,
            "c": "aiq-test-order",
            "x": "TRADE",
            "X": "FILLED",
            "listenKey": "must-not-persist",
            "secret-key": "also-must-not-persist",
        },
    }


def test_user_data_journal_is_hash_chained_deduplicated_and_sanitized(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "events.jsonl"
    state = tmp_path / "state.json"
    journal = UserDataEventJournal(evidence, state)

    assert journal.record_event(order_event(), received_at="2026-07-14T00:00:00Z")
    assert not journal.record_event(order_event(), received_at="2026-07-14T00:00:01Z")
    assert journal.record_event(
        {
            "e": "ACCOUNT_UPDATE",
            "E": 1_800_000_000_002,
            "a": {
                "m": "ORDER",
                "B": [{"a": "USDT", "wb": "100000"}],
                "P": [{"s": "BTCUSDT", "pa": "0"}],
            },
        },
        received_at="2026-07-14T00:00:02Z",
    )
    raw = evidence.read_text(encoding="utf-8")
    assert "must-not-persist" not in raw
    assert "also-must-not-persist" not in raw
    assert '"B"' not in raw
    records = [json.loads(line) for line in raw.splitlines()]
    assert records[0]["previous_sha256"] == "0" * 64
    assert records[1]["previous_sha256"] == records[0]["record_sha256"]
    current = json.loads(state.read_text(encoding="utf-8"))
    assert current["accepted_event_count"] == 2
    assert current["duplicate_event_count"] == 1
    assert current["production_endpoint_requests"] == 0
    summary = verify_user_data_evidence(evidence)
    assert summary["result"] == "PASS"
    assert summary["record_count"] == 2
    assert summary["event_type_counts"] == {
        "ACCOUNT_UPDATE": 1,
        "ORDER_TRADE_UPDATE": 1,
    }

    restarted = UserDataEventJournal(evidence, state)
    assert not restarted.record_event(order_event())
    resumed = json.loads(state.read_text(encoding="utf-8"))
    assert resumed["accepted_event_count"] == 2
    assert resumed["duplicate_event_count"] == 2


def test_user_data_journal_rejects_tampered_history(tmp_path: Path) -> None:
    evidence = tmp_path / "events.jsonl"
    state = tmp_path / "state.json"
    journal = UserDataEventJournal(evidence, state)
    assert journal.record_event(order_event())
    evidence.write_text(evidence.read_text().replace("FILLED", "NEW"), encoding="utf-8")

    with pytest.raises(ValueError, match="hash mismatch"):
        UserDataEventJournal(evidence, state)


def test_user_data_journal_rejects_state_that_disagrees_with_chain(tmp_path: Path) -> None:
    evidence = tmp_path / "events.jsonl"
    state = tmp_path / "state.json"
    journal = UserDataEventJournal(evidence, state)
    assert journal.record_event(order_event())
    document = json.loads(state.read_text(encoding="utf-8"))
    document["accepted_event_count"] = 0
    state.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="state and journal mismatch"):
        UserDataEventJournal(evidence, state)


def test_user_data_journal_counts_every_attempt_after_first_as_reconnect(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state.json"
    journal = UserDataEventJournal(tmp_path / "events.jsonl", state)
    journal.note_connection_attempt()
    journal.note_connection_attempt()

    current = json.loads(state.read_text(encoding="utf-8"))
    assert current["connection_attempt_count"] == 2
    assert current["reconnect_count"] == 1


class FakeClient:
    def __init__(self) -> None:
        self.keepalives: list[str] = []

    def create_listen_key(self) -> str:
        return "a" * 32

    def keepalive_listen_key(self, listen_key: str) -> None:
        self.keepalives.append(listen_key)

    def close_listen_key(self, listen_key: str) -> None:
        del listen_key


class FakeConnection:
    def __init__(self) -> None:
        self.timeout: float | None = None
        self.sent: list[bytes] = []

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exception_type, exception, traceback

    def settimeout(self, value: float) -> None:
        self.timeout = value

    def sendall(self, payload: bytes) -> None:
        self.sent.append(payload)


def test_user_data_connection_consumes_supported_event_and_answers_ping(
    tmp_path: Path,
) -> None:
    connection = FakeConnection()
    paths: list[tuple[str, str]] = []
    frames: Iterator[tuple[int, bytes]] = iter(
        [
            (0x9, b"ping"),
            (0x1, json.dumps(order_event()).encode()),
            (0x8, b""),
        ]
    )

    def opener(host: str, path: str) -> FakeConnection:
        paths.append((host, path))
        return connection

    journal = UserDataEventJournal(tmp_path / "events.jsonl", tmp_path / "state.json")
    observer = UserDataStreamObserver(
        FakeClient(),
        journal,
        websocket_opener=opener,
        frame_reader=lambda _connection: next(frames),
    )
    observer._consume_connection("a" * 32)

    assert paths == [(TESTNET_STREAM_HOST, "/private/ws/" + "a" * 32)]
    assert connection.timeout == 5
    assert connection.sent and connection.sent[0][0] & 0x0F == 0xA
    current = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert current["connection_count"] == 1
    assert current["event_type_counts"] == {"ORDER_TRADE_UPDATE": 1}


def test_exact_stream_opener_rejects_unrouted_private_destination() -> None:
    with pytest.raises(ProbeError, match="DESTINATION_DENIED"):
        open_testnet_stream_websocket(TESTNET_STREAM_HOST, "/private/ws/a?redirect=1")
