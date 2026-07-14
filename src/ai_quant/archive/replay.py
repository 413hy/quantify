"""Deterministic reconstruction from a snapshot and archived depth records."""

from __future__ import annotations

from pathlib import Path

import pyarrow.parquet as pq  # type: ignore[import-untyped]

from ai_quant.market_data.models import BookSnapshot, DepthUpdate
from ai_quant.orderbook.book import BoundaryRule, LocalOrderBook


def replay_depth_archive(
    snapshot: BookSnapshot,
    archive_paths: list[Path],
    *,
    boundary_rule: BoundaryRule = BoundaryRule.COVER_NEXT_UPDATE_ID,
) -> LocalOrderBook:
    book = LocalOrderBook(snapshot.symbol)
    book.start_buffering(snapshot.connection_id)
    events: list[DepthUpdate] = []
    for path in archive_paths:
        rows = pq.read_table(path).to_pylist()
        events.extend(DepthUpdate.model_validate(row) for row in rows)
    events.sort(
        key=lambda event: (
            event.received_at,
            event.connection_id,
            event.first_update_id,
            event.final_update_id,
            event.raw_hash,
        )
    )
    for event in events:
        book.ingest(event)
    book.load_snapshot(snapshot, boundary_rule=boundary_rule)
    return book
