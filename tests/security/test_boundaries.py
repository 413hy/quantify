from pathlib import Path

from ai_quant.common.runtime import RuntimeState, RuntimeStatus


def test_default_state_never_allows_new_entries() -> None:
    status = RuntimeStatus()
    assert status.state is RuntimeState.RISK_LOCKED
    assert not status.new_entries_allowed


def test_no_binance_client_implementation_outside_gateway() -> None:
    root = Path(__file__).resolve().parents[2] / "src/ai_quant"
    forbidden = ("httpx.", "requests.", "websockets.", "aiohttp.")
    findings: list[str] = []
    for path in root.rglob("*.py"):
        if "binance_egress" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                findings.append(f"{path}:{marker}")
    assert findings == []
