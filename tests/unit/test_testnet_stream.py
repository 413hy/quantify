from __future__ import annotations

from ai_quant.binance_egress.testnet_stream import AggregateTradeWindow


def trade(symbol: str, trade_id: int, occurred_at: int, nq: str = "1") -> dict[str, object]:
    return {
        "s": symbol,
        "a": trade_id,
        "p": "100",
        "q": nq,
        "nq": nq,
        "f": trade_id,
        "l": trade_id,
        "T": occurred_at,
        "m": False,
    }


def test_window_retains_only_requested_recent_symbol_trades() -> None:
    window = AggregateTradeWindow(("SOLUSDT", "BNBUSDT"))
    assert window.ingest(trade("SOLUSDT", 1, 1_000))
    assert window.ingest(trade("SOLUSDT", 2, 2_500))
    assert window.ingest(trade("BNBUSDT", 3, 2_900))

    assert window.snapshot("SOLUSDT", now_ms=3_000, maximum_age_ms=1_000) == [
        trade("SOLUSDT", 2, 2_500)
    ]


def test_window_rejects_missing_or_invalid_normal_quantity() -> None:
    window = AggregateTradeWindow(("SOLUSDT",))
    missing = trade("SOLUSDT", 1, 1_000)
    del missing["nq"]

    assert not window.ingest(missing)
    assert not window.ingest(trade("SOLUSDT", 2, 1_000, "-1"))
    assert not window.ingest(trade("XRPUSDT", 3, 1_000))
    assert window.snapshot("SOLUSDT", now_ms=2_000, maximum_age_ms=2_000) == []
