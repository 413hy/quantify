from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from ai_quant.notifications.outbound import Notification, OutboundNotifier
from ai_quant.notifications.telegram import TelegramFileConfig, TelegramSender
from tests.market_fixtures import BASE_TIME


def notice(*, key: str = "incident-1", seconds: int = 0) -> Notification:
    return Notification(
        severity="P0",
        event_type="DATABASE_UNWRITABLE",
        summary="api_key=supersecret database cannot commit",
        runbook="runbooks/08_DATA_RECOVERY.md",
        occurred_at=BASE_TIME + timedelta(seconds=seconds),
        deduplication_key=key,
    )


def test_notification_is_outbound_only_redacted_and_deduplicated() -> None:
    delivered: list[str] = []
    notifier = OutboundNotifier(delivered.append)

    assert notifier.notify(notice())
    assert not notifier.notify(notice(seconds=1))
    assert len(delivered) == 1
    assert "supersecret" not in delivered[0]
    assert "[REDACTED]" in delivered[0]
    assert "runbooks/08_DATA_RECOVERY.md" in delivered[0]


def test_notification_rate_limit_drops_excess_without_affecting_trading() -> None:
    delivered: list[str] = []
    notifier = OutboundNotifier(delivered.append, maximum_per_minute=1)
    assert notifier.notify(notice(key="one"))
    assert not notifier.notify(notice(key="two", seconds=1))
    assert len(delivered) == 1


def test_telegram_sender_is_file_configured_and_outbound_only(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    chat_ids_file = tmp_path / "chat_ids"
    token_file.write_text("123456789:" + "a" * 32, encoding="ascii")
    chat_ids_file.write_text("-1001234567890\n-1001234567890\n987654321\n", encoding="ascii")
    calls: list[tuple[str, dict[str, object], float]] = []
    sender = TelegramSender(
        TelegramFileConfig.load(token_file, chat_ids_file),
        post=lambda path, document, timeout: calls.append((path, document, timeout)),
    )

    sender("archive roundtrip passed")

    assert len(calls) == 2
    assert calls[0][0].endswith("/sendMessage")
    assert calls[0][1] == {
        "chat_id": "-1001234567890",
        "text": "archive roundtrip passed",
        "disable_web_page_preview": True,
    }
    assert all("getUpdates" not in path for path, _, _ in calls)
