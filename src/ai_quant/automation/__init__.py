"""Strategy-agnostic automatic trading orchestration."""

from ai_quant.automation.engine import (
    AutomaticTradeEngine,
    AutomaticTradeIntent,
    AutomaticTradeOutcome,
    AutomationEnvironment,
    AutomationLimits,
    AutomationSnapshot,
    ExecutionReceipt,
    GateDecision,
    IntentAction,
    TradeSide,
)
from ai_quant.automation.runner import AutomaticTradeRunner

__all__ = [
    "AutomaticTradeEngine",
    "AutomaticTradeIntent",
    "AutomaticTradeOutcome",
    "AutomaticTradeRunner",
    "AutomationEnvironment",
    "AutomationLimits",
    "AutomationSnapshot",
    "ExecutionReceipt",
    "GateDecision",
    "IntentAction",
    "TradeSide",
]
