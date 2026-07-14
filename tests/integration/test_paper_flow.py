from __future__ import annotations

from pathlib import Path

from ai_quant.demo.paper_flow import run_paper_flow


def test_offline_paper_flow_reaches_fill_and_native_protection(tmp_path: Path) -> None:
    result = run_paper_flow(tmp_path)

    assert result["mode"] == "OFFLINE_PAPER"
    assert result["external_requests"] == 0
    assert result["universe_leader"] == "BTCUSDT"
    assert result["order_state"] == "FILLED"
    assert result["filled_quantity"] == result["approved_quantity"]
    assert result["protection_healthy"] is True
    assert result["runtime_state"] == "RISK_LOCKED"
