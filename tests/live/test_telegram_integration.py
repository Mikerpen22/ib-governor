"""Read-only Telegram integration test. Skip when Telegram is not configured.
Run with: .venv/bin/pytest -m integration -v   (requires TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)."""
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
