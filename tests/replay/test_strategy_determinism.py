from __future__ import annotations

from ai_quant.strategy.core import StrategyCore, StrategyFrame, run_strategy_frames
from tests.market_fixtures import BASE_TIME
from tests.unit.test_strategy_fusion import arm, confirmation


def test_live_and_backtest_adapters_use_identical_strategy_core() -> None:
    frame = StrategyFrame(BASE_TIME, (arm(),), confirmation(), True)
    live = StrategyCore().evaluate(frame)
    replay = run_strategy_frames([frame])[0]

    assert live == replay
    assert live.candidate is not None


def test_replay_preserves_event_time_order_without_future_input() -> None:
    earlier = StrategyFrame(BASE_TIME, (arm(),), None, True)
    later = StrategyFrame(BASE_TIME.replace(second=1), (arm(),), confirmation(), True)

    results = run_strategy_frames([later, earlier])

    assert results[0].candidate is None
    assert results[1].candidate is not None
