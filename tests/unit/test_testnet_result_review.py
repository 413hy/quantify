from ai_quant.research.testnet_result_review import review_testnet_results


def test_review_separates_target_rate_from_fee_adjusted_positive_rate() -> None:
    report = review_testnet_results(
        [
            _result("XRPUSDT", "TAKE_PROFIT", "0.10", "0.04", "0.06", True),
            _result("BNBUSDT", "TAKE_PROFIT", "0.01", "0.04", "-0.03", True),
            _result("XRPUSDT", "STOP_LOSS", "-0.20", "0.04", "-0.24", False),
        ],
        strategy="TESTNET_EXPERIMENT_OF_PA_V2",
    )

    assert report["result_count"] == 3
    assert report["target_rate"] == "0.6666666666666666666666666667"
    assert report["positive_net_rate"] == "0.3333333333333333333333333333"
    assert report["net_pnl"] == "-0.21"
    assert report["profit_factor"] == "0.2222222222222222222222222222"
    assert report["by_symbol"]["XRPUSDT"]["net_pnl"] == "-0.18"
    assert report["research_verdict"] == "INSUFFICIENT_SAMPLE"


def _result(
    symbol: str,
    exit_reason: str,
    realized: str,
    commission: str,
    net: str,
    target: bool,
) -> dict[str, object]:
    return {
        "record_type": "TESTNET_EXPERIMENT_RESULT",
        "strategy": "TESTNET_EXPERIMENT_OF_PA_V2",
        "symbol": symbol,
        "exit_reason": exit_reason,
        "realized_pnl": realized,
        "commission_paid": commission,
        "net_pnl": net,
        "target_achieved": target,
        "production_endpoint_requests": 0,
    }
