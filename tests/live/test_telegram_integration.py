"""Telegram integration tests against the REAL Bot API. Skip when Telegram is
not configured. Run with: .venv/bin/pytest -m integration -v
(requires TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID).

The unit suite (tests/comms/test_telegram.py) drives TelegramClient through a
FakeHTTP, proving our request shaping. These tests prove the OTHER half: that the
real Bot API accepts those requests — the token authenticates, the chat_id is
reachable, and our actual TelegramClient.send() round-trips a real message. A
mock can't catch a revoked token, a wrong chat_id, or a Bot-API field rename.

getMe / getChat are non-mutating and run whenever configured. The real send()
round-trip posts a visible message, so it's gated behind IBG_LIVE_TELEGRAM_SEND=1
to avoid spamming the operator's chat on every routine `pytest`."""
import os

import pytest

from governor.config import load_env_file, telegram_from_env

pytestmark = pytest.mark.integration


def _tg_cfg():
    load_env_file()
    return telegram_from_env()


@pytest.fixture(scope="module")
def tg():
    cfg = _tg_cfg()
    if not cfg.enabled:
        pytest.skip("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set)")
    return cfg


def test_telegram_getme_ok(tg):
    import httpx

    url = f"https://api.telegram.org/bot{tg.bot_token}/getMe"
    resp = httpx.get(url, timeout=10)
    resp.raise_for_status()
    body = resp.json()
    assert body.get("ok") is True, f"getMe returned ok=False: {body}"
    result = body.get("result", {})
    assert "username" in result, f"getMe result missing 'username': {result}"


def test_telegram_getchat_resolves_configured_chat(tg):
    """The configured chat_id is real and reachable by this bot (no message sent).
    A wrong/stale chat_id — the silent way the daemon's replies vanish — fails here."""
    import httpx

    url = f"https://api.telegram.org/bot{tg.bot_token}/getChat"
    resp = httpx.get(url, params={"chat_id": tg.chat_id}, timeout=10)
    resp.raise_for_status()
    body = resp.json()
    assert body.get("ok") is True, f"getChat ok=False (bad chat_id?): {body}"
    assert str(body.get("result", {}).get("id")) == str(tg.chat_id)


@pytest.mark.skipif(
    os.getenv("IBG_LIVE_TELEGRAM_SEND") != "1",
    reason="set IBG_LIVE_TELEGRAM_SEND=1 to send a real test message via TelegramClient.send()",
)
async def test_real_send_roundtrip_returns_message_id(tg):
    """Our production TelegramClient.send() posts a real message and gets back a
    real message_id — the exact path the daemon uses to deliver every alert."""
    import httpx

    from governor.comms.telegram import TelegramClient

    async with httpx.AsyncClient(timeout=15) as http:
        client = TelegramClient(tg, http)
        message_id = await client.send(
            "✅ <b>ib-governor</b> self-test — real send() round-trip OK.",
            parse_mode="HTML",
        )
    assert isinstance(message_id, int) and message_id > 0, (
        f"send() should return a real message_id, got {message_id!r}"
    )
