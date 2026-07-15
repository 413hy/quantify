from decimal import Decimal

import pytest

import ai_quant.binance_egress.structural_experiment as experiment
from ai_quant.binance_egress.structural_experiment import (
    PositionSignalControl,
    _classify_native_exit,
    _exit_trade_price,
    _place_protection,
    _predictive_limit_entry,
    _prepare_scale_in,
    _protected_position_event,
    estimated_position_outcomes,
    exchange_maximum_initial_leverage,
    plan_market_quantity,
    predictive_limit_price,
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


def test_position_signal_control_discards_stale_mail_and_returns_latest() -> None:
    control = PositionSignalControl()
    old = ExperimentalPlan(
        symbol="SOLUSDT",
        direction=Direction.LONG,
        entry_reference=Decimal("100"),
        stop_anchor=Decimal("99.5"),
        target_reference=Decimal("100.4"),
    )
    latest = ExperimentalPlan(
        symbol="SOLUSDT",
        direction=Direction.SHORT,
        entry_reference=Decimal("100"),
        stop_anchor=Decimal("100.5"),
        target_reference=Decimal("99.6"),
    )
    control.submit(old)
    control.submit(latest)

    assert control.take_latest() is latest
    assert control.take_latest() is None


def test_scale_limit_is_canceled_if_the_parent_position_closes() -> None:
    class Client:
        canceled = False

        def place_order(self, params: dict[str, str]) -> dict[str, str]:
            return {"status": "NEW", "executedQty": "0"}

        def cancel_order(self, symbol: str, client_order_id: str) -> dict[str, str]:
            self.canceled = True
            return {"status": "CANCELED"}

        def query_order(self, symbol: str, client_order_id: str) -> dict[str, str]:
            return {"status": "CANCELED", "executedQty": "0"}

    client = Client()
    _, executed, mode = _predictive_limit_entry(
        client,  # type: ignore[arg-type]
        symbol="SOLUSDT",
        direction=Direction.LONG,
        side="BUY",
        quantity=Decimal("0.1"),
        price=Decimal("99.9"),
        client_order_id="scale-test",
        sleep=lambda _: None,
        position_guard=lambda: False,
    )

    assert client.canceled
    assert executed == 0
    assert mode == "PARENT_POSITION_CLOSED"


def test_same_direction_scale_is_sized_against_whole_position_loss_budget() -> None:
    class Client:
        def book_ticker(self, symbol: str) -> dict[str, str]:
            assert symbol == "SOLUSDT"
            return {"bidPrice": "99.99", "askPrice": "100.01"}

    plan = ExperimentalPlan(
        symbol="SOLUSDT",
        direction=Direction.LONG,
        entry_reference=Decimal("100"),
        stop_anchor=Decimal("99.5"),
        target_reference=Decimal("100.4"),
        predictive_average_20m=Decimal("100.09"),
    )

    prepared = _prepare_scale_in(
        Client(),  # type: ignore[arg-type]
        plan=plan,
        exchange_info=_exchange_info(),
        leverage=75,
        margin_ceiling=Decimal("1"),
        maximum_net_loss=Decimal("1"),
        minimum_estimated_net_target=Decimal("0.10"),
        risk_sizing_slippage_rate=Decimal("0.0012"),
        taker_fee_rate=Decimal("0.0004"),
        current_quantity=Decimal("0.5"),
        current_entry=Decimal("100"),
        current_stop=Decimal("99.5"),
    )

    combined_quantity = Decimal("0.5") + prepared.quantity
    _, _, _, stop_loss = estimated_position_outcomes(
        quantity=combined_quantity,
        actual_entry=prepared.combined_entry,
        stop_trigger=prepared.stop_trigger,
        target_trigger=prepared.target_trigger,
        taker_fee_rate=Decimal("0.0004"),
        adverse_slippage_rate=Decimal("0.0012"),
    )
    assert prepared.quantity > 0
    assert stop_loss <= Decimal("1")


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
        entry_execution_mode="PREDICTIVE_GTX_FILLED",
        predictive_limit_price=Decimal("0.9995"),
        predicted_pullback_bps=Decimal("5"),
        directional_forecast_bps=Decimal("8"),
        predictive_entry_model="FORECAST_ALIGNED_BEST_QUOTE",
    )

    assert event["initial_leverage"] == 75
    assert event["actual_initial_margin"] == "0.95"
    assert event["position_notional"] == "71.25"
    assert Decimal(str(event["estimated_target_net_pnl"])) == Decimal("0.178125")
    assert event["protection_working_type"] == "CONTRACT_PRICE"
    assert event["entry_execution_mode"] == "PREDICTIVE_GTX_FILLED"
    assert event["predictive_limit_price"] == "0.9995"
    assert event["predicted_pullback_bps"] == "5"
    assert event["directional_forecast_bps"] == "8"
    assert event["predictive_entry_model"] == "FORECAST_ALIGNED_BEST_QUOTE"


def test_predictive_entry_uses_observed_and_forecast_twenty_minute_average() -> None:
    long_price, long_pullback, long_forecast, long_model = predictive_limit_price(
        Direction.LONG,
        bid_price=Decimal("99.99"),
        ask_price=Decimal("100.01"),
        tick_size=Decimal("0.01"),
        predictive_average_20m=Decimal("100.09"),
    )
    short_price, short_pullback, short_forecast, short_model = predictive_limit_price(
        Direction.SHORT,
        bid_price=Decimal("99.99"),
        ask_price=Decimal("100.01"),
        tick_size=Decimal("0.01"),
        predictive_average_20m=Decimal("99.91"),
    )
    assert long_price == Decimal("99.99")
    assert short_price == Decimal("100.01")
    assert long_pullback == 0
    assert short_pullback == 0
    assert long_forecast > 0
    assert short_forecast > 0
    assert long_model == "FORECAST_ALIGNED_BEST_QUOTE"
    assert short_model == "FORECAST_ALIGNED_BEST_QUOTE"


@pytest.mark.parametrize(
    ("direction", "average"),
    [(Direction.LONG, "100.01"), (Direction.SHORT, "100.00")],
)
def test_predictive_average_rejects_an_uninformative_forecast(
    direction: Direction, average: str
) -> None:
    with pytest.raises(ProbeError, match="EXPERIMENT_PREDICTIVE_EDGE_INSUFFICIENT"):
        predictive_limit_price(
            direction,
            bid_price=Decimal("100.00"),
            ask_price=Decimal("100.01"),
            tick_size=Decimal("0.01"),
            predictive_average_20m=Decimal(average),
        )


def test_short_aligned_forecast_joins_the_passive_best_ask() -> None:
    price, distance, forecast, model = predictive_limit_price(
        Direction.SHORT,
        bid_price=Decimal("99.99"),
        ask_price=Decimal("100.01"),
        tick_size=Decimal("0.01"),
        predictive_average_20m=Decimal("99.91"),
    )

    assert price == Decimal("100.01")
    assert distance == 0
    assert forecast > 0
    assert model == "FORECAST_ALIGNED_BEST_QUOTE"


@pytest.mark.parametrize(
    ("direction", "average"),
    [(Direction.LONG, "99.91"), (Direction.SHORT, "100.09")],
)
def test_forecast_opposed_to_signal_is_rejected(
    direction: Direction, average: str
) -> None:
    with pytest.raises(ProbeError, match="EXPERIMENT_PREDICTIVE_DIRECTION_CONFLICT"):
        predictive_limit_price(
            direction,
            bid_price=Decimal("99.99"),
            ask_price=Decimal("100.01"),
            tick_size=Decimal("0.01"),
            predictive_average_20m=Decimal(average),
        )


def test_pretrade_outcome_estimate_enforces_meaningful_fee_adjusted_target() -> None:
    gross, fees, net, stop_loss = estimated_position_outcomes(
        quantity=Decimal("71.25"),
        actual_entry=Decimal("1"),
        stop_trigger=Decimal("0.997"),
        target_trigger=Decimal("1.0035"),
        taker_fee_rate=Decimal("0.0004"),
    )

    assert gross == Decimal("0.249375")
    assert fees == Decimal("0.057000")
    assert net == Decimal("0.178125")
    assert stop_loss == Decimal("0.285000")


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


def test_margin_reserves_slippage_buffer_inside_loss_budget() -> None:
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

    assert margin == Decimal("0.9333333333333333333333333333")
    assert margin * Decimal(75) * Decimal("0.005") <= Decimal("0.35")


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
