"""Fail-closed Binance-style snapshot plus diff order-book reconstruction."""

from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from ai_quant.market_data.models import (
    BookSnapshot,
    DataHealthStatus,
    DepthLevel,
    DepthUpdate,
    MarketDataHealth,
)


class OrderBookState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    BUFFERING = "BUFFERING"
    SNAPSHOT_LOADING = "SNAPSHOT_LOADING"
    SYNCING = "SYNCING"
    HEALTHY = "HEALTHY"
    STALE = "STALE"
    GAP = "GAP"
    ROTATING = "ROTATING"


class BoundaryRule(StrEnum):
    """Versioned interpretation of the first event after a REST snapshot."""

    COVER_LAST_UPDATE_ID = "COVER_LAST_UPDATE_ID"
    COVER_NEXT_UPDATE_ID = "COVER_NEXT_UPDATE_ID"


@dataclass(frozen=True, slots=True)
class OrderBookStats:
    applied_count: int
    duplicate_count: int
    gap_count: int
    out_of_order_count: int
    invalid_count: int


class LocalOrderBook:
    """One-symbol order book. Any ambiguity invalidates and clears the whole book."""

    def __init__(
        self,
        symbol: str,
        *,
        buffer_limit: int = 50_000,
        stale_after: timedelta = timedelta(seconds=3),
    ) -> None:
        if buffer_limit < 1:
            raise ValueError("buffer_limit must be positive")
        self.symbol = symbol
        self.state = OrderBookState.DISCONNECTED
        self.connection_id: str | None = None
        self._buffer: deque[DepthUpdate] = deque(maxlen=buffer_limit)
        self._buffer_limit = buffer_limit
        self._stale_after = stale_after
        self._bids: dict[Decimal, Decimal] = {}
        self._asks: dict[Decimal, Decimal] = {}
        self._last_update_id: int | None = None
        self._last_received_at: datetime | None = None
        self._seen: set[tuple[str, int, int, int, str]] = set()
        self._seen_order: deque[tuple[str, int, int, int, str]] = deque()
        self._seen_limit = max(buffer_limit * 2, 100_000)
        self._applied_count = 0
        self._duplicate_count = 0
        self._gap_count = 0
        self._out_of_order_count = 0
        self._invalid_count = 0
        self._reason = "DISCONNECTED"

    @property
    def last_update_id(self) -> int | None:
        return self._last_update_id

    @property
    def valid(self) -> bool:
        return self.state is OrderBookState.HEALTHY

    @property
    def stats(self) -> OrderBookStats:
        return OrderBookStats(
            applied_count=self._applied_count,
            duplicate_count=self._duplicate_count,
            gap_count=self._gap_count,
            out_of_order_count=self._out_of_order_count,
            invalid_count=self._invalid_count,
        )

    def start_buffering(self, connection_id: str) -> None:
        self.connection_id = connection_id
        self.state = OrderBookState.BUFFERING
        self._reason = "SNAPSHOT_REQUIRED"
        self._buffer.clear()
        self._clear_book()

    def rotate(self, connection_id: str) -> None:
        self.state = OrderBookState.ROTATING
        self._reason = "CONNECTION_ROTATING"
        self.start_buffering(connection_id)

    def disconnect(self) -> None:
        self.state = OrderBookState.DISCONNECTED
        self.connection_id = None
        self._reason = "DISCONNECTED"
        self._buffer.clear()
        self._clear_book()

    def ingest(self, update: DepthUpdate) -> bool:
        self._validate_identity(update)
        key = self._event_key(update)
        if key in self._seen:
            self._duplicate_count += 1
            return False
        self._remember(key)
        if self.state in {
            OrderBookState.BUFFERING,
            OrderBookState.SNAPSHOT_LOADING,
            OrderBookState.SYNCING,
        }:
            if len(self._buffer) == self._buffer_limit:
                self._invalidate("BUFFER_OVERFLOW", gap=True)
                return False
            self._buffer.append(update)
            return False
        if self.state is not OrderBookState.HEALTHY:
            return False
        return self._apply_contiguous(update)

    def load_snapshot(
        self,
        snapshot: BookSnapshot,
        *,
        boundary_rule: BoundaryRule = BoundaryRule.COVER_NEXT_UPDATE_ID,
    ) -> None:
        if self.state is not OrderBookState.BUFFERING:
            raise RuntimeError("snapshot can only be loaded while buffering")
        if snapshot.symbol != self.symbol or snapshot.connection_id != self.connection_id:
            self._invalidate("SNAPSHOT_IDENTITY_MISMATCH")
            return
        self.state = OrderBookState.SNAPSHOT_LOADING
        bids = self._levels(snapshot.bids)
        asks = self._levels(snapshot.asks)
        if not self._sides_valid(bids, asks):
            self._invalidate("SNAPSHOT_BOOK_INVALID")
            return
        self._bids = bids
        self._asks = asks
        self._last_update_id = snapshot.last_update_id
        self._last_received_at = snapshot.received_at
        self.state = OrderBookState.SYNCING

        buffered = list(self._buffer)
        self._buffer.clear()
        candidates = [
            event for event in buffered if event.final_update_id >= snapshot.last_update_id
        ]
        if not candidates:
            self._invalidate("SNAPSHOT_BOUNDARY_MISSING", gap=True)
            return
        boundary = (
            snapshot.last_update_id
            if boundary_rule is BoundaryRule.COVER_LAST_UPDATE_ID
            else snapshot.last_update_id + 1
        )
        first_index = next(
            (
                index
                for index, event in enumerate(candidates)
                if event.first_update_id <= boundary <= event.final_update_id
            ),
            None,
        )
        if first_index is None:
            self._invalidate("SNAPSHOT_BOUNDARY_GAP", gap=True)
            return
        self._last_update_id = candidates[first_index].previous_final_update_id
        for event in candidates[first_index:]:
            if not self._apply_contiguous(event):
                return
        self.state = OrderBookState.HEALTHY
        self._reason = ""

    def mark_stale(self, now: datetime) -> bool:
        if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
            raise ValueError("now must be timezone-aware UTC")
        if self._last_received_at is None or now - self._last_received_at <= self._stale_after:
            return False
        if self.state is OrderBookState.HEALTHY:
            self.state = OrderBookState.STALE
            self._reason = "EVENT_STREAM_STALE"
            self._clear_book()
            return True
        return False

    def top(self, depth: int = 1) -> tuple[tuple[str, str], ...]:
        if not self.valid:
            raise RuntimeError("order book is not valid")
        bids = sorted(self._bids.items(), reverse=True)[:depth]
        asks = sorted(self._asks.items())[:depth]
        return tuple((self._format(price), self._format(qty)) for price, qty in bids + asks)

    def book_hash(self) -> str:
        if not self.valid:
            raise RuntimeError("order book is not valid")
        document = {
            "asks": [
                [self._format(price), self._format(qty)]
                for price, qty in sorted(self._asks.items())
            ],
            "bids": [
                [self._format(price), self._format(qty)]
                for price, qty in sorted(self._bids.items(), reverse=True)
            ],
            "last_update_id": self._last_update_id,
            "symbol": self.symbol,
        }
        raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()

    def health(
        self,
        *,
        now: datetime,
        warmed_up: bool,
        clock_offset_ms: float,
        clock_safe: bool = True,
    ) -> MarketDataHealth:
        if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
            raise ValueError("now must be timezone-aware UTC")
        lag = (
            max(0, int((now - self._last_received_at).total_seconds() * 1000))
            if self._last_received_at
            else 0
        )
        reason: tuple[str, ...]
        if not clock_safe:
            status = DataHealthStatus.CLOCK_UNSAFE
            reason = ("CLOCK_OFFSET_UNSAFE",)
        elif self.state is OrderBookState.HEALTHY and warmed_up:
            status = DataHealthStatus.HEALTHY
            reason = ()
        elif self.state is OrderBookState.HEALTHY:
            status = DataHealthStatus.WARMING_UP
            reason = ("WARMUP_INCOMPLETE",)
        elif self.state is OrderBookState.STALE:
            status = DataHealthStatus.STALE
            reason = (self._reason,)
        elif self.state is OrderBookState.GAP:
            status = DataHealthStatus.GAP_DETECTED
            reason = (self._reason,)
        else:
            status = DataHealthStatus.INVALID
            reason = (self._reason,)
        return MarketDataHealth(
            status=status,
            book_valid=self.valid,
            warmed_up=warmed_up and self.valid,
            last_update_id=self._last_update_id,
            event_lag_ms=lag,
            clock_offset_ms=clock_offset_ms,
            gap_count=0 if self.valid else self._gap_count,
            duplicate_count=self._duplicate_count,
            out_of_order_count=0 if self.valid else self._out_of_order_count,
            stale_for_ms=lag if self.state is OrderBookState.STALE else 0,
            reason_codes=reason,
        )

    def _validate_identity(self, update: DepthUpdate) -> None:
        if update.symbol != self.symbol or update.connection_id != self.connection_id:
            self._invalidate("STREAM_IDENTITY_MISMATCH")
            raise ValueError("update does not belong to this book connection")

    @staticmethod
    def _event_key(update: DepthUpdate) -> tuple[str, int, int, int, str]:
        return (
            update.connection_id,
            update.first_update_id,
            update.final_update_id,
            update.previous_final_update_id,
            update.raw_hash,
        )

    def _remember(self, key: tuple[str, int, int, int, str]) -> None:
        if len(self._seen_order) >= self._seen_limit:
            expired = self._seen_order.popleft()
            self._seen.discard(expired)
        self._seen.add(key)
        self._seen_order.append(key)

    def _apply_contiguous(self, update: DepthUpdate) -> bool:
        previous = self._last_update_id
        if previous is None or update.previous_final_update_id != previous:
            if previous is not None and update.final_update_id <= previous:
                self._out_of_order_count += 1
                self._invalidate("OUT_OF_ORDER_UPDATE")
            else:
                self._invalidate("SEQUENCE_GAP", gap=True)
            return False
        bids = dict(self._bids)
        asks = dict(self._asks)
        self._apply_levels(bids, update.bids)
        self._apply_levels(asks, update.asks)
        if not self._sides_valid(bids, asks):
            self._invalidate("CROSSED_OR_EMPTY_BOOK")
            return False
        self._bids = bids
        self._asks = asks
        self._last_update_id = update.final_update_id
        self._last_received_at = update.received_at
        self._applied_count += 1
        if self.state is OrderBookState.SYNCING:
            return True
        self.state = OrderBookState.HEALTHY
        self._reason = ""
        return True

    def _invalidate(self, reason: str, *, gap: bool = False) -> None:
        self.state = OrderBookState.GAP if gap else OrderBookState.DISCONNECTED
        self._reason = reason
        self._invalid_count += 1
        if gap:
            self._gap_count += 1
        self._buffer.clear()
        self._clear_book()

    def _clear_book(self) -> None:
        self._bids.clear()
        self._asks.clear()
        self._last_update_id = None
        self._last_received_at = None

    @staticmethod
    def _levels(levels: tuple[DepthLevel, ...]) -> dict[Decimal, Decimal]:
        result: dict[Decimal, Decimal] = {}
        LocalOrderBook._apply_levels(result, levels)
        return result

    @staticmethod
    def _apply_levels(target: dict[Decimal, Decimal], levels: tuple[DepthLevel, ...]) -> None:
        for level in levels:
            price = Decimal(level.price)
            quantity = Decimal(level.quantity)
            if quantity == 0:
                target.pop(price, None)
            else:
                target[price] = quantity

    @staticmethod
    def _sides_valid(bids: dict[Decimal, Decimal], asks: dict[Decimal, Decimal]) -> bool:
        return bool(bids and asks and max(bids) < min(asks))

    @staticmethod
    def _format(value: Decimal) -> str:
        rendered = format(value, "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        return rendered or "0"
