from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
import respx
from pydantic import SecretStr

from pendulum.config.settings import TelegramSettings
from pendulum.notifications import templates
from pendulum.notifications.telegram import API_URL, NullNotifier, TelegramNotifier, make_notifier

TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ"


def _settings() -> TelegramSettings:
    return TelegramSettings(_env_file=None, bot_token=SecretStr(TOKEN), chat_id="42")


@respx.mock
async def test_send_html_and_retry_on_429() -> None:
    route = respx.post(f"{API_URL}/bot{TOKEN}/sendMessage").mock(
        side_effect=[
            httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 0}}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    n = TelegramNotifier(_settings())
    assert await n.send("<b>hi</b>")
    body = route.calls.last.request.content.decode()
    assert '"parse_mode":"HTML"' in body
    assert '"chat_id":"42"' in body
    await n.aclose()


@respx.mock
async def test_rejected_message_does_not_raise_or_leak_token(capsys: pytest.CaptureFixture[str]) -> None:
    respx.post(f"{API_URL}/bot{TOKEN}/sendMessage").mock(
        return_value=httpx.Response(400, json={"ok": False, "description": "can't parse entities"})
    )
    n = TelegramNotifier(_settings())
    assert not await n.send("<b>broken")
    assert TOKEN not in capsys.readouterr().out
    await n.aclose()


async def test_make_notifier_without_config() -> None:
    n = make_notifier(TelegramSettings(_env_file=None))
    assert isinstance(n, NullNotifier)
    assert not await n.send("x")


def test_templates_escape_and_format() -> None:
    html = templates.range_exit("<SOL>/USDT", Decimal(120), Decimal(90), Decimal(110), "rebuild & go")
    assert "&lt;SOL&gt;" in html
    assert "rebuild &amp; go" in html
    assert "above" in html
    row = templates.PairSummary(
        "SOL/USDT",
        Decimal(160),
        Decimal(150),
        Decimal(4),
        12,
        3,
        Decimal(100),
        Decimal(90),
        Decimal(110),
        Decimal("0.05"),
    )
    s = templates.daily_summary(datetime(2026, 10, 19, tzinfo=UTC), [row])
    assert "+10.00 $" in s
    assert "+6.67%" in s
    assert "unrealized +6.00 $" in s
    assert "B&amp;H +5.00%" in s
    assert "Not a forecast" in s
