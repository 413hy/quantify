"""Top-10 membership hysteresis with residence, confirmation, and managed sets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ai_quant.universe.ranking import UniverseSnapshot


@dataclass(frozen=True, slots=True)
class MembershipView:
    active: tuple[str, ...]
    standby: tuple[str, ...]
    managed_positions: tuple[str, ...]
    reduced_pool_alert: bool


class MembershipController:
    def __init__(
        self,
        *,
        size: int = 10,
        minimum_residence: timedelta = timedelta(minutes=60),
        replacement_margin: Decimal = Decimal("5.00"),
        confirmations: int = 2,
    ) -> None:
        self.size = size
        self.minimum_residence = minimum_residence
        self.replacement_margin = replacement_margin
        self.confirmations = confirmations
        self._active: list[str] = []
        self._entered_at: dict[str, datetime] = {}
        self._streaks: dict[str, int] = {}

    def apply(
        self,
        snapshot: UniverseSnapshot,
        *,
        computed_at: datetime,
        immediately_ineligible: set[str] | None = None,
        managed_positions: set[str] | None = None,
    ) -> MembershipView:
        self._require_utc(computed_at)
        ineligible = immediately_ineligible or set()
        managed = managed_positions or set()
        ranking = list(snapshot.ranking)
        score = {rank.symbol: rank.score for rank in ranking}
        raw_top = [rank.symbol for rank in ranking[: self.size]]
        for symbol in score:
            self._streaks[symbol] = self._streaks.get(symbol, 0) + 1 if symbol in raw_top else 0
        self._active = [symbol for symbol in self._active if symbol not in ineligible]
        for symbol in raw_top:
            if len(self._active) >= self.size:
                break
            if (
                symbol not in ineligible
                and symbol not in self._active
                and self._streaks[symbol] >= self.confirmations
            ):
                self._active.append(symbol)
                self._entered_at[symbol] = computed_at
        if len(self._active) >= self.size:
            challengers = [
                symbol
                for symbol in raw_top
                if symbol not in self._active and symbol not in ineligible
            ]
            incumbents = sorted(
                self._active,
                key=lambda symbol: (score.get(symbol, Decimal("-Infinity")), symbol.encode()),
            )
            for challenger in challengers:
                if not incumbents:
                    break
                incumbent = incumbents[0]
                resident_since = self._entered_at[incumbent]
                if computed_at - resident_since < self.minimum_residence:
                    continue
                if (
                    self._streaks[challenger] >= self.confirmations
                    and score[challenger]
                    >= score.get(incumbent, Decimal("-Infinity")) + self.replacement_margin
                ):
                    self._active.remove(incumbent)
                    self._active.append(challenger)
                    self._entered_at.pop(incumbent, None)
                    self._entered_at[challenger] = computed_at
                    incumbents.pop(0)
        order = {rank.symbol: index for index, rank in enumerate(ranking)}
        self._active.sort(key=lambda symbol: (order.get(symbol, 10**9), symbol.encode()))
        standby = tuple(
            rank.symbol
            for rank in ranking[self.size : self.size + 5]
            if rank.symbol not in ineligible
        )
        return MembershipView(
            active=tuple(self._active),
            standby=standby,
            managed_positions=tuple(sorted(managed - set(self._active))),
            reduced_pool_alert=len(self._active) < self.size,
        )

    @staticmethod
    def _require_utc(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("computed_at must be timezone-aware UTC")
