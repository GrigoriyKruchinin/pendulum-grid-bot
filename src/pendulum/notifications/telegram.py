"""Telegram notifier (HTML parse mode) with rate limiting and retries."""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

import httpx

from pendulum.config.settings import TelegramSettings
from pendulum.exchanges.rate_limit import AsyncTokenBucket, sleep_backoff
from pendulum.logging import get_logger

log = get_logger(__name__)
API_URL = "https://api.telegram.org"
MAX_MESSAGE_LEN = 4096


class INotifier(Protocol):
    """Outbound notifications. Implementations must never raise into trading code."""

    async def send(self, html: str) -> bool:
        """Deliver an HTML message; returns False if it could not be delivered."""
        ...


class NullNotifier:
    """Used when Telegram is not configured: logs and drops messages."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, html: str) -> bool:
        """Keep the message in memory (handy for tests)."""
        self.sent.append(html)
        log.info("notification_dropped", length=len(html))
        return False


class TelegramNotifier:
    """Sends HTML messages to one chat via the Bot API."""

    def __init__(
        self,
        settings: TelegramSettings,
        *,
        client: httpx.AsyncClient | None = None,
        max_attempts: int = 4,
    ) -> None:
        if settings.bot_token is None or settings.chat_id is None:
            raise ValueError("Telegram is not configured")
        self._token = settings.bot_token
        self._chat_id = settings.chat_id
        self._client = client or httpx.AsyncClient(base_url=API_URL, timeout=httpx.Timeout(10.0))
        # Telegram allows ~1 msg/s per chat; bursts of 3 are fine.
        self._bucket = AsyncTokenBucket(rate=1, capacity=3)
        self._max_attempts = max_attempts

    async def aclose(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def send(self, html: str) -> bool:
        """Send ``html``; retries on network errors, 429 and 5xx. Never raises."""
        text = html if len(html) <= MAX_MESSAGE_LEN else html[: MAX_MESSAGE_LEN - 20] + "\n…(truncated)"
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        path = f"/bot{self._token.get_secret_value()}/sendMessage"
        for attempt in range(self._max_attempts):
            await self._bucket.acquire()
            try:
                resp = await self._client.post(path, json=payload)
            except httpx.TransportError as exc:
                log.warning("telegram_transport_error", error=type(exc).__name__, attempt=attempt + 1)
                await sleep_backoff(attempt)
                continue
            if resp.status_code == 200:
                return True
            if resp.status_code == 429:
                retry_after = float(_json(resp).get("parameters", {}).get("retry_after", 1))
                await asyncio.sleep(min(retry_after, 30))
                continue
            if resp.status_code >= 500:
                await sleep_backoff(attempt)
                continue
            # 4xx other than 429: bad markup or chat id. Log the description, never the URL (token).
            log.error(
                "telegram_rejected", status=resp.status_code, description=_json(resp).get("description")
            )
            return False
        log.error("telegram_gave_up", attempts=self._max_attempts)
        return False


def _json(resp: httpx.Response) -> dict[str, Any]:
    """Response body as a dict; empty for non-JSON bodies (e.g. proxy HTML errors)."""
    try:
        body = resp.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def make_notifier(settings: TelegramSettings | None = None) -> INotifier:
    """Telegram notifier if configured, else a null notifier."""
    s = settings or TelegramSettings()
    return TelegramNotifier(s) if s.enabled else NullNotifier()
