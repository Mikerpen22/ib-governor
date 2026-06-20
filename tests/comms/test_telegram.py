# tests/comms/test_telegram.py
import os

from governor.config import TelegramConfig, telegram_from_env


def test_telegram_enabled_only_when_both_present():
    assert TelegramConfig(bot_token="t", chat_id="c").enabled is True
    assert TelegramConfig(bot_token="", chat_id="c").enabled is False
    assert TelegramConfig().enabled is False


def test_telegram_from_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
    cfg = telegram_from_env()
    assert cfg.bot_token == "123:abc" and cfg.chat_id == "999" and cfg.enabled


# append to tests/comms/test_telegram.py
import pytest

from governor.comms.telegram import TelegramClient
from governor.config import TelegramConfig


class FakeResp:
    def __init__(self, payload): self._p = payload
    def raise_for_status(self): pass
    def json(self): return self._p


class FakeHTTP:
    def __init__(self, updates_payload=None):
        self.posts = []
        self._updates = updates_payload or {"ok": True, "result": []}
    async def post(self, url, json):
        self.posts.append((url, json)); return FakeResp({"ok": True})
    async def get(self, url, params):
        return FakeResp(self._updates)


@pytest.mark.asyncio
async def test_send_posts_to_chat():
    http = FakeHTTP()
    c = TelegramClient(TelegramConfig(bot_token="T", chat_id="42"), http)
    await c.send("hello")
    url, body = http.posts[0]
    assert url.endswith("/sendMessage") and body["chat_id"] == "42" and body["text"] == "hello"


@pytest.mark.asyncio
async def test_poll_filters_by_chat_and_advances_offset():
    updates = {"ok": True, "result": [
        {"update_id": 5, "message": {"chat": {"id": 42}, "text": "confirm AAAA"}},
        {"update_id": 6, "message": {"chat": {"id": 99}, "text": "from a stranger"}},  # wrong chat
    ]}
    c = TelegramClient(TelegramConfig(bot_token="T", chat_id="42"), FakeHTTP(updates))
    texts, callbacks, new_offset = await c.poll(offset=0)
    assert texts == ["confirm AAAA"]      # stranger's chat filtered out
    assert callbacks == []
    assert new_offset == 7                # max(update_id)+1


@pytest.mark.asyncio
async def test_poll_surfaces_callback_taps_from_our_chat_only():
    updates = {"ok": True, "result": [
        {"update_id": 8, "callback_query": {"id": "cb1", "data": "confirm:ABCDEF12",
                                            "message": {"chat": {"id": 42}}}},
        {"update_id": 9, "callback_query": {"id": "cb2", "data": "confirm:DEADBEEF",
                                            "message": {"chat": {"id": 99}}}},  # stranger
    ]}
    c = TelegramClient(TelegramConfig(bot_token="T", chat_id="42"), FakeHTTP(updates))
    texts, callbacks, new_offset = await c.poll(offset=0)
    assert texts == []
    assert callbacks == [{"id": "cb1", "data": "confirm:ABCDEF12"}]   # stranger's tap filtered
    assert new_offset == 10


@pytest.mark.asyncio
async def test_send_includes_reply_markup():
    http = FakeHTTP()
    c = TelegramClient(TelegramConfig(bot_token="T", chat_id="42"), http)
    kb = {"inline_keyboard": [[{"text": "✅", "callback_data": "confirm:X"}]]}
    await c.send("pick one", reply_markup=kb)
    _url, body = http.posts[0]
    assert body["reply_markup"] == kb


@pytest.mark.asyncio
async def test_answer_callback_posts():
    http = FakeHTTP()
    c = TelegramClient(TelegramConfig(bot_token="T", chat_id="42"), http)
    await c.answer_callback("cb1")
    url, body = http.posts[0]
    assert url.endswith("/answerCallbackQuery") and body["callback_query_id"] == "cb1"
