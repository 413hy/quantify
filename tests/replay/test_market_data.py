from __future__ import annotations

from pathlib import Path

from ai_quant.archive.parquet import RawArchiveWriter
from ai_quant.archive.replay import replay_depth_archive
from tests.market_fixtures import snapshot, update


def test_same_archive_always_produces_same_book_hash(tmp_path: Path) -> None:
    events = [
        update(101, 101, 100, seconds=1, bids=(("100", "4"),)),
        update(102, 102, 101, seconds=2, asks=(("101", "6"),)),
    ]
    archived = RawArchiveWriter(tmp_path).write_depth(events, object_id="replay01")

    first = replay_depth_archive(snapshot(), [archived.absolute_path])
    second = replay_depth_archive(snapshot(), [archived.absolute_path])

    assert first.valid
    assert first.book_hash() == second.book_hash()
    assert first.top() == (("100", "4"), ("101", "6"))
