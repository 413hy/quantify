from decimal import Decimal

import pytest

import ai_quant.binance_egress.structural_experiment as experiment
from ai_quant.binance_egress.structural_experiment import (
    _classify_native_exit,
    _exit_trade_price,
    _place_protection,
    _protected_position_event,
    exchange_maximum_initial_leverage,
    plan_market_quantity,
    quantize_protection,
    risk_adjusted_margin_budget,
)
from ai_quant.binance_egress.testnet_probe import TestnetProbeError as ProbeError
from ai_quant.features.price_action import Direction
from ai_quant.strategy.testnet_baseline import TestnetExperimentalPlan as ExperimentalPlan


def _exchange_info() -> dict[str, object]:
    return {
        "symbols": [
            {
                "symbol": "SOLUSDT",
                "status": "TRADING",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                    {
                        "filterType": "MARKET_LOT_SIZE",
                        "stepSize": "0.01",
                        "minQty": "0.01",
                    },
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                ],
            }
        ]
    }


def test_market_quantity_stays_inside_one_usdt_margin_budget() -> None:
    quantity = plan_market_quantity(
        _exchange_info(),
        symbol="SOLUSDT",
        reference_price=Decimal("150"),
        margin_budget=Decimal("1"),
        leverage=10,
    )

    assert quantity == Decimal("0.06")
    assert quantity * Decimal("150") / Decimal(10) <= Decimal("1")


def test_market_quantity_rejects_exchange_minimum_above_budget() -> None:
    with pytest.raises(
        ProbeError,
        match="EXPERIMENT_EXCHANGE_MINIMUM_EXCEEDS_MARGIN_BUDGET",
    ):
        plan_market_quantity(
            _exchange_info(),
            symbol="SOLUSDT",
            reference_price=Decimal("1000"),
            margin_budget=Decimal("1"),
            leverage=10,
        )


def test_long_protection_keeps_structural_stop_and_small_target() -> None:
    plan = ExperimentalPlan(
        symbol="SOLUSDT",
        direction=Direction.LONG,
        entry_reference=Decimal("100"),
        stop_anchor=Decimal("99.50"),
        target_reference=Decimal("100.25"),
    )

    stop, target = quantize_protection(
        plan,
        actual_entry=Decimal("100.03"),
        tick_size=Decimal("0.01"),
    )

    assert stop == Decimal("99.50")
    assert target == Decimal("100.29")
    assert stop < Decimal("100.03") < target


def test_protected_position_event_exposes_leverage_margin_and_fee_adjusted_target() -> None:
    plan = ExperimentalPlan(
        symbol="XRPUSDT",
        direction=Direction.LONG,
        entry_reference=Decimal("1"),
        stop_anchor=Decimal("0.997"),
        target_reference=Decimal("1.0035"),
    )

    event = _protected_position_event(
        plan=plan,
        leverage=75,
        quantity=Decimal("71.25"),
        actual_entry=Decimal("1"),
        stop_trigger=Decimal("0.997"),
        target_trigger=Decimal("1.0035"),
        effective_margin_budget=Decimal("0.95"),
        taker_fee_rate=Decimal("0.0004"),
    )

    assert event["initial_leverage"] == 75
    assert event["actual_initial_margin"] == "0.95"
    assert event["position_notional"] == "71.25"
    assert Decimal(str(event["estimated_target_net_pnl"])) == Decimal("0.178125")
    assert event["protection_working_type"] == "CONTRACT_PRICE"


def test_testnet_protection_uses_contract_price_trigger() -> None:
    class Client:
        def __init__(self) -> None:
            self.params: dict[str, str] = {}

        def place_algo_order(self, params: dict[str, str]) -> dict[str, object]:
            self.params = params
            return {"algoStatus": "NEW"}

    client = Client()
    _place_protection(  # type: ignore[arg-type]
        client,
        symbol="SOLUSDT",
        side="SELL",
        client_algo_id="test-id",
        order_type="TAKE_PROFIT_MARKET",
        trigger_price=Decimal("100.35"),
    )

    assert client.params["workingType"] == "CONTRACT_PRICE"


def test_native_exit_classification_waits_for_final_algo_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statuses = iter(["CANCELED", "REMOVED", "CANCELED", "FINISHED"])
    sleeps: list[float] = []
    monkeypatch.setattr(experiment, "_algo_status", lambda *args: next(statuses))

    reason, stop_status, target_status = _classify_native_exit(
        object(),  # type: ignore[arg-type]
        symbol="XRPUSDT",
        stop_id=1,
        stop_client_id="stop",
        target_id=2,
        target_client_id="target",
        sleep=sleeps.append,
    )

    assert reason == "TAKE_PROFIT"
    assert stop_status == "CANCELED"
    assert target_status == "FINISHED"
    assert sleeps == [0.2]


def test_exit_trade_price_uses_latest_fill() -> None:
    assert _exit_trade_price(
        [
            {"id": 10, "time": 100, "price": "100.1"},
            {"id": 11, "time": 200, "price": "100.5"},
        ]
    ) == Decimal("100.5")


def test_short_protection_rounds_away_from_entry() -> None:
    plan = ExperimentalPlan(
        symbol="SOLUSDT",
        direction=Direction.SHORT,
        entry_reference=Decimal("100"),
        stop_anchor=Decimal("100.501"),
        target_reference=Decimal("99.75"),
    )

    stop, target = quantize_protection(
        plan,
        actual_entry=Decimal("99.98"),
        tick_size=Decimal("0.01"),
    )

    assert stop == Decimal("100.51")
    assert target == Decimal("99.73")
    assert target < Decimal("99.98") < stop


def test_margin_expands_but_stop_loss_budget_caps_effective_size() -> None:
    plan = ExperimentalPlan(
        symbol="SOLUSDT",
        direction=Direction.LONG,
        entry_reference=Decimal("100"),
        stop_anchor=Decimal("99.70"),
        target_reference=Decimal("100.20"),
    )

    margin = risk_adjusted_margin_budget(
        plan,
        margin_ceiling=Decimal("1"),
        leverage=75,
        maximum_net_loss=Decimal("0.35"),
        taker_fee_rate=Decimal("0.0004"),
    )

    assert margin == Decimal("1")
    assert margin * Decimal(75) * Decimal("0.004") == Decimal("0.300")


def test_exchange_maximum_leverage_is_selected_from_symbol_brackets() -> None:
    leverage = exchange_maximum_initial_leverage(
        [
            {
                "symbol": "XRPUSDT",
                "brackets": [
                    {"initialLeverage": 75},
                    {"initialLeverage": 50},
                ],
            }
        ],
        "XRPUSDT",
    )

    assert leverage == 75
