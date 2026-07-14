"""Hourly UTC Parquet/Zstd raw market-data archives with atomic publication."""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from ai_quant.market_data.models import AggregateTrade, DepthUpdate


@dataclass(frozen=True, slots=True)
class ArchivedObject:
    relative_path: str
    absolute_path: Path
    sha256: str
    size_bytes: int
    row_count: int
    schema_version: str
    stream: Literal["depth", "aggTrade"]
    symbol: str
    hour: datetime


_LEVEL = pa.struct([pa.field("price", pa.string()), pa.field("quantity", pa.string())])
_DEPTH_SCHEMA = pa.schema(
    [
        pa.field("schema_version", pa.string()),
        pa.field("environment", pa.string()),
        pa.field("symbol", pa.string()),
        pa.field("connection_id", pa.string()),
        pa.field("subscription_id", pa.string()),
        pa.field("event_time", pa.timestamp("us", tz="UTC")),
        pa.field("transaction_time", pa.timestamp("us", tz="UTC")),
        pa.field("received_at", pa.timestamp("us", tz="UTC")),
        pa.field("U", pa.int64()),
        pa.field("u", pa.int64()),
        pa.field("pu", pa.int64()),
        pa.field("bids", pa.list_(_LEVEL)),
        pa.field("asks", pa.list_(_LEVEL)),
        pa.field("raw_hash", pa.string()),
        pa.field("clock_offset_ms", pa.float64()),
        pa.field("rest_base", pa.string()),
        pa.field("route_role", pa.string()),
        pa.field("route_base_hash", pa.string()),
        pa.field("receive_schema_version", pa.string()),
        pa.field("duplicate", pa.bool_()),
        pa.field("out_of_order", pa.bool_()),
    ]
)
_TRADE_SCHEMA = pa.schema(
    [
        pa.field("schema_version", pa.string()),
        pa.field("environment", pa.string()),
        pa.field("symbol", pa.string()),
        pa.field("connection_id", pa.string()),
        pa.field("event_time", pa.timestamp("us", tz="UTC")),
        pa.field("received_at", pa.timestamp("us", tz="UTC")),
        pa.field("aggregate_trade_id", pa.int64()),
        pa.field("first_trade_id", pa.int64()),
        pa.field("last_trade_id", pa.int64()),
        pa.field("price", pa.string()),
        pa.field("quantity", pa.string()),
        pa.field("notional_quantity", pa.string()),
        pa.field("settlement_time", pa.timestamp("us", tz="UTC")),
        pa.field("price_scale", pa.int32()),
        pa.field("buyer_is_maker", pa.bool_()),
        pa.field("raw_hash", pa.string()),
        pa.field("route_role", pa.string()),
        pa.field("route_base_hash", pa.string()),
    ]
)


class RawArchiveWriter:
    def __init__(self, root: Path) -> None:
        self.root = root

    def write_depth(
        self, events: list[DepthUpdate], *, object_id: str | None = None
    ) -> ArchivedObject:
        rows = [event.model_dump(by_alias=True, mode="python") for event in events]
        return self._write(rows, _DEPTH_SCHEMA, "depth", events, object_id)

    def write_trades(
        self, events: list[AggregateTrade], *, object_id: str | None = None
    ) -> ArchivedObject:
        rows = [event.model_dump(mode="python") for event in events]
        return self._write(rows, _TRADE_SCHEMA, "aggTrade", events, object_id)

    def _write(
        self,
        rows: list[dict[str, Any]],
        schema: pa.Schema,
        stream: Literal["depth", "aggTrade"],
        events: list[DepthUpdate] | list[AggregateTrade],
        object_id: str | None,
    ) -> ArchivedObject:
        if not events:
            raise ValueError("cannot archive an empty batch")
        first = events[0]
        hour = first.received_at.replace(minute=0, second=0, microsecond=0)
        if any(event.symbol != first.symbol for event in events):
            raise ValueError("archive batch must contain one symbol")
        if any(
            event.received_at.replace(minute=0, second=0, microsecond=0) != hour for event in events
        ):
            raise ValueError("archive batch must fit one UTC hour")
        identity = object_id or uuid.uuid4().hex
        if not identity.replace("-", "").isalnum():
            raise ValueError("invalid object id")
        category = "raw_l2" if stream == "depth" else "raw_trades"
        relative = Path(
            category,
            f"date={hour:%Y-%m-%d}",
            f"hour={hour:%H}",
            f"symbol={first.symbol}",
            f"stream={stream}",
            f"part-{identity}.parquet",
        )
        destination = self.root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise FileExistsError("archive objects are append-only")
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        table = pa.Table.from_pylist(rows, schema=schema)
        try:
            pq.write_table(
                table,
                temporary,
                compression="zstd",
                use_dictionary=False,
                write_statistics=True,
            )
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            metadata = pq.read_metadata(temporary)
            if metadata.num_rows != len(events):
                raise OSError("Parquet footer row count mismatch")
            digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
            size = temporary.stat().st_size
            os.replace(temporary, destination)
            directory_fd = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)
        return ArchivedObject(
            relative_path=relative.as_posix(),
            absolute_path=destination,
            sha256=digest,
            size_bytes=size,
            row_count=len(events),
            schema_version=first.schema_version,
            stream=stream,
            symbol=first.symbol,
            hour=hour,
        )
