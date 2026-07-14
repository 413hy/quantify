"""Outbound-only Telegram delivery using token and target files."""

from __future__ import annotations

import http.client
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_TOKEN = re.compile(r"^[1-9][0-9]{4,15}:[A-Za-z0-9_-]{20,}$")
_CHAT_ID = re.compile(r"^-?[1-9][0-9]{0,19}$")
_TELEGRAM_HOST = "api.telegram.org"


class TelegramDeliveryError(RuntimeError):
    """A deliberately secret-free Telegram delivery failure."""


@dataclass(frozen=True, slots=True)
class TelegramFileConfig:
    token: str
    chat_ids: tuple[str, ...]

    @classmethod
    def load(cls, token_file: Path, chat_ids_file: Path) -> TelegramFileConfig:
        token = token_file.read_text(encoding="ascii").strip()
        if not _TOKEN.fullmatch(token):
            raise ValueError("Telegram bot token file is empty or invalid")
        lines = [line.strip() for line in chat_ids_file.read_text(encoding="ascii").splitlines()]
        chat_ids = tuple(dict.fromkeys(line for line in lines if line))
        if not chat_ids or any(not _CHAT_ID.fullmatch(chat_id) for chat_id in chat_ids):
            raise ValueError("Telegram chat ID file must contain one valid ID per line")
        return cls(token=token, chat_ids=chat_ids)


TelegramPost = Callable[[str, dict[str, object], float], None]


def _https_post(path: str, document: dict[str, object], timeout_seconds: float) -> None:
    body = json.dumps(document, separators=(",", ":")).encode()
    connection = http.client.HTTPSConnection(_TELEGRAM_HOST, timeout=timeout_seconds)
    try:
        connection.request("POST", path, body=body, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        payload = response.read(64 * 1024)
    except (OSError, http.client.HTTPException) as error:
        raise TelegramDeliveryError("Telegram HTTPS delivery failed") from error
    finally:
        connection.close()
    if response.status != 200:
        raise TelegramDeliveryError("Telegram API rejected notification")
    try:
        result = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TelegramDeliveryError("Telegram API returned an invalid response") from error
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise TelegramDeliveryError("Telegram API did not confirm notification")


class TelegramSender:
    """Callable sender with no update polling or command-processing surface."""

    def __init__(
        self,
        config: TelegramFileConfig,
        *,
        post: TelegramPost = _https_post,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not 0 < timeout_seconds <= 30:
            raise ValueError("Telegram timeout must be in (0, 30] seconds")
        self._config = config
        self._post = post
        self._timeout_seconds = timeout_seconds

    def __call__(self, message: str) -> None:
        if not message or len(message) > 4096:
            raise ValueError("Telegram message must contain 1 to 4096 characters")
        path = f"/bot{self._config.token}/sendMessage"
        for chat_id in self._config.chat_ids:
            self._post(
                path,
                {"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
                self._timeout_seconds,
            )
