from pendulum.logging import REDACTED, redact_processor, redact_value


def test_sensitive_keys_are_masked() -> None:
    event = {"event": "x", "api_key": "abc", "nested": {"password": "p", "ok": 1}, "items": [{"token": "t"}]}
    out = redact_processor(None, "info", event)
    assert out["api_key"] == REDACTED
    assert out["nested"] == {"password": REDACTED, "ok": 1}
    assert out["items"] == [{"token": REDACTED}]
    assert out["event"] == "x"


def test_telegram_token_inside_url_is_masked() -> None:
    url = "https://api.telegram.org/bot123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ/sendMessage"
    assert "AAHdq" not in redact_value(url)
    assert REDACTED in redact_value(url)
