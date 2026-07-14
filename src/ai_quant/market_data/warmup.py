"""Continuous market-data warm-up gate."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


class MarketWarmupGate:
    """Requires continuous health plus trades and both required bar intervals."""

    def __init__(
        self,
        *,
        continuous_for: timedelta = timedelta(seconds=120),
        minimum_trades: int = 1_000,
        minimum_1m_bars: int = 2,
        minimum_5m_bars: int = 1,
    ) -> None:
        self.continuous_for = continuous_for
        self.minimum_trades = minimum_trades
        self.minimum_1m_bars = minimum_1m_bars
        self.minimum_5m_bars = minimum_5m_bars
        self._healthy_since: datetime | None = None
        self._valid_trades = 0
        self._bars = {"1m": 0, "5m": 0}

    def observe_health(self, *, healthy: bool, observed_at: datetime) -> None:
        self._require_utc(observed_at)
        if not healthy:
            self.reset()
        elif self._healthy_since is None:
            self._healthy_since = observed_at

    def record_valid_trades(self, count: int = 1) -> None:
        if count < 0:
            raise ValueError("trade count cannot be negative")
        self._valid_trades += count

    def record_closed_bar(self, interval: str) -> None:
        if interval not in self._bars:
            raise ValueError("only 1m and 5m closed bars satisfy the warm-up contract")
        self._bars[interval] += 1

    def ready(self, *, now: datetime, clock_safe: bool) -> bool:
        self._require_utc(now)
        return bool(
            clock_safe
            and self._healthy_since is not None
            and now - self._healthy_since >= self.continuous_for
            and self._valid_trades >= self.minimum_trades
            and self._bars["1m"] >= self.minimum_1m_bars
            and self._bars["5m"] >= self.minimum_5m_bars
        )

    def reset(self) -> None:
        self._healthy_since = None
        self._valid_trades = 0
        self._bars = {"1m": 0, "5m": 0}

    @staticmethod
    def _require_utc(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("timestamp must be timezone-aware UTC")
