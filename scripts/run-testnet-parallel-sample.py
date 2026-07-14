#!/usr/bin/env python3
"""Run a bounded multi-symbol Testnet execution stress sample."""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from ai_quant.binance_egress.micro_scalp import run_testnet_micro_scalp
from ai_quant.notifications import (
    Notification,
    OutboundNotifier,
    TelegramFileConfig,
    TelegramSender,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key-file", required=True, type=Path)
    parser.add_argument("--api-secret-file", required=True, type=Path)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--telegram-token-file", required=True, type=Path)
    parser.add_argument("--telegram-chat-ids-file", required=True, type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    parser.add_argument("--symbols", default="SOLUSDT,BNBUSDT,XRPUSDT")
    parser.add_argument("--target-net-profit", type=Decimal, default=Decimal("0.05"))
    parser.add_argument("--maximum-net-loss", type=Decimal, default=Decimal("0.10"))
    parser.add_argument("--maximum-holding-seconds", type=int, default=900)
    arguments = parser.parse_args()
    symbols = tuple(symbol.strip() for symbol in arguments.symbols.split(",") if symbol.strip())
    if not 2 <= len(symbols) <= 5 or len(set(symbols)) != len(symbols):
        raise ValueError("parallel sample requires 2 to 5 unique symbols")

    def run_one(symbol: str) -> dict[str, Any]:
        return run_testnet_micro_scalp(
            api_key_file=arguments.api_key_file,
            api_secret_file=arguments.api_secret_file,
            repository_root=arguments.repository_root,
            symbol=symbol,
            target_net_profit=arguments.target_net_profit,
            maximum_net_loss=arguments.maximum_net_loss,
            maximum_holding_seconds=arguments.maximum_holding_seconds,
        )

    started_at = datetime.now(UTC)
    results: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=len(symbols), thread_name_prefix="testnet-sample") as pool:
        futures = {symbol: pool.submit(run_one, symbol) for symbol in symbols}
        for symbol in symbols:
            try:
                results[symbol] = futures[symbol].result()
            except Exception as exc:
                failures[symbol] = type(exc).__name__

    arguments.output_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    for symbol, result in results.items():
        _atomic_write_json(arguments.output_directory / f"{symbol.lower()}.json", result)
    summary = {
        "schema_version": "1.0.0",
        "probe": "BINANCE_TESTNET_PARALLEL_EXECUTION_SAMPLE",
        "sample_classification": "EXECUTION_STRESS_NOT_STRATEGY_SIGNAL",
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "completed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "symbols": list(symbols),
        "successful_count": len(results),
        "failure_count": len(failures),
        "failure_types": failures,
        "net_pnl": {symbol: str(result["net_pnl"]) for symbol, result in results.items()},
        "all_flat": all(
            result["final_open_order_count"] == 0
            and result["final_open_algo_order_count"] == 0
            and result["final_position_quantity"] == "0"
            for result in results.values()
        ),
        "production_endpoint_requests": 0,
    }
    _atomic_write_json(arguments.output_directory / "summary.json", summary)
    notifier = OutboundNotifier(
        TelegramSender(
            TelegramFileConfig.load(arguments.telegram_token_file, arguments.telegram_chat_ids_file)
        )
    )
    for symbol, result in results.items():
        notifier.notify(_trade_notification(symbol, result))
    notifier.notify(
        Notification(
            severity="INFO" if not failures else "WARNING",
            event_type="测试网并行执行样本汇总",
            summary=(
                f"样本分类: 执行压力测试 (非策略信号)\n"
                f"并行标的: {', '.join(symbols)}\n"
                f"成功: {len(results)}\n"
                f"失败: {len(failures)}\n"
                f"最终全部归零: {'是' if summary['all_flat'] else '否'}\n"
                "生产接口请求: 0"
            ),
            runbook="docs/testnet-campaign.md",
            occurred_at=datetime.now(UTC),
            deduplication_key=f"parallel-sample-{started_at.isoformat()}",
        )
    )
    print(
        "TESTNET_PARALLEL_SAMPLE="
        f"{'PASS' if not failures and summary['all_flat'] else 'FAIL_CLOSED'} "
        f"successful={len(results)} failed={len(failures)} flat={summary['all_flat']}"
    )
    return 0 if not failures and summary["all_flat"] else 2


def _trade_notification(symbol: str, result: dict[str, Any]) -> Notification:
    target_achieved = "是" if bool(result["target_achieved"]) else "否"
    exit_reason = {
        "TAKE_PROFIT": "止盈触发",
        "STOP_LOSS": "止损触发",
        "MAX_HOLDING_TIME": "达到最长持仓时间",
        "NATIVE_EXIT_UNCLASSIFIED": "交易所原生保护退出",
    }.get(str(result["exit_reason"]), str(result["exit_reason"]))
    return Notification(
        severity="INFO" if result["target_achieved"] else "WARNING",
        event_type="测试网并行执行样本结果",
        summary=(
            "样本分类: 执行压力测试 (非策略信号)\n"
            f"交易对: {symbol}\n"
            "方向: 做多\n"
            f"数量: {result['quantity']}\n"
            f"保证金: {result['actual_initial_margin']} USDT\n"
            f"入场价: {result['entry_price']}\n"
            f"止盈触发价: {result['target_trigger']}\n"
            f"止损触发价: {result['stop_trigger']}\n"
            f"退出原因: {exit_reason}\n"
            f"达到目标: {target_achieved}\n"
            f"已实现盈亏: {result['realized_pnl']} USDT\n"
            f"手续费: {result['commission_paid']} USDT\n"
            f"净结果: {result['net_pnl']} USDT\n"
            f"剩余订单/条件单/持仓: {result['final_open_order_count']} / "
            f"{result['final_open_algo_order_count']} / {result['final_position_quantity']}"
        ),
        runbook="docs/testnet-campaign.md",
        occurred_at=datetime.now(UTC),
        deduplication_key=f"parallel-sample-trade-{symbol}-{result['completed_at']}",
    )


def _atomic_write_json(path: Path, document: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
