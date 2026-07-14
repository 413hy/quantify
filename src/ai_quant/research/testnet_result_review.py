"""Fee-aware summaries for append-only Testnet experiment results."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any


def review_testnet_results(
    documents: Iterable[Mapping[str, Any]], *, strategy: str | None = None
) -> dict[str, Any]:
    results = [
        item
        for item in documents
        if item.get("record_type") == "TESTNET_EXPERIMENT_RESULT"
        and (strategy is None or item.get("strategy") == strategy)
    ]
    strategy_results = [
        item for item in results if item.get("exit_reason") in {"TAKE_PROFIT", "STOP_LOSS"}
    ]
    positive = [_decimal(item, "net_pnl") for item in results if _decimal(item, "net_pnl") > 0]
    negative = [_decimal(item, "net_pnl") for item in results if _decimal(item, "net_pnl") < 0]
    gross = sum((_decimal(item, "realized_pnl") for item in results), Decimal(0))
    fees = sum((_decimal(item, "commission_paid") for item in results), Decimal(0))
    net = sum((_decimal(item, "net_pnl") for item in results), Decimal(0))
    strategy_positive = [
        _decimal(item, "net_pnl") for item in strategy_results if _decimal(item, "net_pnl") > 0
    ]
    strategy_negative = [
        _decimal(item, "net_pnl") for item in strategy_results if _decimal(item, "net_pnl") < 0
    ]
    strategy_net = sum((_decimal(item, "net_pnl") for item in strategy_results), Decimal(0))
    target_net = [
        _decimal(item, "net_pnl") for item in results if item.get("target_achieved") is True
    ]
    strategy_target_count = sum(
        item.get("exit_reason") == "TAKE_PROFIT" for item in strategy_results
    )
    non_target_net = [
        _decimal(item, "net_pnl") for item in results if item.get("target_achieved") is not True
    ]
    by_symbol: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for item in results:
        by_symbol[str(item.get("symbol", "UNKNOWN"))].append(item)
    return {
        "report": "TESTNET_EXPERIMENT_RESULT_REVIEW",
        "strategy_filter": strategy,
        "result_count": len(results),
        "strategy_result_count": len(strategy_results),
        "operator_exit_count": sum(
            item.get("exit_reason") == "OPERATOR_SERVICE_STOP" for item in results
        ),
        "positive_net_count": len(positive),
        "positive_net_rate": _ratio(len(positive), len(results)),
        "target_count": len(target_net),
        "target_rate": _ratio(len(target_net), len(results)),
        "strategy_target_count": strategy_target_count,
        "strategy_target_rate": _ratio(strategy_target_count, len(strategy_results)),
        "exit_reasons": dict(
            sorted(Counter(str(item.get("exit_reason")) for item in results).items())
        ),
        "gross_realized_pnl": format(gross, "f"),
        "commission_paid": format(fees, "f"),
        "net_pnl": format(net, "f"),
        "strategy_net_pnl": format(strategy_net, "f"),
        "strategy_positive_net_rate": _ratio(len(strategy_positive), len(strategy_results)),
        "strategy_profit_factor": _profit_factor(strategy_positive, strategy_negative),
        "profit_factor": _profit_factor(positive, negative),
        "average_positive_net": _average(positive),
        "average_negative_net": _average(negative),
        "average_target_net": _average(target_net),
        "average_non_target_net": _average(non_target_net),
        "unclassified_exit_count": sum(
            item.get("exit_reason") == "NATIVE_EXIT_UNCLASSIFIED" for item in results
        ),
        "production_endpoint_requests": sum(
            int(item.get("production_endpoint_requests", 0)) for item in results
        ),
        "by_symbol": {
            symbol: _symbol_summary(items) for symbol, items in sorted(by_symbol.items())
        },
        "research_verdict": (
            "INSUFFICIENT_SAMPLE"
            if len(strategy_results) < 30
            else "NET_POSITIVE"
            if strategy_net > 0
            else "NET_NOT_POSITIVE"
        ),
    }


def _symbol_summary(items: list[Mapping[str, Any]]) -> dict[str, Any]:
    values = [_decimal(item, "net_pnl") for item in items]
    return {
        "result_count": len(items),
        "positive_net_count": sum(value > 0 for value in values),
        "net_pnl": format(sum(values, Decimal(0)), "f"),
    }


def _decimal(document: Mapping[str, Any], field: str) -> Decimal:
    return Decimal(str(document.get(field, "0")))


def _average(values: list[Decimal]) -> str | None:
    if not values:
        return None
    return format(sum(values, Decimal(0)) / Decimal(len(values)), "f")


def _ratio(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "0"
    return format(Decimal(numerator) / Decimal(denominator), "f")


def _profit_factor(positive: list[Decimal], negative: list[Decimal]) -> str | None:
    loss = abs(sum(negative, Decimal(0)))
    if loss == 0:
        return None
    return format(sum(positive, Decimal(0)) / loss, "f")
