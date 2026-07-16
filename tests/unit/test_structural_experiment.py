from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

import ai_quant.binance_egress.structural_experiment as experiment
from ai_quant.binance_egress.structural_experiment import (
    PositionSignalControl,
    _classify_native_exit,
    _confirmed_market_entry,
    _exit_trade_price,
    _opposing_signal_action,
    _place_protection,
    _prepare_scale_in,
    _protected_position_event,
    estimated_position_outcomes,
    exchange_maximum_initial_leverage,
    market_entry_reference,
    plan_market_quantity,
    quantize_protection,
    resume_protected_structural_experiment,
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


def test_exit_only_opposing_signal_never_becomes_a_replacement_entry() -> None:
    plan = ExperimentalPlan(
        symbol="SOLUSDT",
        direction=Direction.LONG,
        entry_reference=Decimal("77.35"),
        stop_anchor=Decimal("77.35"),
        target_reference=Decimal("77.35"),
        exit_only=True,
    )

    replacement, record_type, exit_reason = _opposing_signal_action(plan)

    assert replacement is None
    assert record_type == "TESTNET_POSITION_INVALIDATION_REQUESTED"
    assert exit_reason == "SIGNAL_INVALIDATION"


def test_scale_market_entry_is_blocked_if_the_parent_position_closes() -> None:
    class Client:
        called = False

        def place_order(self, params: dict[str, str]) -> dict[str, str]:
            self.called = True
            return {"status": "FILLED", "executedQty": params["quantity"]}

    client = Client()
    _, executed, mode = _confirmed_market_entry(
        client,  # type: ignore[arg-type]
        symbol="SOLUSDT",
        side="BUY",
        quantity=Decimal("0.1"),
        client_order_id="scale-test",
        position_guard=lambda: False,
    )

    assert not client.called
    assert executed == 0
    assert mode == "PARENT_POSITION_CLOSED"


def test_confirmed_signal_uses_only_a_market_order() -> None:
    class Client:
        def __init__(self) -> None:
            self.order_types: list[str] = []

        def place_order(self, params: dict[str, str]) -> dict[str, str]:
            self.order_types.append(params["type"])
            return {
                "status": "FILLED",
                "executedQty": params["quantity"],
                "avgPrice": "100.01",
                "clientOrderId": params["newClientOrderId"],
            }

    client = Client()
    document, executed, mode = _confirmed_market_entry(
        client,  # type: ignore[arg-type]
        symbol="SOLUSDT",
        side="BUY",
        quantity=Decimal("0.1"),
        client_order_id="entry-test",
    )

    assert client.order_types == ["MARKET"]
    assert document is not None
    assert document["clientOrderId"] == "entry-test"
    assert executed == Decimal("0.1")
    assert mode == "CONFIRMED_SIGNAL_MARKET_FILLED"


def test_partial_market_fill_that_fails_post_fill_review_is_flattened_and_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Client:
        def __init__(self) -> None:
            self.position = Decimal(0)
            self.order_types: list[str] = []

        def synchronize_time(self) -> tuple[int, int]:
            return 1_000, 0

        def position_mode(self) -> dict[str, bool]:
            return {"dualSidePosition": False}

        def open_orders(self, symbol: str) -> list[dict[str, object]]:
            return []

        def open_algo_orders(self, symbol: str) -> list[dict[str, object]]:
            return []

        def position_risk(self, symbol: str) -> list[dict[str, str]]:
            if self.position == 0:
                return []
            return [{"positionAmt": format(self.position, "f"), "entryPrice": "100.01"}]

        def leverage_brackets(self, symbol: str) -> list[dict[str, object]]:
            return [{"symbol": symbol, "brackets": [{"initialLeverage": 75}]}]

        def change_initial_leverage(self, symbol: str, leverage: int) -> dict[str, object]:
            return {"symbol": symbol, "leverage": leverage}

        def exchange_info(self) -> dict[str, object]:
            return _exchange_info()

        def book_ticker(self, symbol: str) -> dict[str, str]:
            return {"bidPrice": "99.99", "askPrice": "100.01"}

        def commission_rate(self, symbol: str) -> dict[str, str]:
            return {"takerCommissionRate": "0.0004"}

        def place_order(self, params: dict[str, str]) -> dict[str, str]:
            self.order_types.append(params["type"])
            if params.get("reduceOnly") == "true":
                self.position = Decimal(0)
                return {"status": "FILLED", "executedQty": params["quantity"]}
            self.position = Decimal("0.10")
            return {
                "status": "PARTIALLY_FILLED",
                "executedQty": "0.10",
                "avgPrice": "100.01",
                "clientOrderId": params["newClientOrderId"],
            }

        def query_order(self, symbol: str, client_order_id: str) -> dict[str, str]:
            return {
                "status": "PARTIALLY_FILLED",
                "executedQty": "0.10",
                "avgPrice": "100.01",
                "clientOrderId": client_order_id,
            }

        def account_trades(self, symbol: str, *, start_time_ms: int) -> list[dict[str, object]]:
            return [
                {
                    "id": 1,
                    "time": 1_001,
                    "side": "BUY",
                    "price": "100.01",
                    "realizedPnl": "0",
                    "commission": "0.01",
                },
                {
                    "id": 2,
                    "time": 1_002,
                    "side": "SELL",
                    "price": "99.91",
                    "realizedPnl": "-0.01",
                    "commission": "0.01",
                },
            ]

    client = Client()
    monkeypatch.setattr(experiment, "_credential", lambda *args: "credential")
    monkeypatch.setattr(experiment, "BinanceTestnetClient", lambda *args: client)
    plan = ExperimentalPlan(
        symbol="SOLUSDT",
        direction=Direction.LONG,
        entry_reference=Decimal("100"),
        stop_anchor=Decimal("99.5"),
        target_reference=Decimal("100.4"),
        predictive_average_20m=Decimal("100.09"),
    )

    result = experiment.run_structural_experiment(
        api_key_file=Path("key"),
        api_secret_file=Path("secret"),
        repository_root=Path.cwd(),
        plan=plan,
        sleep=lambda _seconds: None,
    )

    assert client.order_types == ["MARKET", "MARKET"]
    assert result["exit_reason"] == "EXECUTION_FAIL_CLOSED"
    assert result["execution_error_code"] == "EXPERIMENT_ACTUAL_ENTRY_NET_TARGET_INSUFFICIENT"
    assert result["entry_executed_quantity"] == "0.10"
    assert result["account_trade_count"] == 2
    assert result["net_pnl"] == "-0.03"


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


def test_same_direction_scale_rejects_poor_fee_adjusted_reward_risk() -> None:
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

    with pytest.raises(ProbeError, match="EXPERIMENT_NET_REWARD_RISK_INSUFFICIENT"):
        _prepare_scale_in(
            Client(),  # type: ignore[arg-type]
            plan=plan,
            exchange_info=_exchange_info(),
            leverage=75,
            margin_ceiling=Decimal("1"),
            maximum_net_loss=Decimal("1"),
            minimum_estimated_net_target=Decimal("0.10"),
            minimum_net_reward_risk_ratio=Decimal("1"),
            risk_sizing_slippage_rate=Decimal("0.0012"),
            taker_fee_rate=Decimal("0.0004"),
            current_quantity=Decimal("0.5"),
            current_entry=Decimal("100"),
            current_stop=Decimal("99.5"),
        )


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
        target_feasibility_rate_15m=Decimal("0.42"),
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
        entry_execution_mode="CONFIRMED_SIGNAL_MARKET_FILLED",
        entry_reference_price=Decimal("1.0001"),
        directional_forecast_bps=Decimal("8"),
        entry_forecast_model="FORECAST_ALIGNED_MARKET_REFERENCE",
        stop_algo_id=100,
        stop_client_algo_id="aqa-t-exp-sl-test",
        target_algo_id=101,
        target_client_algo_id="aqa-t-exp-tp-test",
    )

    assert event["initial_leverage"] == 75
    assert event["actual_initial_margin"] == "0.95"
    assert event["position_notional"] == "71.25"
    assert Decimal(str(event["estimated_target_net_pnl"])) == Decimal("0.178125")
    assert event["protection_working_type"] == "CONTRACT_PRICE"
    assert event["entry_execution_mode"] == "CONFIRMED_SIGNAL_MARKET_FILLED"
    assert event["entry_reference_price"] == "1.0001"
    assert event["directional_forecast_bps"] == "8"
    assert event["entry_forecast_model"] == "FORECAST_ALIGNED_MARKET_REFERENCE"
    assert event["stop_algo_id"] == 100
    assert event["stop_client_algo_id"] == "aqa-t-exp-sl-test"
    assert event["target_algo_id"] == 101
    assert event["target_client_algo_id"] == "aqa-t-exp-tp-test"
    assert event["target_feasibility_rate_15m"] == "0.42"


def test_restart_recovery_records_native_target_and_cleans_sibling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Client:
        stop_status = "NEW"

        def synchronize_time(self) -> tuple[int, int]:
            return 1_000, 0

        def position_mode(self) -> dict[str, bool]:
            return {"dualSidePosition": False}

        def position_risk(self, symbol: str) -> list[dict[str, str]]:
            return [{"symbol": symbol, "positionAmt": "0"}]

        def query_algo_order(
            self, *, client_algo_id: str | None = None, algo_id: int | None = None
        ) -> dict[str, object]:
            assert client_algo_id is None
            if algo_id == 100:
                return {
                    "algoId": 100,
                    "clientAlgoId": "aqa-t-exp-sl-test",
                    "algoStatus": self.stop_status,
                }
            return {
                "algoId": 101,
                "clientAlgoId": "aqa-t-exp-tp-test",
                "algoStatus": "FINISHED",
            }

        def open_algo_orders(self, symbol: str) -> list[dict[str, object]]:
            if self.stop_status != "NEW":
                return []
            return [
                {
                    "algoId": 100,
                    "clientAlgoId": "aqa-t-exp-sl-test",
                    "algoStatus": "NEW",
                }
            ]

        def cancel_algo_order(
            self, *, client_algo_id: str | None = None, algo_id: int | None = None
        ) -> dict[str, object]:
            assert client_algo_id is None and algo_id == 100
            self.stop_status = "CANCELED"
            return {"algoId": 100, "algoStatus": "CANCELED"}

        def account_trades(self, symbol: str, *, start_time_ms: int) -> list[dict[str, object]]:
            assert start_time_ms > 0
            return [
                {
                    "id": 1,
                    "time": 1_001,
                    "side": "BUY",
                    "price": "100",
                    "realizedPnl": "0",
                    "commission": "0.01",
                },
                {
                    "id": 2,
                    "time": 1_002,
                    "side": "SELL",
                    "price": "101",
                    "realizedPnl": "0.10",
                    "commission": "0.01",
                },
            ]

    client = Client()
    monkeypatch.setattr(experiment, "_credential", lambda *args: "credential")
    monkeypatch.setattr(experiment, "BinanceTestnetClient", lambda *args: client)
    result = resume_protected_structural_experiment(
        api_key_file=Path("key"),
        api_secret_file=Path("secret"),
        repository_root=Path.cwd(),
        recovery_event={
            "symbol": "SOLUSDT",
            "direction": "LONG",
            "quantity": "0.1",
            "entry_price": "100",
            "initial_leverage": 50,
            "strategy": "TEST_STRATEGY",
            "position_started_at": datetime(2026, 7, 15, tzinfo=UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "stop_algo_id": 100,
            "stop_client_algo_id": "aqa-t-exp-sl-test",
            "target_algo_id": 101,
            "target_client_algo_id": "aqa-t-exp-tp-test",
            "stop_trigger": "99",
            "target_trigger": "101",
        },
        sleep=lambda _seconds: None,
    )

    assert result["recovered_after_restart"] is True
    assert result["exit_reason"] == "TAKE_PROFIT"
    assert result["target_achieved"] is True
    assert result["net_pnl"] == "0.08"
    assert result["stop_final_status"] == "CANCELED"


def test_predictive_entry_uses_observed_and_forecast_twenty_minute_average() -> None:
    long_price, long_forecast, long_model = market_entry_reference(
        Direction.LONG,
        bid_price=Decimal("99.99"),
        ask_price=Decimal("100.01"),
        predictive_average_20m=Decimal("100.09"),
    )
    short_price, short_forecast, short_model = market_entry_reference(
        Direction.SHORT,
        bid_price=Decimal("99.99"),
        ask_price=Decimal("100.01"),
        predictive_average_20m=Decimal("99.91"),
    )
    assert long_price == Decimal("100.01")
    assert short_price == Decimal("99.99")
    assert long_forecast > 0
    assert short_forecast > 0
    assert long_model == "FORECAST_ALIGNED_MARKET_REFERENCE"
    assert short_model == "FORECAST_ALIGNED_MARKET_REFERENCE"


@pytest.mark.parametrize(
    ("direction", "average"),
    [(Direction.LONG, "100.01"), (Direction.SHORT, "100.00")],
)
def test_predictive_average_rejects_an_uninformative_forecast(
    direction: Direction, average: str
) -> None:
    with pytest.raises(ProbeError, match="EXPERIMENT_PREDICTIVE_EDGE_INSUFFICIENT"):
        market_entry_reference(
            direction,
            bid_price=Decimal("100.00"),
            ask_price=Decimal("100.01"),
            predictive_average_20m=Decimal(average),
        )


def test_fast_signal_can_use_an_explicit_sub_one_bps_forecast_threshold() -> None:
    price, forecast, model = market_entry_reference(
        Direction.LONG,
        bid_price=Decimal("99.99"),
        ask_price=Decimal("100.01"),
        predictive_average_20m=Decimal("100.002"),
        minimum_forecast_edge_bps=Decimal("0.10"),
    )

    assert price == Decimal("100.01")
    assert forecast == Decimal("0.20000")
    assert model == "FORECAST_ALIGNED_MARKET_REFERENCE"


def test_short_aligned_forecast_uses_the_executable_bid() -> None:
    price, forecast, model = market_entry_reference(
        Direction.SHORT,
        bid_price=Decimal("99.99"),
        ask_price=Decimal("100.01"),
        predictive_average_20m=Decimal("99.91"),
    )

    assert price == Decimal("99.99")
    assert forecast > 0
    assert model == "FORECAST_ALIGNED_MARKET_REFERENCE"


@pytest.mark.parametrize(
    ("direction", "average"),
    [(Direction.LONG, "99.91"), (Direction.SHORT, "100.09")],
)
def test_confirmed_signal_is_rejected_when_linear_forecast_conflicts(
    direction: Direction, average: str
) -> None:
    with pytest.raises(ProbeError, match="EXPERIMENT_PREDICTIVE_DIRECTION_CONFLICT"):
        market_entry_reference(
            direction,
            bid_price=Decimal("99.99"),
            ask_price=Decimal("100.01"),
            predictive_average_20m=Decimal(average),
        )


def test_strong_breadth_can_make_linear_forecast_diagnostic_only() -> None:
    price, forecast, model = market_entry_reference(
        Direction.SHORT,
        bid_price=Decimal("99.99"),
        ask_price=Decimal("100.01"),
        predictive_average_20m=Decimal("100.09"),
        minimum_forecast_edge_bps=Decimal(0),
    )

    assert price == Decimal("99.99")
    assert forecast < 0
    assert model == "STRONG_BREADTH_MARKET_REFERENCE_FORECAST_DIAGNOSTIC_ONLY"


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
