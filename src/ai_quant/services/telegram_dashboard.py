"""Authorized read-only Telegram dashboard for Testnet campaign evidence."""

from __future__ import annotations

import argparse
import fcntl
import http.client
import json
import os
import signal
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from ai_quant.notifications import TelegramDeliveryError, TelegramFileConfig
from ai_quant.research.testnet_result_review import review_testnet_results

_TELEGRAM_HOST = "api.telegram.org"
_POLL_TIMEOUT_SECONDS = 30
_BUTTON_PNL = "📊 当前盈亏"
_BUTTON_POSITIONS = "📈 当前持仓"
_BUTTON_STATUS = "🧭 运行状态"
_BUTTON_STATS = "🧪 策略统计"
_BUTTON_REFRESH = "🔄 刷新盈亏"
_BUTTON_HELP = "❔ 帮助"
_COMMANDS = (
    {"command": "start", "description": "打开量化系统仪表盘"},
    {"command": "pnl", "description": "查看当前盈亏"},
    {"command": "positions", "description": "查看当前持仓与保护"},
    {"command": "status", "description": "查看服务运行状态"},
    {"command": "stats", "description": "查看当前策略统计"},
    {"command": "help", "description": "查看使用说明"},
)
_KEYBOARD: dict[str, object] = {
    "keyboard": [
        [{"text": _BUTTON_PNL}, {"text": _BUTTON_POSITIONS}],
        [{"text": _BUTTON_STATUS}, {"text": _BUTTON_STATS}],
        [{"text": _BUTTON_REFRESH}, {"text": _BUTTON_HELP}],
    ],
    "is_persistent": True,
    "resize_keyboard": True,
    "input_field_placeholder": "请选择要查看的量化信息",
}

TelegramCall = Callable[[str, dict[str, object], float], object]


def _telegram_call(path: str, document: dict[str, object], timeout: float) -> object:
    body = json.dumps(document, separators=(",", ":")).encode()
    connection = http.client.HTTPSConnection(_TELEGRAM_HOST, timeout=timeout)
    try:
        connection.request(
            "POST", path, body=body, headers={"Content-Type": "application/json"}
        )
        response = connection.getresponse()
        payload = response.read(1024 * 1024)
    except (OSError, http.client.HTTPException) as exc:
        raise TelegramDeliveryError("Telegram dashboard HTTPS request failed") from exc
    finally:
        connection.close()
    try:
        envelope = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TelegramDeliveryError("Telegram dashboard returned invalid JSON") from exc
    if response.status != 200 or not isinstance(envelope, dict) or envelope.get("ok") is not True:
        raise TelegramDeliveryError("Telegram dashboard API request was rejected")
    return envelope.get("result")


class TelegramDashboardClient:
    def __init__(
        self,
        token: str,
        *,
        call: TelegramCall = _telegram_call,
    ) -> None:
        self._base = f"/bot{token}"
        self._call = call

    def webhook_info(self) -> dict[str, Any]:
        result = self._call(f"{self._base}/getWebhookInfo", {}, 10)
        if not isinstance(result, dict):
            raise TelegramDeliveryError("Telegram webhook information is invalid")
        return cast(dict[str, Any], result)

    def set_commands(self) -> None:
        result = self._call(
            f"{self._base}/setMyCommands", {"commands": list(_COMMANDS)}, 10
        )
        if result is not True:
            raise TelegramDeliveryError("Telegram command registration was not confirmed")

    def get_updates(self, *, offset: int, timeout_seconds: int) -> list[dict[str, Any]]:
        if not -1 <= offset or not 0 <= timeout_seconds <= 50:
            raise ValueError("Telegram update request is invalid")
        result = self._call(
            f"{self._base}/getUpdates",
            {
                "offset": offset,
                "limit": 100,
                "timeout": timeout_seconds,
                "allowed_updates": ["message"],
            },
            float(timeout_seconds + 10),
        )
        if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
            raise TelegramDeliveryError("Telegram update response is invalid")
        return cast(list[dict[str, Any]], result)

    def send_message(self, chat_id: str, text: str) -> None:
        if not text or len(text) > 4096:
            raise ValueError("Telegram dashboard message length is invalid")
        result = self._call(
            f"{self._base}/sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
                "reply_markup": _KEYBOARD,
            },
            10,
        )
        if not isinstance(result, dict):
            raise TelegramDeliveryError("Telegram message delivery was not confirmed")


class TelegramDashboard:
    def __init__(
        self,
        *,
        client: TelegramDashboardClient,
        allowed_chat_ids: tuple[str, ...],
        campaign_state_file: Path,
        observations_file: Path,
        user_stream_state_file: Path,
        service_state_file: Path,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.allowed_chat_ids = frozenset(allowed_chat_ids)
        self.campaign_state_file = campaign_state_file
        self.observations_file = observations_file
        self.user_stream_state_file = user_stream_state_file
        self.service_state_file = service_state_file
        self.sleep = sleep
        self.stop_requested = False

    def request_stop(self) -> None:
        self.stop_requested = True

    def run(self) -> int:
        webhook = self.client.webhook_info()
        if webhook.get("url") not in {None, ""}:
            raise RuntimeError("TELEGRAM_WEBHOOK_ALREADY_CONFIGURED")
        self.client.set_commands()
        state = self._load_or_create_state()
        if state["next_update_offset"] is None:
            latest = self.client.get_updates(offset=-1, timeout_seconds=0)
            state["next_update_offset"] = _next_offset(latest)
        announced = set(cast(list[str], state["announced_chat_ids"]))
        for chat_id in sorted(self.allowed_chat_ids):
            if chat_id in announced:
                continue
            self.client.send_message(chat_id, _welcome_text())
            announced.add(chat_id)
        state["announced_chat_ids"] = sorted(announced)
        self._save_state(state)

        consecutive_errors = 0
        while not self.stop_requested:
            try:
                offset = int(state["next_update_offset"])
                updates = self.client.get_updates(
                    offset=offset, timeout_seconds=_POLL_TIMEOUT_SECONDS
                )
                for update in sorted(updates, key=lambda item: int(item.get("update_id", -1))):
                    self._process_update(update, state)
                    state["next_update_offset"] = int(update["update_id"]) + 1
                    self._save_state(state)
                consecutive_errors = 0
                state["last_error"] = None
                state["status"] = "RUNNING"
                self._save_state(state)
            except (KeyError, TypeError, ValueError, TelegramDeliveryError) as exc:
                consecutive_errors += 1
                state["last_error"] = type(exc).__name__
                state["status"] = "DEGRADED"
                self._save_state(state)
                self.sleep(min(60, 2**min(consecutive_errors, 5)))
        state["status"] = "STOPPED"
        self._save_state(state)
        return 0

    def _process_update(self, update: Mapping[str, Any], state: dict[str, Any]) -> None:
        update_id = update.get("update_id")
        if not isinstance(update_id, int):
            raise ValueError("Telegram update ID is invalid")
        message = update.get("message")
        if not isinstance(message, dict):
            state["ignored_update_count"] = int(state["ignored_update_count"]) + 1
            return
        chat = message.get("chat")
        if not isinstance(chat, dict) or not isinstance(chat.get("id"), int):
            state["ignored_update_count"] = int(state["ignored_update_count"]) + 1
            return
        chat_id = str(chat["id"])
        if chat_id not in self.allowed_chat_ids:
            state["unauthorized_update_count"] = int(
                state["unauthorized_update_count"]
            ) + 1
            return
        text = message.get("text")
        if not isinstance(text, str):
            response = "仅支持文字命令, 请使用下方按钮。"
        else:
            response = self._response_for(text)
        self.client.send_message(chat_id, response)
        state["authorized_request_count"] = int(state["authorized_request_count"]) + 1
        state["last_authorized_request_at"] = _now_text()

    def _response_for(self, text: str) -> str:
        command = text.strip()
        if command.startswith("/"):
            command = command.split(maxsplit=1)[0].split("@", maxsplit=1)[0].lower()
        if command in {"/start", "/help", _BUTTON_HELP}:
            return _welcome_text()
        campaign = _load_object(self.campaign_state_file)
        if command in {"/pnl", _BUTTON_PNL, _BUTTON_REFRESH}:
            events = _load_relevant_events(self.observations_file)
            return render_pnl(campaign, events)
        if command in {"/positions", _BUTTON_POSITIONS}:
            events = _load_relevant_events(self.observations_file)
            return render_positions(campaign, events)
        if command in {"/status", _BUTTON_STATUS}:
            user_stream = _load_object(self.user_stream_state_file)
            return render_status(campaign, user_stream)
        if command in {"/stats", _BUTTON_STATS}:
            events = _load_relevant_events(self.observations_file)
            return render_strategy_stats(campaign, events)
        return "没有识别这个操作。请使用下方按钮, 或发送 /help 查看帮助。"

    def _load_or_create_state(self) -> dict[str, Any]:
        if self.service_state_file.exists():
            state = _load_object(self.service_state_file)
            required = {
                "schema_version",
                "status",
                "next_update_offset",
                "announced_chat_ids",
                "authorized_request_count",
                "unauthorized_update_count",
                "ignored_update_count",
                "last_authorized_request_at",
                "last_error",
                "started_at",
                "updated_at",
            }
            if set(state) != required:
                raise ValueError("Telegram dashboard state is invalid")
            state["status"] = "STARTING"
            return state
        now = _now_text()
        return {
            "schema_version": "1.0.0",
            "status": "STARTING",
            "next_update_offset": None,
            "announced_chat_ids": [],
            "authorized_request_count": 0,
            "unauthorized_update_count": 0,
            "ignored_update_count": 0,
            "last_authorized_request_at": None,
            "last_error": None,
            "started_at": now,
            "updated_at": now,
        }

    def _save_state(self, state: dict[str, Any]) -> None:
        state["updated_at"] = _now_text()
        _atomic_write_json(self.service_state_file, state)


def render_pnl(campaign: Mapping[str, Any], events: list[dict[str, Any]]) -> str:
    strategy = str(campaign.get("strategy", "UNKNOWN"))
    current = review_testnet_results(events, strategy=strategy)
    all_results = review_testnet_results(events)
    return (
        "📊 当前盈亏概览\n"
        "━━━━━━━━━━━━━━━━\n"
        f"策略: {strategy}\n"
        f"当前 UTC 交易日: {_money(campaign.get('daily_net_pnl'))} USDT\n"
        f"本轮累计: {_money(campaign.get('cumulative_net_pnl'))} USDT\n"
        f"本轮已平仓: {int(campaign.get('trade_count', 0))} 单\n"
        f"本轮活动仓位: {len(_string_list(campaign.get('active_symbols')))} 个\n"
        "\n🧪 当前策略样本\n"
        f"费用后盈利: {current['positive_net_count']}/{current['result_count']} 单\n"
        f"费用后胜率: {_percent(current['positive_net_rate'])}\n"
        f"净结果: {_money(current['net_pnl'])} USDT\n"
        f"手续费: {_money(current['commission_paid'])} USDT\n"
        f"Profit Factor: {_optional_number(current['profit_factor'])}\n"
        "\n📚 全部实验历史\n"
        f"已平仓: {all_results['result_count']} 单\n"
        f"累计净结果: {_money(all_results['net_pnl'])} USDT\n"
        f"更新时间: {_beijing_time(campaign.get('updated_at'))}"
    )


def render_positions(campaign: Mapping[str, Any], events: list[dict[str, Any]]) -> str:
    active = _string_list(campaign.get("active_symbols"))
    if not active:
        return "📈 当前持仓\n━━━━━━━━━━━━━━━━\n当前没有活动仓位。"
    latest: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("record_type") != "TESTNET_POSITION_PROTECTED":
            continue
        symbol = str(event.get("symbol", ""))
        latest[symbol] = event
    sections = [f"📈 当前持仓 ({len(active)})", "━━━━━━━━━━━━━━━━"]
    for symbol in active:
        protected = latest.get(symbol)
        if protected is None:
            sections.append(f"{symbol}\n仓位正在建立或等待保护确认。")
            continue
        sections.append(
            "\n".join(
                (
                    f"{symbol} · {_direction_cn(protected.get('direction'))}",
                    f"杠杆: {protected.get('initial_leverage')}x",
                    f"初始保证金: {_money(protected.get('actual_initial_margin'))} USDT",
                    f"名义价值: {_money(protected.get('position_notional'))} USDT",
                    f"入场: {protected.get('entry_price')}",
                    f"止盈: {protected.get('target_trigger')}",
                    f"预计止盈净额: {_money(protected.get('estimated_target_net_pnl'))} USDT",
                    f"止损: {protected.get('stop_trigger')}",
                    f"预计止损净额: -{_money(protected.get('estimated_stop_net_loss'))} USDT",
                    "保护: 原生止盈/止损已确认",
                )
            )
        )
    return "\n\n".join(sections)


def render_status(
    campaign: Mapping[str, Any], user_stream: Mapping[str, Any], *, now: datetime | None = None
) -> str:
    current = now or datetime.now(UTC)
    campaign_fresh = _fresh(campaign.get("updated_at"), current, maximum_age_seconds=90)
    stream_fresh = _fresh(user_stream.get("updated_at"), current, maximum_age_seconds=120)
    return (
        "🧭 系统运行状态\n"
        "━━━━━━━━━━━━━━━━\n"
        f"交易活动: {_health(campaign.get('status'), campaign_fresh)}\n"
        f"用户数据流: {_health(user_stream.get('status'), stream_fresh)}\n"
        f"策略: {campaign.get('strategy', 'UNKNOWN')}\n"
        f"决策来源: {campaign.get('decision_authority', 'UNKNOWN')}\n"
        f"依赖 Codex: {'是' if campaign.get('codex_dependency') else '否'}\n"
        f"活动仓位: {len(_string_list(campaign.get('active_symbols')))} 个\n"
        f"已提交/已平仓: {int(campaign.get('submitted_trade_count', 0))}/"
        f"{int(campaign.get('trade_count', 0))}\n"
        f"生产接口请求: {int(campaign.get('production_endpoint_requests', 0))}\n"
        f"最近状态时间: {_beijing_time(campaign.get('updated_at'))}"
    )


def render_strategy_stats(campaign: Mapping[str, Any], events: list[dict[str, Any]]) -> str:
    strategy = str(campaign.get("strategy", "UNKNOWN"))
    report = review_testnet_results(events, strategy=strategy)
    by_symbol = cast(dict[str, dict[str, Any]], report["by_symbol"])
    symbol_lines = [
        f"• {symbol}: {values['result_count']} 单 / 净 {_money(values['net_pnl'])} U"
        for symbol, values in by_symbol.items()
    ]
    if not symbol_lines:
        symbol_lines = ["• 当前策略尚无已平仓样本"]
    return (
        "🧪 策略统计\n"
        "━━━━━━━━━━━━━━━━\n"
        f"策略: {strategy}\n"
        f"样本: {report['result_count']} 单\n"
        f"费用后胜率: {_percent(report['positive_net_rate'])}\n"
        f"目标命中率: {_percent(report['target_rate'])}\n"
        f"平均盈利: {_optional_money(report['average_positive_net'])}\n"
        f"平均亏损: {_optional_money(report['average_negative_net'])}\n"
        f"Profit Factor: {_optional_number(report['profit_factor'])}\n"
        f"未分类退出: {report['unclassified_exit_count']} 单\n"
        f"研究结论: {_verdict_cn(report['research_verdict'])}\n"
        "\n逐币结果\n"
        + "\n".join(symbol_lines)
    )


def _welcome_text() -> str:
    return (
        "🤖 AI 量化系统 · Testnet 仪表盘\n"
        "━━━━━━━━━━━━━━━━\n"
        "这里可以只读查看当前盈亏、持仓、服务状态和策略统计。\n\n"
        "点击下方虚拟键盘即可查询。此界面不提供开仓、平仓或参数修改功能。"
    )


def _load_relevant_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        try:
            document = json.loads(line)
        except json.JSONDecodeError:
            if index == len(lines) - 1:
                continue
            raise
        if isinstance(document, dict) and document.get("record_type") in {
            "TESTNET_EXPERIMENT_RESULT",
            "TESTNET_POSITION_PROTECTED",
        }:
            events.append(document)
    return events


def _load_object(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"state document is not an object: {path.name}")
    return cast(dict[str, Any], document)


def _next_offset(updates: list[dict[str, Any]]) -> int:
    identifiers = [item.get("update_id") for item in updates]
    if not identifiers:
        return 0
    if not all(isinstance(value, int) for value in identifiers):
        raise ValueError("Telegram bootstrap update ID is invalid")
    return max(cast(list[int], identifiers)) + 1


def _atomic_write_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
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


def _money(value: object) -> str:
    try:
        number = Decimal(str("0" if value is None else value))
    except InvalidOperation:
        return "0.000000"
    return format(number.quantize(Decimal("0.000001")), "f")


def _optional_money(value: object) -> str:
    return "样本不足" if value is None else f"{_money(value)} USDT"


def _optional_number(value: object) -> str:
    if value is None:
        return "样本不足"
    return format(Decimal(str(value)).quantize(Decimal("0.01")), "f")


def _percent(value: object) -> str:
    return f"{(Decimal(str(value)) * Decimal(100)).quantize(Decimal('0.01'))}%"


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _direction_cn(value: object) -> str:
    return "做多" if str(value) == "LONG" else "做空" if str(value) == "SHORT" else "未知"


def _beijing_time(value: object) -> str:
    if not isinstance(value, str):
        return "未知"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return "未知"
    return parsed.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")


def _fresh(value: object, now: datetime, *, maximum_age_seconds: int) -> bool:
    if not isinstance(value, str):
        return False
    try:
        updated = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return 0 <= (now - updated).total_seconds() <= maximum_age_seconds


def _health(status: object, fresh: bool) -> str:
    return "🟢 正常" if status == "RUNNING" and fresh else f"🟠 {status or '未知'}"


def _verdict_cn(value: object) -> str:
    return {
        "INSUFFICIENT_SAMPLE": "样本不足 (至少需要 30 单)",
        "NET_POSITIVE": "样本净结果为正",
        "NET_NOT_POSITIVE": "样本净结果未转正",
    }.get(str(value), str(value))


def _now_text() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--chat-ids-file", required=True, type=Path)
    parser.add_argument("--campaign-state-file", required=True, type=Path)
    parser.add_argument("--observations-file", required=True, type=Path)
    parser.add_argument("--user-stream-state-file", required=True, type=Path)
    parser.add_argument("--service-state-file", required=True, type=Path)
    parser.add_argument("--lock-file", required=True, type=Path)
    arguments = parser.parse_args()
    config = TelegramFileConfig.load(arguments.token_file, arguments.chat_ids_file)
    dashboard = TelegramDashboard(
        client=TelegramDashboardClient(config.token),
        allowed_chat_ids=config.chat_ids,
        campaign_state_file=arguments.campaign_state_file,
        observations_file=arguments.observations_file,
        user_stream_state_file=arguments.user_stream_state_file,
        service_state_file=arguments.service_state_file,
    )
    arguments.lock_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with arguments.lock_file.open("w", encoding="ascii") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("TELEGRAM_DASHBOARD_ALREADY_RUNNING")
            return 3

        def request_stop(_signal_number: int, _frame: object) -> None:
            dashboard.request_stop()

        signal.signal(signal.SIGINT, request_stop)
        signal.signal(signal.SIGTERM, request_stop)
        return dashboard.run()


if __name__ == "__main__":
    raise SystemExit(main())
