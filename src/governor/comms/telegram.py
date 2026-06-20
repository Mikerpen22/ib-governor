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

    async def send(self, text: str, parse_mode: str | None = None) -> None:
        payload: dict = {"chat_id": self._cfg.chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode  # "HTML" → <b>/<i>/<code> render
        try:
            resp = await self._http.post(f"{self._base}/sendMessage", json=payload)
            resp.raise_for_status()
        except Exception as exc:  # comms failure must not crash the brake
            log.error("telegram send failed: %s", exc)

    async def poll(self, offset: int) -> tuple[list[str], int]:
        """Long-poll getUpdates. Returns (texts from our chat, next offset)."""
        resp = await self._http.get(
            f"{self._base}/getUpdates",
            params={"offset": offset, "timeout": self._cfg.poll_timeout},
        )
        resp.raise_for_status()
        data = resp.json()
        texts: list[str] = []
        next_offset = offset
        for upd in data.get("result", []):
            next_offset = max(next_offset, upd["update_id"] + 1)
            msg = upd.get("message") or {}
            if str(msg.get("chat", {}).get("id")) != str(self._cfg.chat_id):
                continue  # chat-id auth: ignore anyone but the owner
            if "text" in msg:
                texts.append(msg["text"])
        return texts, next_offset
