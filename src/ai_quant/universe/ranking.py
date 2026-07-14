"""Exact Decimal implementation of the closed Top-10 scoring formula."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
WEIGHTS = {
    "liquidity": Decimal("0.30"),
    "depth": Decimal("0.30"),
    "spread": Decimal("0.20"),
    "activity": Decimal("0.10"),
    "completeness": Decimal("0.10"),
}


@dataclass(frozen=True, slots=True)
class UniverseInput:
    symbol: str
    quote_notional_15m: Decimal
    twap_bid_depth_10bps: Decimal
    twap_ask_depth_10bps: Decimal
    median_spread_bps: Decimal
    trade_count_15m: int
    input_completeness_pct: Decimal

    def __post_init__(self) -> None:
        if (
            self.quote_notional_15m < 0
            or self.twap_bid_depth_10bps <= 0
            or self.twap_ask_depth_10bps <= 0
            or self.median_spread_bps < 0
            or self.trade_count_15m < 0
            or not Decimal(0) <= self.input_completeness_pct <= Decimal(100)
        ):
            raise ValueError("universe input is outside its valid domain")


@dataclass(frozen=True, slots=True)
class ComponentEvidence:
    raw: Decimal
    q05: Decimal
    q95: Decimal
    winsorized: Decimal
    normalized: Decimal
    weighted: Decimal


@dataclass(frozen=True, slots=True)
class UniverseRank:
    symbol: str
    score: Decimal
    components: dict[str, ComponentEvidence]


@dataclass(frozen=True, slots=True)
class UniverseSnapshot:
    ranking: tuple[UniverseRank, ...]
    eligible_count: int
    reduced_pool_alert: bool


def _type7(values: list[Decimal], probability: Decimal) -> Decimal:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    h = Decimal(len(ordered) - 1) * probability
    lower = int(h)
    fraction = h - Decimal(lower)
    upper = min(lower + 1, len(ordered) - 1)
    return (Decimal(1) - fraction) * ordered[lower] + fraction * ordered[upper]


def _normalize(raw_by_symbol: dict[str, Decimal]) -> dict[str, ComponentEvidence]:
    values = list(raw_by_symbol.values())
    q05 = _type7(values, Decimal("0.05"))
    q95 = _type7(values, Decimal("0.95"))
    winsorized = {symbol: min(max(value, q05), q95) for symbol, value in raw_by_symbol.items()}
    output: dict[str, ComponentEvidence] = {}
    for symbol, raw in raw_by_symbol.items():
        value = winsorized[symbol]
        if len(values) == 1 or q05 == q95:
            percentile = Decimal("0.5")
        else:
            less = sum(other < value for other in winsorized.values())
            equal = sum(other == value for other in winsorized.values())
            percentile = (Decimal(less) + Decimal(equal - 1) / Decimal(2)) / Decimal(
                len(values) - 1
            )
        output[symbol] = ComponentEvidence(
            raw=raw,
            q05=q05,
            q95=q95,
            winsorized=value,
            normalized=percentile * Decimal(100),
            weighted=Decimal(0),
        )
    return output


def rank_universe(inputs: list[UniverseInput]) -> UniverseSnapshot:
    if not inputs:
        return UniverseSnapshot(ranking=(), eligible_count=0, reduced_pool_alert=True)
    if len({item.symbol for item in inputs}) != len(inputs):
        raise ValueError("universe symbols must be unique")
    with localcontext(CONTEXT):
        raw = {
            "liquidity": {
                item.symbol: (Decimal(1) + item.quote_notional_15m).ln() for item in inputs
            },
            "depth": {
                item.symbol: (
                    Decimal(1) + min(item.twap_bid_depth_10bps, item.twap_ask_depth_10bps)
                ).ln()
                for item in inputs
            },
            "spread": {item.symbol: -item.median_spread_bps for item in inputs},
            "activity": {
                item.symbol: (Decimal(1) + Decimal(item.trade_count_15m)).ln() for item in inputs
            },
            "completeness": {item.symbol: item.input_completeness_pct for item in inputs},
        }
        normalized = {name: _normalize(values) for name, values in raw.items()}
        ranks: list[UniverseRank] = []
        for item in inputs:
            components: dict[str, ComponentEvidence] = {}
            score = Decimal(0)
            for name, weight in WEIGHTS.items():
                evidence = normalized[name][item.symbol]
                weighted = evidence.normalized * weight
                components[name] = ComponentEvidence(
                    raw=evidence.raw,
                    q05=evidence.q05,
                    q95=evidence.q95,
                    winsorized=evidence.winsorized,
                    normalized=evidence.normalized,
                    weighted=weighted,
                )
                score += weighted
            ranks.append(UniverseRank(item.symbol, score, components))
        ranks.sort(key=lambda rank: (-rank.score, rank.symbol.encode()))
    return UniverseSnapshot(
        ranking=tuple(ranks),
        eligible_count=len(ranks),
        reduced_pool_alert=len(ranks) < 10,
    )
