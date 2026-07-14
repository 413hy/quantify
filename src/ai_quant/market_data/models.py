"""Strict, transport-independent market-data records.

Decimal values intentionally remain strings at the contract boundary.  This avoids
binary floating-point loss in raw archives and makes replay byte-for-byte stable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class MarketModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value


def _decimal(value: str, *, positive: bool) -> str:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("invalid decimal string") from exc
    if not parsed.is_finite() or (parsed <= 0 if positive else parsed < 0):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"decimal must be finite and {qualifier}")
    return value


class DataHealthStatus(StrEnum):
    HEALTHY = "HEALTHY"
    WARMING_UP = "WARMING_UP"
    STALE = "STALE"
    GAP_DETECTED = "GAP_DETECTED"
    CLOCK_UNSAFE = "CLOCK_UNSAFE"
    INVALID = "INVALID"


class DepthLevel(MarketModel):
    price: str
    quantity: str

    @field_validator("price")
    @classmethod
    def validate_price(cls, value: str) -> str:
        return _decimal(value, positive=True)

    @field_validator("quantity")
    @classmethod
    def validate_quantity(cls, value: str) -> str:
        return _decimal(value, positive=False)


class BookSnapshot(MarketModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    symbol: str = Field(pattern=r"^[A-Z0-9]{2,24}$")
    connection_id: str
    received_at: datetime
    last_update_id: int = Field(ge=0)
    bids: tuple[DepthLevel, ...]
    asks: tuple[DepthLevel, ...]

    _received_at_utc = field_validator("received_at")(_require_utc)

    @model_validator(mode="after")
    def has_valid_sides(self) -> BookSnapshot:
        if not self.bids or not self.asks:
            raise ValueError("snapshot must contain both book sides")
        return self


class DepthUpdate(MarketModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    environment: Literal["shadow", "paper", "testnet", "production"]
    symbol: str = Field(pattern=r"^[A-Z0-9]{2,24}$")
    connection_id: str
    subscription_id: str
    event_time: datetime
    transaction_time: datetime
    received_at: datetime
    first_update_id: int = Field(alias="U", ge=0)
    final_update_id: int = Field(alias="u", ge=0)
    previous_final_update_id: int = Field(alias="pu", ge=0)
    bids: tuple[DepthLevel, ...]
    asks: tuple[DepthLevel, ...]
    raw_hash: Sha256
    clock_offset_ms: float
    rest_base: str
    route_role: str
    route_base_hash: Sha256
    receive_schema_version: Literal["1.0.0"] = "1.0.0"
    duplicate: bool = False
    out_of_order: bool = False

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)
    _event_time_utc = field_validator("event_time")(_require_utc)
    _transaction_time_utc = field_validator("transaction_time")(_require_utc)
    _received_at_utc = field_validator("received_at")(_require_utc)

    @model_validator(mode="after")
    def sequence_is_ordered(self) -> DepthUpdate:
        if self.final_update_id < self.first_update_id:
            raise ValueError("final update id precedes first update id")
        return self


class AggregateTrade(MarketModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    environment: Literal["shadow", "paper", "testnet", "production"]
    symbol: str = Field(pattern=r"^[A-Z0-9]{2,24}$")
    connection_id: str
    event_time: datetime
    received_at: datetime
    aggregate_trade_id: int = Field(ge=0)
    first_trade_id: int = Field(ge=0)
    last_trade_id: int = Field(ge=0)
    price: str
    quantity: str
    notional_quantity: str
    settlement_time: datetime
    price_scale: int | None = Field(default=None, ge=0)
    buyer_is_maker: bool
    raw_hash: Sha256
    route_role: str
    route_base_hash: Sha256

    _event_time_utc = field_validator("event_time")(_require_utc)
    _received_at_utc = field_validator("received_at")(_require_utc)
    _settlement_time_utc = field_validator("settlement_time")(_require_utc)

    @field_validator("price")
    @classmethod
    def validate_price(cls, value: str) -> str:
        return _decimal(value, positive=True)

    @field_validator("quantity", "notional_quantity")
    @classmethod
    def validate_quantity(cls, value: str) -> str:
        return _decimal(value, positive=False)

    @model_validator(mode="after")
    def trade_ids_are_ordered(self) -> AggregateTrade:
        if self.last_trade_id < self.first_trade_id:
            raise ValueError("last trade id precedes first trade id")
        return self


class MarketDataHealth(MarketModel):
    status: DataHealthStatus
    book_valid: bool
    warmed_up: bool
    last_update_id: int | None = Field(default=None, ge=0)
    event_lag_ms: int = Field(ge=0)
    clock_offset_ms: float
    gap_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    out_of_order_count: int = Field(ge=0)
    stale_for_ms: int = Field(ge=0)
    reason_codes: tuple[str, ...]

    @model_validator(mode="after")
    def healthy_contract_is_closed(self) -> MarketDataHealth:
        if self.status is DataHealthStatus.HEALTHY:
            if (
                not self.book_valid
                or not self.warmed_up
                or self.gap_count
                or self.out_of_order_count
                or self.stale_for_ms
                or self.reason_codes
            ):
                raise ValueError("HEALTHY violates closed health contract")
        elif not self.reason_codes:
            raise ValueError("non-healthy state requires a reason code")
        return self
