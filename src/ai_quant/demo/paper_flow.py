"""Run market data through replay, strategy, edge, risk, fill, and protection."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from ai_quant.archive.parquet import RawArchiveWriter
from ai_quant.archive.replay import replay_depth_archive
from ai_quant.cost.edge import CostBreakdown, CostComponent, evaluate_edge
from ai_quant.execution.orders import (
    OrderEvent,
    OrderState,
    OrderTransport,
    project_order,
)
from ai_quant.execution.protection import evaluate_protection
from ai_quant.execution.simulator import SimulatedOrderType, simulate_fill
from ai_quant.features.order_flow import BookLevel, calculate_order_flow
from ai_quant.features.price_action import Direction
from ai_quant.market_data.models import (
    AggregateTrade,
    BookSnapshot,
    DepthLevel,
    DepthUpdate,
)
from ai_quant.risk.sizing import RiskSizingInput, size_entry
from ai_quant.strategy.core import StrategyCore, StrategyFrame
from ai_quant.strategy.fusion import (
    OrderFlowConfirmation,
    OrderFlowTrigger,
    PriceActionArm,
    Setup,
)
from ai_quant.universe.ranking import UniverseInput, rank_universe

DEMO_TIME = datetime(2026, 7, 14, 10, tzinfo=UTC)


def run_paper_flow(root: Path) -> dict[str, object]:
    snapshot = BookSnapshot(
        symbol="BTCUSDT",
        connection_id="demo-connection",
        received_at=DEMO_TIME,
        last_update_id=100,
        bids=(DepthLevel(price="100", quantity="3"),),
        asks=(DepthLevel(price="101", quantity="3"),),
    )
    depth = _depth_update()
    archived = RawArchiveWriter(root).write_depth([depth], object_id="paperflow01")
    book = replay_depth_archive(snapshot, [archived.absolute_path])
    if not book.valid:
        raise RuntimeError("paper demo could not reconstruct a valid order book")

    universe = rank_universe(
        [
            UniverseInput(
                symbol=f"S{index:02d}USDT" if index else "BTCUSDT",
                quote_notional_15m=Decimal(100 - index) * 1_000,
                twap_bid_depth_10bps=Decimal(100 - index) * 100,
                twap_ask_depth_10bps=Decimal(100 - index) * 100,
                median_spread_bps=Decimal(index + 1),
                trade_count_15m=1_000 - index,
                input_completeness_pct=Decimal(100),
            )
            for index in range(10)
        ]
    )
    if universe.ranking[0].symbol != "BTCUSDT":
        raise RuntimeError("paper demo universe selection changed")

    trade = _trade()
    flow = calculate_order_flow(
        (BookLevel(Decimal(100), Decimal(4)),),
        (BookLevel(Decimal(101), Decimal(3)),),
        (trade,),
        depth_levels=1,
    )
    arm = PriceActionArm(
        symbol="BTCUSDT",
        setup=Setup.T1_TREND_PULLBACK_CONTINUATION,
        direction=Direction.LONG,
        armed_at=DEMO_TIME,
        expires_at=DEMO_TIME + timedelta(seconds=2),
        entry_reference=Decimal(101),
        stop_anchor=Decimal(99),
        target_reference=Decimal(105),
        structure_version="demo-structure-v1",
    )
    confirmation = OrderFlowConfirmation(
        direction=Direction.LONG,
        trigger=OrderFlowTrigger.OF1_MOMENTUM_SWEEP_CONTINUATION,
        confirmed_at=DEMO_TIME + timedelta(milliseconds=500),
        valid=flow.valid and flow.trade_imbalance > 0,
    )
    strategy = StrategyCore().evaluate(
        StrategyFrame(confirmation.confirmed_at, (arm,), confirmation, book.valid)
    )
    if strategy.candidate is None:
        raise RuntimeError("paper demo strategy did not produce a candidate")

    cost_component = CostComponent(Decimal(1), DEMO_TIME, "a" * 64)
    costs = CostBreakdown(*(cost_component for _ in range(7)))
    edge = evaluate_edge(
        Decimal(20),
        costs,
        now=DEMO_TIME + timedelta(seconds=1),
        maximum_component_age_seconds=5,
        minimum_net_edge_bps=Decimal(5),
    )
    if not edge.approved:
        raise RuntimeError("paper demo edge was rejected")

    sizing = size_entry(
        RiskSizingInput(
            equity=Decimal(10_000),
            entry_assumption=Decimal(101),
            stop_trigger=Decimal(99),
            entry_slippage_per_unit=Decimal("0.1"),
            emergency_exit_slippage_per_unit=Decimal("0.2"),
            entry_fee_per_unit=Decimal("0.1"),
            exit_fee_per_unit=Decimal("0.1"),
            funding_buffer_per_unit=Decimal("0.1"),
            reserved_episode_risk=Decimal(0),
            reserved_all_risk=Decimal(0),
            reserved_cluster_risk=Decimal(0),
            current_daily_loss=Decimal(0),
            current_drawdown=Decimal(0),
            current_gross_notional=Decimal(0),
            current_positions=0,
            step_size=Decimal("0.1"),
            minimum_quantity=Decimal("0.1"),
            minimum_notional=Decimal(5),
            maximum_executable_quantity=Decimal(10),
        ),
        risk_multiplier=Decimal("0.10"),
    )
    if not sizing.approved:
        raise RuntimeError("paper demo risk sizing was rejected")

    fill = simulate_fill(
        side="BUY",
        quantity=sizing.quantity,
        order_type=SimulatedOrderType.TAKER,
        limit_price=None,
        visible_levels=((Decimal(101), Decimal(10)),),
    )
    events = (
        _event(0, OrderState.CREATED),
        _event(1, OrderState.RISK_APPROVED),
        _event(2, OrderState.SUBMITTING),
        _event(3, OrderState.ACKNOWLEDGED, order_id="paper-order-1"),
        _event(
            4,
            OrderState.FILLED,
            filled=fill.filled_quantity,
            order_id="paper-order-1",
        ),
    )
    order = project_order("paper-intent-1", OrderTransport.STANDARD, "aq-s-paperflow01", events)
    protection = evaluate_protection(
        position_quantity=order.cumulative_filled_quantity,
        protected_quantity=order.cumulative_filled_quantity,
        first_fill_at=events[-1].occurred_at,
        now=events[-1].occurred_at + timedelta(milliseconds=200),
        exchange_confirmed=True,
        direction_correct=True,
        reduce_only=True,
    )
    return {
        "mode": "OFFLINE_PAPER",
        "external_requests": 0,
        "archive_sha256": archived.sha256,
        "book_hash": book.book_hash(),
        "universe_leader": universe.ranking[0].symbol,
        "signal_id": strategy.candidate.candidate_id,
        "net_edge_bps": str(edge.net_edge_bps),
        "approved_quantity": str(sizing.quantity),
        "order_state": order.state,
        "filled_quantity": str(order.cumulative_filled_quantity),
        "protection_healthy": protection.healthy,
        "runtime_state": "RISK_LOCKED",
    }


def _depth_update() -> DepthUpdate:
    return DepthUpdate(
        environment="paper",
        symbol="BTCUSDT",
        connection_id="demo-connection",
        subscription_id="demo-subscription",
        event_time=DEMO_TIME + timedelta(seconds=1),
        transaction_time=DEMO_TIME + timedelta(seconds=1),
        received_at=DEMO_TIME + timedelta(seconds=1, milliseconds=10),
        U=101,
        u=101,
        pu=100,
        bids=(DepthLevel(price="100", quantity="4"),),
        asks=(),
        raw_hash=hashlib.sha256(b"paper-depth-101").hexdigest(),
        clock_offset_ms=0,
        rest_base="offline://snapshot",
        route_role="OFFLINE_REPLAY",
        route_base_hash="b" * 64,
    )


def _trade() -> AggregateTrade:
    return AggregateTrade(
        environment="paper",
        symbol="BTCUSDT",
        connection_id="demo-connection",
        event_time=DEMO_TIME + timedelta(milliseconds=400),
        received_at=DEMO_TIME + timedelta(milliseconds=410),
        aggregate_trade_id=1,
        first_trade_id=1,
        last_trade_id=1,
        price="101",
        quantity="5",
        notional_quantity="5",
        settlement_time=DEMO_TIME + timedelta(milliseconds=400),
        buyer_is_maker=False,
        raw_hash=hashlib.sha256(b"paper-trade-1").hexdigest(),
        route_role="OFFLINE_REPLAY",
        route_base_hash="b" * 64,
    )


def _event(
    index: int,
    state: OrderState,
    *,
    filled: Decimal = Decimal(0),
    order_id: str | None = None,
) -> OrderEvent:
    return OrderEvent(
        event_id=f"paper-event-{index}",
        occurred_at=DEMO_TIME + timedelta(milliseconds=600 + index),
        state=state,
        cumulative_filled_quantity=filled,
        order_id=order_id,
    )
