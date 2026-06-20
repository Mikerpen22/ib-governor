"""Thin async Telegram Bot API client over an injected httpx-like client.
send() posts a message; poll() long-polls getUpdates and returns texts from the
configured chat only (chat-id auth) plus the next offset."""
from __future__ import annotations

import logging

from ..config import TelegramConfig

log = logging.getLogger("governor.telegram")


class TelegramClient:
    def __init__(self, cfg: TelegramConfig, http) -> None:
        self._cfg = cfg
        self._http = http
        self._base = f"https://api.telegram.org/bot{cfg.bot_token}"

    async def send(self, text: str, parse_mode: str | None = None,
                   reply_markup: dict | None = None) -> None:
        payload: dict = {"chat_id": self._cfg.chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode  # "HTML" → <b>/<i>/<code> render
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup  # e.g. an inline_keyboard
        try:
            resp = await self._http.post(f"{self._base}/sendMessage", json=payload)
            resp.raise_for_status()
        except Exception as exc:  # comms failure must not crash the brake
            log.error("telegram send failed: %s", exc)

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        """Acknowledge an inline-button tap so Telegram clears the loading spinner."""
        try:
            resp = await self._http.post(
                f"{self._base}/answerCallbackQuery",
                json={"callback_query_id": callback_id, "text": text},
            )
            resp.raise_for_status()
        except Exception as exc:  # comms failure must not crash the brake
            log.error("telegram answerCallbackQuery failed: %s", exc)

    async def poll(self, offset: int) -> tuple[list[str], list[dict], int]:
        """Long-poll getUpdates. Returns (texts, callbacks, next_offset) — both the
        text messages and the inline-button taps from our chat (chat-id auth)."""
        resp = await self._http.get(
            f"{self._base}/getUpdates",
            params={"offset": offset, "timeout": self._cfg.poll_timeout},
        )
        resp.raise_for_status()
        data = resp.json()
        texts: list[str] = []
        callbacks: list[dict] = []
        next_offset = offset
        for upd in data.get("result", []):
            next_offset = max(next_offset, upd["update_id"] + 1)
            msg = upd.get("message") or {}
            if "text" in msg and str(msg.get("chat", {}).get("id")) == str(self._cfg.chat_id):
                texts.append(msg["text"])
            cb = upd.get("callback_query") or {}
            if cb:
                cb_chat = cb.get("message", {}).get("chat", {}).get("id")
                if str(cb_chat) == str(self._cfg.chat_id):  # chat-id auth on taps too
                    callbacks.append({"id": cb.get("id"), "data": cb.get("data", "")})
        return texts, callbacks, next_offset
