"""Exact bounded economics for a small long micro-scalp position."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal

from ai_quant.risk.sizing import maximum_quantity_for_margin_budget


@dataclass(frozen=True, slots=True)
class MicroScalpPlan:
    quantity: Decimal
    entry_assumption: Decimal
    notional: Decimal
    initial_margin: Decimal
    stop_trigger: Decimal
    target_trigger: Decimal
    estimated_target_net_profit: Decimal
    estimated_stop_net_profit: Decimal


def plan_long_micro_scalp(
    *,
    entry_assumption: Decimal,
    margin_budget: Decimal,
    initial_leverage: Decimal,
    step_size: Decimal,
    minimum_quantity: Decimal,
    minimum_notional: Decimal,
    tick_size: Decimal,
    taker_fee_rate: Decimal,
    target_net_profit: Decimal,
    maximum_net_loss: Decimal,
    adverse_exit_slippage_bps: Decimal,
) -> MicroScalpPlan:
    """Plan target/stop triggers after fees and an adverse exit slippage assumption."""
    if margin_budget > Decimal("1"):
        raise ValueError("micro-scalp margin budget exceeds 1 USDT")
    positive = (
        entry_assumption,
        margin_budget,
        initial_leverage,
        step_size,
        minimum_quantity,
        tick_size,
        target_net_profit,
        maximum_net_loss,
    )
    if any(value <= 0 for value in positive):
        raise ValueError("micro-scalp sizing inputs must be positive")
    if minimum_notional < 0 or taker_fee_rate < 0 or adverse_exit_slippage_bps < 0:
        raise ValueError("micro-scalp cost inputs must be non-negative")
    slippage_fraction = adverse_exit_slippage_bps / Decimal(10_000)
    if slippage_fraction >= Decimal(1) or taker_fee_rate >= Decimal(1):
        raise ValueError("micro-scalp cost assumptions are invalid")

    quantity = maximum_quantity_for_margin_budget(
        margin_budget=margin_budget,
        initial_leverage=initial_leverage,
        entry_price=entry_assumption,
        step_size=step_size,
    )
    return plan_long_micro_scalp_for_quantity(
        entry_assumption=entry_assumption,
        quantity=quantity,
        margin_budget=margin_budget,
        initial_leverage=initial_leverage,
        minimum_quantity=minimum_quantity,
        minimum_notional=minimum_notional,
        tick_size=tick_size,
        taker_fee_rate=taker_fee_rate,
        target_net_profit=target_net_profit,
        maximum_net_loss=maximum_net_loss,
        adverse_exit_slippage_bps=adverse_exit_slippage_bps,
    )


def plan_long_micro_scalp_for_quantity(
    *,
    entry_assumption: Decimal,
    quantity: Decimal,
    margin_budget: Decimal,
    initial_leverage: Decimal,
    minimum_quantity: Decimal,
    minimum_notional: Decimal,
    tick_size: Decimal,
    taker_fee_rate: Decimal,
    target_net_profit: Decimal,
    maximum_net_loss: Decimal,
    adverse_exit_slippage_bps: Decimal,
) -> MicroScalpPlan:
    """Replan protection from the actual fill without increasing filled quantity."""
    if margin_budget > Decimal("1"):
        raise ValueError("micro-scalp margin budget exceeds 1 USDT")
    if initial_leverage < 1 or initial_leverage > Decimal("10"):
        raise ValueError("initial leverage exceeds immutable hard cap")
    positive = (
        entry_assumption,
        quantity,
        margin_budget,
        initial_leverage,
        minimum_quantity,
        tick_size,
        target_net_profit,
        maximum_net_loss,
    )
    if any(value <= 0 for value in positive):
        raise ValueError("micro-scalp sizing inputs must be positive")
    if minimum_notional < 0 or taker_fee_rate < 0 or adverse_exit_slippage_bps < 0:
        raise ValueError("micro-scalp cost inputs must be non-negative")
    slippage_fraction = adverse_exit_slippage_bps / Decimal(10_000)
    if slippage_fraction >= Decimal(1) or taker_fee_rate >= Decimal(1):
        raise ValueError("micro-scalp cost assumptions are invalid")

    notional = quantity * entry_assumption
    if quantity < minimum_quantity or notional < minimum_notional:
        raise ValueError("micro-scalp order does not meet exchange minimums")
    if notional / initial_leverage > margin_budget:
        raise ValueError("micro-scalp actual fill exceeds margin budget")

    fee = taker_fee_rate
    assumed_target_exit = (
        target_net_profit / quantity + entry_assumption * (Decimal(1) + fee)
    ) / (Decimal(1) - fee)
    raw_target_trigger = assumed_target_exit / (Decimal(1) - slippage_fraction)
    target_trigger = _ceil_to_step(raw_target_trigger, tick_size)

    assumed_stop_exit = (
        entry_assumption * (Decimal(1) + fee) - maximum_net_loss / quantity
    ) / (Decimal(1) - fee)
    raw_stop_trigger = assumed_stop_exit / (Decimal(1) - slippage_fraction)
    stop_trigger = _ceil_to_step(raw_stop_trigger, tick_size)
    if not stop_trigger < entry_assumption < target_trigger:
        raise ValueError("micro-scalp stop/entry/target structure is invalid")

    target_exit = target_trigger * (Decimal(1) - slippage_fraction)
    stop_exit = stop_trigger * (Decimal(1) - slippage_fraction)
    estimated_target_net = _long_net_profit(
        entry_assumption, target_exit, quantity, fee
    )
    estimated_stop_net = _long_net_profit(entry_assumption, stop_exit, quantity, fee)
    if estimated_target_net < target_net_profit:
        raise ValueError("micro-scalp target does not cover required net profit")
    if estimated_stop_net < -maximum_net_loss:
        raise ValueError("micro-scalp stop exceeds maximum net loss")
    return MicroScalpPlan(
        quantity=quantity,
        entry_assumption=entry_assumption,
        notional=notional,
        initial_margin=notional / initial_leverage,
        stop_trigger=stop_trigger,
        target_trigger=target_trigger,
        estimated_target_net_profit=estimated_target_net,
        estimated_stop_net_profit=estimated_stop_net,
    )


def _ceil_to_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def _long_net_profit(
    entry_price: Decimal,
    exit_price: Decimal,
    quantity: Decimal,
    fee_rate: Decimal,
) -> Decimal:
    return quantity * (exit_price - entry_price) - quantity * fee_rate * (
        entry_price + exit_price
    )
