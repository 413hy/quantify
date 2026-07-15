"""Causal replay of the Testnet V3 decision stream using recorded 10-second marks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class ReplayParameters:
    name: str
    confirmation_rounds: int = 2
    minimum_quality_score: Decimal = Decimal("2.00")
    minimum_activity_ratio: Decimal = Decimal("0.50")
    maximum_spread_bps: Decimal = Decimal("8.00")
    minimum_pa_alignment_count: int = 1
    activity_lookback_rounds: int = 12
    minimum_activity_samples: int = 6
    minimum_target_bps: Decimal | None = None
    target_bps_override: Decimal | None = None

    def __post_init__(self) -> None:
        if self.minimum_target_bps is not None and self.target_bps_override is not None:
            raise ValueError("replay target controls are mutually exclusive")
        for value in (self.minimum_target_bps, self.target_bps_override):
            if value is not None and value <= 0:
                raise ValueError("replay target distance must be positive")


@dataclass(frozen=True, slots=True)
class ReplayPosition:
    symbol: str
    direction: str
    entered_at: datetime
    entry: Decimal
    stop: Decimal
    target: Decimal
    quality_score: Decimal
    activity_ratio: Decimal
    spread_bps: Decimal
    pa_alignment_count: int


@dataclass(frozen=True, slots=True)
class ReplayTrade:
    position: ReplayPosition
    exited_at: datetime
    exit_reason: str
    exit_price: Decimal
    net_bps: Decimal


def replay_observations(
    documents: list[dict[str, Any]],
    parameters: ReplayParameters,
    *,
    start_at: datetime,
    maximum_positions: int = 5,
    cooldown_seconds: int = 60,
    round_trip_fee_bps: Decimal = Decimal("8"),
    adverse_exit_slippage_bps: Decimal = Decimal("2"),
) -> dict[str, Any]:
    """Replay entries causally and close only when a later recorded mid crosses protection."""
    observations = sorted(
        (
            item
            for item in documents
            if item.get("record_type") == "SIGNAL_OBSERVATION"
            and _time(item.get("observed_at")) >= start_at
        ),
        key=lambda item: (_time(item.get("observed_at")), str(item.get("symbol"))),
    )
    histories: dict[str, list[Decimal]] = {}
    pending: dict[str, tuple[str, int]] = {}
    active: dict[str, ReplayPosition] = {}
    last_exit: dict[str, datetime] = {}
    trades: list[ReplayTrade] = []
    candidate_count = 0
    confirmed_count = 0
    maximum_concurrent = 0

    for observation in observations:
        symbol = str(observation["symbol"])
        observed_at = _time(observation["observed_at"])
        mid = Decimal(str(observation["mid_price"]))
        position = active.get(symbol)
        if position is not None:
            exit_reason = _exit_reason(position, mid)
            if exit_reason is not None:
                exit_price = position.target if exit_reason == "TAKE_PROFIT" else position.stop
                trades.append(
                    ReplayTrade(
                        position=position,
                        exited_at=observed_at,
                        exit_reason=exit_reason,
                        exit_price=exit_price,
                        net_bps=_net_bps(
                            position,
                            exit_price,
                            round_trip_fee_bps=round_trip_fee_bps,
                            adverse_exit_slippage_bps=adverse_exit_slippage_bps,
                        ),
                    )
                )
                del active[symbol]
                last_exit[symbol] = observed_at
            else:
                pending.pop(symbol, None)

        current_activity = Decimal(str(observation["order_flow"]["aggressive_notional"]))
        history = histories.setdefault(symbol, [])
        history.append(current_activity)
        del history[: -parameters.activity_lookback_rounds]
        median = _median(sorted(history))
        activity_ratio = Decimal(0) if median <= 0 else current_activity / median
        plan_value = observation.get("testnet_experimental_plan")
        plan = plan_value if isinstance(plan_value, dict) else None
        if plan is None or symbol in active:
            pending.pop(symbol, None)
            continue
        candidate_count += 1
        quality = Decimal(str(plan.get("signal_quality_score", "0")))
        spread = Decimal(str(plan.get("observed_spread_bps", observation["spread_bps"])))
        pa_count = int(plan.get("pa_alignment_count", 0))
        if (
            len(history) < parameters.minimum_activity_samples
            or activity_ratio < parameters.minimum_activity_ratio
            or quality < parameters.minimum_quality_score
            or spread > parameters.maximum_spread_bps
            or pa_count < parameters.minimum_pa_alignment_count
        ):
            pending.pop(symbol, None)
            continue
        direction = str(plan["direction"])
        previous_direction, previous_count = pending.get(symbol, ("", 0))
        count = previous_count + 1 if previous_direction == direction else 1
        pending[symbol] = (direction, count)
        if count < parameters.confirmation_rounds:
            continue
        confirmed_count += 1
        if len(active) >= maximum_positions:
            continue
        if symbol in last_exit and observed_at - last_exit[symbol] < timedelta(
            seconds=cooldown_seconds
        ):
            continue
        entry = Decimal(str(plan["entry_reference"]))
        stop = Decimal(str(plan["stop_anchor"]))
        target = Decimal(str(plan["target_reference"]))
        if parameters.target_bps_override is not None:
            target_bps = parameters.target_bps_override
            distance = entry * target_bps / Decimal(10_000)
            target = entry + distance if direction == "LONG" else entry - distance
        elif parameters.minimum_target_bps is not None:
            recorded_target_bps = abs(target - entry) / entry * Decimal(10_000)
            target_bps = max(recorded_target_bps, parameters.minimum_target_bps)
            distance = entry * target_bps / Decimal(10_000)
            target = entry + distance if direction == "LONG" else entry - distance
        active[symbol] = ReplayPosition(
            symbol=symbol,
            direction=direction,
            entered_at=observed_at,
            entry=entry,
            stop=stop,
            target=target,
            quality_score=quality,
            activity_ratio=activity_ratio,
            spread_bps=spread,
            pa_alignment_count=pa_count,
        )
        pending.pop(symbol, None)
        maximum_concurrent = max(maximum_concurrent, len(active))

    positive = [trade.net_bps for trade in trades if trade.net_bps > 0]
    negative = [trade.net_bps for trade in trades if trade.net_bps < 0]
    net_bps = sum((trade.net_bps for trade in trades), Decimal(0))
    by_symbol: dict[str, dict[str, Decimal | int]] = {}
    for trade in trades:
        summary = by_symbol.setdefault(
            trade.position.symbol, {"closed_trades": 0, "winning_trades": 0, "net_bps": Decimal(0)}
        )
        summary["closed_trades"] = int(summary["closed_trades"]) + 1
        summary["winning_trades"] = int(summary["winning_trades"]) + int(trade.net_bps > 0)
        summary["net_bps"] = Decimal(summary["net_bps"]) + trade.net_bps
    return {
        "parameters": {
            "name": parameters.name,
            "confirmation_rounds": parameters.confirmation_rounds,
            "minimum_quality_score": format(parameters.minimum_quality_score, "f"),
            "minimum_activity_ratio": format(parameters.minimum_activity_ratio, "f"),
            "maximum_spread_bps": format(parameters.maximum_spread_bps, "f"),
            "minimum_pa_alignment_count": parameters.minimum_pa_alignment_count,
            "minimum_target_bps": (
                None
                if parameters.minimum_target_bps is None
                else format(parameters.minimum_target_bps, "f")
            ),
            "target_bps_override": (
                None
                if parameters.target_bps_override is None
                else format(parameters.target_bps_override, "f")
            ),
        },
        "observation_count": len(observations),
        "raw_candidate_count": candidate_count,
        "confirmed_candidate_count": confirmed_count,
        "closed_trades": len(trades),
        "winning_trades": len(positive),
        "win_rate": _ratio(len(positive), len(trades)),
        "target_count": sum(trade.exit_reason == "TAKE_PROFIT" for trade in trades),
        "stop_count": sum(trade.exit_reason == "STOP_LOSS" for trade in trades),
        "open_positions_at_dataset_end": len(active),
        "maximum_concurrent_positions": maximum_concurrent,
        "net_bps": format(net_bps, "f"),
        "net_pnl_at_50_usdt_notional": format(net_bps / Decimal(10_000) * Decimal(50), "f"),
        "profit_factor": _profit_factor(positive, negative),
        "by_symbol": {
            symbol: {
                "closed_trades": int(values["closed_trades"]),
                "winning_trades": int(values["winning_trades"]),
                "net_bps": format(Decimal(values["net_bps"]), "f"),
            }
            for symbol, values in sorted(by_symbol.items())
        },
        "open_positions": [
            {
                "symbol": position.symbol,
                "direction": position.direction,
                "entered_at": position.entered_at.isoformat(),
            }
            for position in sorted(active.values(), key=lambda item: item.symbol)
        ],
        "trades": [
            {
                "symbol": trade.position.symbol,
                "direction": trade.position.direction,
                "entered_at": trade.position.entered_at.isoformat(),
                "exited_at": trade.exited_at.isoformat(),
                "exit_reason": trade.exit_reason,
                "entry_price": format(trade.position.entry, "f"),
                "exit_price": format(trade.exit_price, "f"),
                "quality_score": format(trade.position.quality_score, "f"),
                "activity_ratio": format(trade.position.activity_ratio, "f"),
                "spread_bps": format(trade.position.spread_bps, "f"),
                "pa_alignment_count": trade.position.pa_alignment_count,
                "net_bps": format(trade.net_bps, "f"),
            }
            for trade in trades
        ],
    }


def _exit_reason(position: ReplayPosition, mid: Decimal) -> str | None:
    if position.direction == "LONG":
        if mid <= position.stop:
            return "STOP_LOSS"
        if mid >= position.target:
            return "TAKE_PROFIT"
    elif position.direction == "SHORT":
        if mid >= position.stop:
            return "STOP_LOSS"
        if mid <= position.target:
            return "TAKE_PROFIT"
    else:
        raise ValueError("replay direction is invalid")
    return None


def _net_bps(
    position: ReplayPosition,
    exit_price: Decimal,
    *,
    round_trip_fee_bps: Decimal,
    adverse_exit_slippage_bps: Decimal,
) -> Decimal:
    sign = Decimal(1) if position.direction == "LONG" else Decimal(-1)
    gross = sign * (exit_price - position.entry) / position.entry * Decimal(10_000)
    return gross - round_trip_fee_bps - adverse_exit_slippage_bps


def _median(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal(0)
    midpoint = len(values) // 2
    if len(values) % 2:
        return values[midpoint]
    return (values[midpoint - 1] + values[midpoint]) / Decimal(2)


def _time(value: object) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _ratio(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "0"
    return format(Decimal(numerator) / Decimal(denominator), "f")


def _profit_factor(positive: list[Decimal], negative: list[Decimal]) -> str | None:
    loss = abs(sum(negative, Decimal(0)))
    if loss == 0:
        return None
    return format(sum(positive, Decimal(0)) / loss, "f")
