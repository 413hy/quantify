#!/usr/bin/env python3
"""Send one redacted outbound-only Telegram deployment probe."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from ai_quant.notifications import (
    Notification,
    OutboundNotifier,
    TelegramFileConfig,
    TelegramSender,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--chat-ids-file", type=Path, required=True)
    arguments = parser.parse_args()
    sender = TelegramSender(TelegramFileConfig.load(arguments.token_file, arguments.chat_ids_file))
    notifier = OutboundNotifier(sender)
    sent = notifier.notify(
        Notification(
            severity="INFO",
            event_type="DEPLOYMENT_NOTIFICATION_PROBE",
            summary="AI Quant outbound notification channel verified",
            runbook="runbooks/01_INITIALIZE.md",
            occurred_at=datetime.now(UTC),
            deduplication_key="deployment-notification-probe",
        )
    )
    if not sent:
        raise RuntimeError("deployment notification probe was not sent")
    print("TELEGRAM_OUTBOUND_PROBE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
