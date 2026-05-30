from __future__ import annotations

import logging

import requests


logger = logging.getLogger(__name__)


class TelegramClient:
    def __init__(
        self,
        bot_token: str | None,
        chat_id: str | None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout_seconds = timeout_seconds

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send_message(self, text: str) -> bool:
        if not self.is_configured:
            logger.warning("Telegram credentials are not configured; message not sent.")
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
        }
        try:
            response = requests.post(
                url,
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
        except requests.RequestException:
            logger.exception("Telegram sendMessage request failed")
            return False

        if not body.get("ok"):
            logger.error("Telegram sendMessage returned an error: %s", body)
            return False
        return True

