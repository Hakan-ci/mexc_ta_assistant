from __future__ import annotations

from dataclasses import dataclass
import logging
import math
import requests

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliveryResult:
    success: bool
    retryable: bool = False
    retry_after: float | None = None
    error: str | None = None

    def __bool__(self):
        return self.success


class TelegramClient:
    def __init__(self, bot_token: str | None, chat_id: str | None, timeout_seconds: float = 10.0):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout_seconds = timeout_seconds

    @property
    def is_configured(self):
        return bool(self.bot_token and self.chat_id)

    def send_message(self, text: str) -> DeliveryResult:
        if not self.is_configured:
            return DeliveryResult(False, error='Telegram credentials missing')
        try:
            response = requests.post(
                f'https://api.telegram.org/bot{self.bot_token}/sendMessage',
                json={'chat_id': self.chat_id, 'text': text}, timeout=self.timeout_seconds)
        except requests.RequestException:
            # Request exceptions contain the URL (and bot token). Never log their text/traceback.
            logger.warning('Telegram transport failure')
            return DeliveryResult(False, retryable=True, error='Telegram transport failure')
        try:
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError('Invalid response')
        except ValueError:
            code = response.status_code
            return DeliveryResult(False, retryable=code == 429 or code >= 500 or code < 400,
                                  retry_after=30 if code == 429 else None, error='Invalid Telegram response')
        if response.status_code == 200 and body.get('ok') is True:
            return DeliveryResult(True)
        code = response.status_code if response.status_code >= 400 else body.get('error_code', response.status_code)
        if not isinstance(code, int):
            code = response.status_code
        retry_after = None
        if code == 429:
            try:
                retry_after = max(1.0, float(body.get('parameters', {}).get('retry_after', 30)))
                if not math.isfinite(retry_after):
                    retry_after = 30.0
            except (TypeError, ValueError, AttributeError):
                retry_after = 30.0
        retryable = code == 429 or (isinstance(code, int) and code >= 500)
        return DeliveryResult(False, retryable, retry_after, f'Telegram error {code}')
