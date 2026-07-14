"""Closed-bar-only Price Action primitives using exact Decimal arithmetic."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise


class Regime(StrEnum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    TRANSITION = "TRANSITION"
    INVALID = "INVALID"


class Structure(StrEnum):
    HH_HL = "HH_HL"
    LH_LL = "LH_LL"
    COMPRESSION = "COMPRESSION"
    RANGE_BOUND = "RANGE_BOUND"
    UNCONFIRMED = "UNCONFIRMED"


class Direction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


class SwingKind(StrEnum):
    HIGH = "HIGH"
    LOW = "LOW"


@dataclass(frozen=True, slots=True)
class ClosedBar:
    symbol: str
    timeframe: str
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    closed: bool = True

    def __post_init__(self) -> None:
        if not self.closed:
            raise ValueError("Price Action accepts only closed bars")
        if any(
            value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value)
            for value in (self.open_time, self.close_time)
        ):
            raise ValueError("bar times must be timezone-aware UTC")
        if (
            self.close_time <= self.open_time
            or self.low <= 0
            or self.high < self.low
            or self.high < max(self.open, self.close)
            or self.low > min(self.open, self.close)
            or self.volume < 0
        ):
            raise ValueError("invalid OHLCV bar")


@dataclass(frozen=True, slots=True)
class ConfirmedSwing:
    swing_id: str
    kind: SwingKind
    price: Decimal
    open_time: datetime
    confirm_time: datetime
    atr: Decimal


@dataclass(frozen=True, slots=True)
class PriceActionFrame:
    as_of: datetime
    regime: Regime
    structure: Structure
    direction: Direction
    atr: Decimal | None
    efficiency_ratio: Decimal | None
    reason_codes: tuple[str, ...]


def true_ranges(bars: list[ClosedBar]) -> tuple[Decimal | None, ...]:
    if not bars:
        return ()
    values: list[Decimal | None] = [None]
    for previous, current in pairwise(bars):
        values.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return tuple(values)


def simple_atr(bars: list[ClosedBar], period: int) -> tuple[Decimal | None, ...]:
    if period < 1:
        raise ValueError("ATR period must be positive")
    ranges = true_ranges(bars)
    result: list[Decimal | None] = []
    for index in range(len(ranges)):
        window = ranges[max(1, index - period + 1) : index + 1]
        if len(window) < period or any(value is None for value in window):
            result.append(None)
        else:
            result.append(
                sum((value for value in window if value is not None), Decimal(0)) / Decimal(period)
            )
    return tuple(result)


def efficiency_ratio(bars: list[ClosedBar], lookback: int) -> Decimal | None:
    if lookback < 1 or len(bars) <= lookback:
        return None
    selected = bars[-(lookback + 1) :]
    denominator = sum(
        (abs(current.close - previous.close) for previous, current in pairwise(selected)),
        Decimal(0),
    )
    if denominator <= 0:
        return None
    return abs(selected[-1].close - selected[0].close) / denominator


def confirmed_swings(
    bars: list[ClosedBar],
    atr_values: tuple[Decimal | None, ...],
    *,
    left: int,
    right: int,
) -> tuple[ConfirmedSwing, ...]:
    if left < 1 or right < 1 or len(atr_values) != len(bars):
        raise ValueError("invalid swing inputs")
    swings: list[ConfirmedSwing] = []
    for index in range(left, len(bars) - right):
        candidate = bars[index]
        atr = atr_values[index]
        if atr is None or atr <= 0:
            continue
        is_high = candidate.high > max(
            bar.high for bar in bars[index - left : index]
        ) and candidate.high >= max(bar.high for bar in bars[index + 1 : index + right + 1])
        is_low = candidate.low < min(
            bar.low for bar in bars[index - left : index]
        ) and candidate.low <= min(bar.low for bar in bars[index + 1 : index + right + 1])
        if is_high and is_low:
            high_prominence = candidate.high - max(bar.high for bar in bars[index - left : index])
            low_prominence = min(bar.low for bar in bars[index - left : index]) - candidate.low
            if high_prominence == low_prominence:
                continue
            is_high = high_prominence > low_prominence
            is_low = not is_high
        kind = SwingKind.HIGH if is_high else SwingKind.LOW if is_low else None
        if kind is None:
            continue
        price = candidate.high if kind is SwingKind.HIGH else candidate.low
        identity = (
            f"{candidate.symbol}|{candidate.timeframe}|{candidate.open_time.isoformat()}|{kind}"
        ).encode()
        swing = ConfirmedSwing(
            swing_id=hashlib.sha256(identity).hexdigest(),
            kind=kind,
            price=price,
            open_time=candidate.open_time,
            confirm_time=bars[index + right].close_time,
            atr=atr,
        )
        if swings and swings[-1].kind is kind:
            previous = swings[-1]
            more_extreme = (
                price > previous.price if kind is SwingKind.HIGH else price < previous.price
            )
            if more_extreme:
                swings[-1] = swing
        else:
            swings.append(swing)
    return tuple(swings)


def classify_structure(
    swings: tuple[ConfirmedSwing, ...],
    *,
    required_pairs: int,
    equal_tolerance_atr: Decimal,
) -> Structure:
    highs = [swing for swing in swings if swing.kind is SwingKind.HIGH]
    lows = [swing for swing in swings if swing.kind is SwingKind.LOW]
    if required_pairs < 1 or len(highs) < required_pairs + 1 or len(lows) < required_pairs + 1:
        return Structure.UNCONFIRMED

    def trend(values: list[ConfirmedSwing]) -> int:
        relevant = values[-(required_pairs + 1) :]
        directions: list[int] = []
        for older, newer in pairwise(relevant):
            tolerance = newer.atr * equal_tolerance_atr
            if newer.price > older.price + tolerance:
                directions.append(1)
            elif newer.price < older.price - tolerance:
                directions.append(-1)
            else:
                directions.append(0)
        return directions[0] if directions and len(set(directions)) == 1 else 0

    high_trend = trend(highs)
    low_trend = trend(lows)
    if high_trend == low_trend == 1:
        return Structure.HH_HL
    if high_trend == low_trend == -1:
        return Structure.LH_LL
    return Structure.UNCONFIRMED


def analyze_price_action(
    bars: list[ClosedBar],
    *,
    atr_period: int,
    efficiency_lookback: int,
    efficiency_threshold: Decimal,
    slope_lookback: int,
    slope_threshold_atr: Decimal,
    swing_left: int,
    swing_right: int,
    required_pairs: int,
    equal_tolerance_atr: Decimal,
) -> PriceActionFrame:
    if not bars:
        raise ValueError("at least one closed bar is required")
    atrs = simple_atr(bars, atr_period)
    atr = atrs[-1]
    efficiency = efficiency_ratio(bars, efficiency_lookback)
    swings = confirmed_swings(bars, atrs, left=swing_left, right=swing_right)
    structure = classify_structure(
        swings, required_pairs=required_pairs, equal_tolerance_atr=equal_tolerance_atr
    )
    if atr is None or atr <= 0 or efficiency is None or len(bars) <= slope_lookback:
        return PriceActionFrame(
            bars[-1].close_time,
            Regime.INVALID,
            structure,
            Direction.NEUTRAL,
            atr,
            efficiency,
            ("PA_REGIME_INVALID",),
        )
    slope = (bars[-1].close - bars[-1 - slope_lookback].close) / (atr * Decimal(slope_lookback))
    if (
        structure is Structure.HH_HL
        and efficiency >= efficiency_threshold
        and slope >= slope_threshold_atr
    ):
        regime, direction = Regime.TREND_UP, Direction.LONG
    elif (
        structure is Structure.LH_LL
        and efficiency >= efficiency_threshold
        and slope <= -slope_threshold_atr
    ):
        regime, direction = Regime.TREND_DOWN, Direction.SHORT
    else:
        regime, direction = Regime.TRANSITION, Direction.NEUTRAL
    reasons = () if direction is not Direction.NEUTRAL else ("PA_DIRECTION_NEUTRAL",)
    return PriceActionFrame(
        bars[-1].close_time, regime, structure, direction, atr, efficiency, reasons
    )
