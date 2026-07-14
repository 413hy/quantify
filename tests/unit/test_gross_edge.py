from __future__ import annotations

from decimal import Decimal

import pytest

from ai_quant.cost.gross_edge import (
    GrossEdgeCell,
    GrossEdgeKey,
    estimate_gross_edge,
)

KEY = GrossEdgeKey("T1", "TREND_UP", "LIQUID", "BUY", "MAKER")


def cell(
    identity: tuple[str | None, str | None, str | None, str | None, str | None],
    observations: int,
    mean: str,
    hash_character: str,
) -> GrossEdgeCell:
    return GrossEdgeCell(*identity, observations, Decimal(mean), hash_character * 64)


def estimate(cells: tuple[GrossEdgeCell, ...]):
    return estimate_gross_edge(
        KEY,
        cells,
        minimum_cell_observations=20,
        minimum_parent_observations=50,
        shrinkage_strength_observations=10,
    )


def test_exact_mean_is_shrunk_to_first_adequate_parent() -> None:
    exact = cell(("T1", "TREND_UP", "LIQUID", "BUY", "MAKER"), 30, "12", "a")
    closest = cell(("T1", "TREND_UP", "LIQUID", "BUY", None), 60, "4", "b")
    broad = cell(("T1", "TREND_UP", None, None, None), 500, "1", "c")

    decision = estimate((exact, closest, broad))

    assert decision.approved
    assert decision.gross_edge_bps == Decimal("10")
    assert decision.source_level == "EXACT_SHRUNK_TO_DROP_ENTRY_PATH"
    assert decision.evidence_hashes == ("a" * 64, "b" * 64)


def test_adequate_parent_can_cover_sparse_exact_cell() -> None:
    exact = cell(("T1", "TREND_UP", "LIQUID", "BUY", "MAKER"), 5, "99", "a")
    parent = cell(("T1", "TREND_UP", "LIQUID", None, None), 80, "3", "b")

    decision = estimate((exact, parent))

    assert decision.approved
    assert decision.gross_edge_bps == Decimal("3")
    assert decision.source_level == "DROP_SIDE"
    assert decision.reason_codes == ("GROSS_EDGE_PARENT_FALLBACK",)


def test_insufficient_exact_and_parent_observations_reject_entry() -> None:
    exact = cell(("T1", "TREND_UP", "LIQUID", "BUY", "MAKER"), 5, "99", "a")
    parent = cell(("T1", None, None, None, None), 49, "4", "b")

    decision = estimate((exact, parent))

    assert not decision.approved
    assert decision.gross_edge_bps is None
    assert decision.reason_codes == ("NET_EDGE_EVIDENCE_INCOMPLETE",)


def test_signed_negative_edge_is_preserved_and_duplicate_cells_fail_closed() -> None:
    exact = cell(("T1", "TREND_UP", "LIQUID", "BUY", "MAKER"), 30, "-2", "a")
    parent = cell((None, None, None, None, None), 100, "-1", "b")
    decision = estimate((exact, parent))
    assert decision.gross_edge_bps == Decimal("-1.75")

    with pytest.raises(ValueError, match="duplicate"):
        estimate((exact, exact, parent))


def test_parent_cells_can_only_drop_dimensions_in_frozen_order() -> None:
    with pytest.raises(ValueError, match="frozen order"):
        cell(("T1", None, "LIQUID", None, None), 50, "1", "a")
