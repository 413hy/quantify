"""One strategy core shared verbatim by live adapters and replay/backtest adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ai_quant.strategy.fusion import (
    FusionDecision,
    OrderFlowConfirmation,
    PriceActionArm,
    fuse_pa_order_flow,
)


@dataclass(frozen=True, slots=True)
class StrategyFrame:
    event_time: datetime
    arms: tuple[PriceActionArm, ...]
    confirmation: OrderFlowConfirmation | None
    data_healthy: bool


class StrategyCore:
    def evaluate(self, frame: StrategyFrame) -> FusionDecision:
        return fuse_pa_order_flow(
            frame.arms,
            frame.confirmation,
            data_healthy=frame.data_healthy,
        )


def run_strategy_frames(
    frames: list[StrategyFrame], *, core: StrategyCore | None = None
) -> tuple[FusionDecision, ...]:
    engine = core or StrategyCore()
    ordered = sorted(enumerate(frames), key=lambda pair: (pair[1].event_time, pair[0]))
    return tuple(engine.evaluate(frame) for _, frame in ordered)
