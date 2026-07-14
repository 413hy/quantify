"""Exact hard-limit sizing for USDT linear contracts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

BINANCE_USDM_LEVERAGE_PROTOCOL_MAXIMUM = Decimal("125")


@dataclass(frozen=True, slots=True)
class ConfiguredRiskLimits:
    per_trade_initial_stop_pct: Decimal = Decimal("0.005")
    total_open_risk_pct: Decimal = Decimal("0.020")
    correlation_cluster_risk_pct: Decimal = Decimal("0.010")
    utc_daily_net_loss_pct: Decimal = Decimal("0.040")
    intraday_equity_drawdown_pct: Decimal = Decimal("0.050")
    effective_leverage: Decimal = Decimal("125")
    concurrent_positions: int = 10

    def __post_init__(self) -> None:
        values_and_caps = (
            (self.per_trade_initial_stop_pct, Decimal("0.005")),
            (self.total_open_risk_pct, Decimal("0.020")),
            (self.correlation_cluster_risk_pct, Decimal("0.010")),
            (self.utc_daily_net_loss_pct, Decimal("0.040")),
            (self.intraday_equity_drawdown_pct, Decimal("0.050")),
        )
        if any(value < 0 or value > cap for value, cap in values_and_caps):
            raise ValueError("configured risk limit exceeds immutable hard cap")
        if not 1 <= self.effective_leverage <= BINANCE_USDM_LEVERAGE_PROTOCOL_MAXIMUM:
            raise ValueError("exchange-selected leverage exceeds Binance protocol range")
        if not 0 <= self.concurrent_positions <= 10:
            raise ValueError("configured position count exceeds immutable hard cap")


@dataclass(frozen=True, slots=True)
class EffectiveRiskLimits:
    per_trade_initial_stop_pct: Decimal
    total_open_risk_pct: Decimal
    correlation_cluster_risk_pct: Decimal
    utc_daily_net_loss_pct: Decimal
    intraday_equity_drawdown_pct: Decimal
    effective_leverage: Decimal
    concurrent_positions: int


def effective_limits(configured: ConfiguredRiskLimits, multiplier: Decimal) -> EffectiveRiskLimits:
    if not Decimal(0) <= multiplier <= Decimal(1):
        raise ValueError("risk multiplier must be within [0,1]")
    return EffectiveRiskLimits(
        per_trade_initial_stop_pct=configured.per_trade_initial_stop_pct * multiplier,
        total_open_risk_pct=configured.total_open_risk_pct * multiplier,
        correlation_cluster_risk_pct=(configured.correlation_cluster_risk_pct * multiplier),
        utc_daily_net_loss_pct=configured.utc_daily_net_loss_pct * multiplier,
        intraday_equity_drawdown_pct=(configured.intraday_equity_drawdown_pct * multiplier),
        # Leverage follows the current exchange/account bracket. The risk
        # multiplier scales loss budgets, not the exchange-selected initial leverage.
        effective_leverage=configured.effective_leverage,
        concurrent_positions=configured.concurrent_positions,
    )


@dataclass(frozen=True, slots=True)
class RiskSizingInput:
    equity: Decimal
    entry_assumption: Decimal
    stop_trigger: Decimal
    entry_slippage_per_unit: Decimal
    emergency_exit_slippage_per_unit: Decimal
    entry_fee_per_unit: Decimal
    exit_fee_per_unit: Decimal
    funding_buffer_per_unit: Decimal
    reserved_episode_risk: Decimal
    reserved_all_risk: Decimal
    reserved_cluster_risk: Decimal
    current_daily_loss: Decimal
    current_drawdown: Decimal
    current_gross_notional: Decimal
    current_positions: int
    step_size: Decimal
    minimum_quantity: Decimal
    minimum_notional: Decimal
    maximum_executable_quantity: Decimal


@dataclass(frozen=True, slots=True)
class RiskSizingDecision:
    approved: bool
    quantity: Decimal
    loss_per_unit: Decimal
    reserved_risk: Decimal
    available_risk: Decimal
    reason_codes: tuple[str, ...]


def maximum_quantity_for_margin_budget(
    *,
    margin_budget: Decimal,
    initial_leverage: Decimal,
    entry_price: Decimal,
    step_size: Decimal,
) -> Decimal:
    """Convert a per-order margin ceiling to a floor-quantized quantity cap."""
    if margin_budget <= 0 or entry_price <= 0 or step_size <= 0:
        raise ValueError("margin sizing inputs must be positive")
    if not 1 <= initial_leverage <= BINANCE_USDM_LEVERAGE_PROTOCOL_MAXIMUM:
        raise ValueError("initial leverage exceeds Binance protocol range")
    raw_quantity = margin_budget * initial_leverage / entry_price
    return (raw_quantity / step_size).to_integral_value(rounding=ROUND_FLOOR) * step_size


def size_entry(
    request: RiskSizingInput,
    *,
    configured: ConfiguredRiskLimits | None = None,
    risk_multiplier: Decimal,
) -> RiskSizingDecision:
    configured = configured or ConfiguredRiskLimits()
    values = (
        request.equity,
        request.entry_assumption,
        request.stop_trigger,
        request.entry_slippage_per_unit,
        request.emergency_exit_slippage_per_unit,
        request.entry_fee_per_unit,
        request.exit_fee_per_unit,
        request.funding_buffer_per_unit,
        request.reserved_episode_risk,
        request.reserved_all_risk,
        request.reserved_cluster_risk,
        request.current_daily_loss,
        request.current_drawdown,
        request.current_gross_notional,
        request.step_size,
        request.minimum_quantity,
        request.minimum_notional,
        request.maximum_executable_quantity,
    )
    if any(value < 0 for value in values if isinstance(value, Decimal)):
        return _denied("RISK_INPUT_INVALID")
    if (
        request.equity <= 0
        or request.step_size <= 0
        or request.entry_assumption <= 0
        or request.stop_trigger <= 0
    ):
        return _denied("RISK_INPUT_INVALID")
    limits = effective_limits(configured, risk_multiplier)
    if request.current_positions >= limits.concurrent_positions:
        return _denied("RISK_POSITION_COUNT_LIMIT")
    loss_per_unit = (
        abs(request.entry_assumption - request.stop_trigger)
        + request.entry_slippage_per_unit
        + request.emergency_exit_slippage_per_unit
        + request.entry_fee_per_unit
        + request.exit_fee_per_unit
        + request.funding_buffer_per_unit
    )
    if loss_per_unit <= 0:
        return _denied("RISK_STOP_DISTANCE_INVALID")
    trade = request.equity * limits.per_trade_initial_stop_pct - request.reserved_episode_risk
    total = request.equity * limits.total_open_risk_pct - request.reserved_all_risk
    cluster = request.equity * limits.correlation_cluster_risk_pct - request.reserved_cluster_risk
    daily = request.equity * limits.utc_daily_net_loss_pct - request.current_daily_loss
    drawdown = request.equity * limits.intraday_equity_drawdown_pct - request.current_drawdown
    available = max(Decimal(0), min(trade, total, cluster, daily, drawdown))
    if available <= 0:
        return RiskSizingDecision(
            False, Decimal(0), loss_per_unit, Decimal(0), available, ("RISK_BUDGET_EXHAUSTED",)
        )
    quantity_by_risk = available / loss_per_unit
    leverage_headroom = max(
        Decimal(0),
        request.equity * limits.effective_leverage - request.current_gross_notional,
    )
    quantity_by_leverage = leverage_headroom / request.entry_assumption
    raw_quantity = min(
        quantity_by_risk,
        quantity_by_leverage,
        request.maximum_executable_quantity,
    )
    quantity = (raw_quantity / request.step_size).to_integral_value(
        rounding=ROUND_FLOOR
    ) * request.step_size
    if quantity < request.minimum_quantity:
        return RiskSizingDecision(
            False, Decimal(0), loss_per_unit, Decimal(0), available, ("RISK_MIN_QUANTITY",)
        )
    if quantity * request.entry_assumption < request.minimum_notional:
        return RiskSizingDecision(
            False, Decimal(0), loss_per_unit, Decimal(0), available, ("RISK_MIN_NOTIONAL",)
        )
    reserved = quantity * loss_per_unit
    return RiskSizingDecision(True, quantity, loss_per_unit, reserved, available, ())


def _denied(reason: str) -> RiskSizingDecision:
    return RiskSizingDecision(False, Decimal(0), Decimal(0), Decimal(0), Decimal(0), (reason,))
