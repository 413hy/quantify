from __future__ import annotations

from datetime import timedelta

from ai_quant.notifications.outbound import Notification, OutboundNotifier
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
