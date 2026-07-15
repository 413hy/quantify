"""Deduplicated, rate-limited, redacted outbound notifications; no inbound commands."""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_SECRET = re.compile(r"(?i)(api[_-]?key|secret|token|password|signature)\s*[:=]\s*([^\s,;]+)")


@dataclass(frozen=True, slots=True)
class Notification:
    severity: str
    event_type: str
    summary: str
    runbook: str
    occurred_at: datetime
    deduplication_key: str


class OutboundNotifier:
    def __init__(
        self,
        sender: Callable[[str], None],
        *,
        maximum_per_minute: int = 20,
        deduplication_ttl: timedelta = timedelta(minutes=10),
    ) -> None:
        self._sender = sender
        self._maximum_per_minute = maximum_per_minute
        self._deduplication_ttl = deduplication_ttl
        self._sent: dict[str, datetime] = {}
        self._recent: deque[datetime] = deque()

    def notify(self, notification: Notification) -> bool:
        prior = self._sent.get(notification.deduplication_key)
        if prior and notification.occurred_at - prior < self._deduplication_ttl:
            return False
        threshold = notification.occurred_at - timedelta(minutes=1)
        while self._recent and self._recent[0] <= threshold:
            self._recent.popleft()
        if len(self._recent) >= self._maximum_per_minute:
            return False
        message = self.render(notification)
        self._sender(message)
        self._recent.append(notification.occurred_at)
        self._sent[notification.deduplication_key] = notification.occurred_at
        return True

    @staticmethod
    def render(notification: Notification) -> str:
        safe_summary = _SECRET.sub(r"\1=[REDACTED]", notification.summary)
        beijing_time = notification.occurred_at.astimezone(ZoneInfo("Asia/Shanghai"))
        severity_label = {
            "INFO": "🟢 信息",
            "NOTICE": "🔵 提醒",
            "WARNING": "🟠 警告",
            "ERROR": "🔴 错误",
            "P0": "🚨 紧急",
            "P1": "🔴 严重",
            "P2": "🟠 警告",
            "P3": "🔵 提醒",
        }.get(notification.severity, notification.severity)
        return (
            f"🤖 {notification.event_type}\n"
            f"{safe_summary}\n"
            "────────────\n"
            f"{severity_label} | {beijing_time:%m-%d %H:%M:%S}\n"
            f"指引: {notification.runbook}"
        )
