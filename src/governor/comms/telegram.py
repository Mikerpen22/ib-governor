"""Thin async Telegram Bot API client over an injected httpx-like client.
send() posts a message; poll() long-polls getUpdates and returns texts from the
configured chat only (chat-id auth) plus the next offset."""
from __future__ import annotations

import logging

from ..config import TelegramConfig
from .format import strip_tags

log = logging.getLogger("governor.telegram")


def _message_id(resp) -> int | None:
    """Pull the sent message's id out of a Bot API response, tolerantly (a stub
    or an error body may not carry one)."""
    try:
        return resp.json().get("result", {}).get("message_id")
    except Exception:  # noqa: BLE001 — a missing id is not worth crashing for
        return None


class TelegramClient:
    def __init__(self, cfg: TelegramConfig, http) -> None:
        self._cfg = cfg
        self._http = http
        self._base = f"https://api.telegram.org/bot{cfg.bot_token}"

    async def send(self, text: str, parse_mode: str | None = None,
                   reply_markup: dict | None = None) -> int | None:
        """Post a message. Returns the sent message_id (so a later tap can edit it
        in place), or None on failure."""
        payload: dict = {"chat_id": self._cfg.chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode  # "HTML" → <b>/<i>/<code> render
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup  # e.g. an inline_keyboard
        try:
            resp = await self._http.post(f"{self._base}/sendMessage", json=payload)
            resp.raise_for_status()
            return _message_id(resp)
        except Exception as exc:  # comms failure must not crash the brake
            if not parse_mode:                      # plain send failed → nothing to fall back to
                log.error("telegram send failed: %s", exc)
                return None
            # A parse_mode send rejected for bad markup (Telegram 400) would
            # otherwise drop the message entirely — for a brake alert that's
            # unacceptable. Retry once as plain text (tags stripped) so the words
            # always get through; the buttons (valid in plain mode) ride along.
            log.warning("telegram %s send failed (%s) — retrying as plain text", parse_mode, exc)
        plain: dict = {"chat_id": self._cfg.chat_id, "text": strip_tags(text)}
        if reply_markup is not None:
            plain["reply_markup"] = reply_markup
        try:
            resp = await self._http.post(f"{self._base}/sendMessage", json=plain)
            resp.raise_for_status()
            return _message_id(resp)
        except Exception as exc:  # comms failure must not crash the brake
            log.error("telegram send failed (plain fallback too): %s", exc)
            return None

    async def edit_message(self, message_id: int, text: str, parse_mode: str | None = None,
                           reply_markup: dict | None = None) -> bool:
        """Edit a sent message in place (editMessageText) — used to mutate a confirm
        card into its outcome on a button tap. Returns True on success; a failed
        edit must not crash the brake (the caller falls back to a fresh send)."""
        payload: dict = {"chat_id": self._cfg.chat_id, "message_id": message_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        try:
            resp = await self._http.post(f"{self._base}/editMessageText", json=payload)
            resp.raise_for_status()
            return True
        except Exception as exc:  # comms failure must not crash the brake
            log.error("telegram editMessageText failed: %s", exc)
            return False

    async def set_my_commands(self, commands: list[dict]) -> None:
        """Register the bot's slash-command menu (setMyCommands) so the user sees
        /leverage, /pnl, /positions … as one-tap shortcuts. Best-effort."""
        try:
            resp = await self._http.post(f"{self._base}/setMyCommands",
                                         json={"commands": commands})
            resp.raise_for_status()
        except Exception as exc:  # comms failure must not crash the brake
            log.error("telegram setMyCommands failed: %s", exc)

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
                cb_msg = cb.get("message", {})
                cb_chat = cb_msg.get("chat", {}).get("id")
                if str(cb_chat) == str(self._cfg.chat_id):  # chat-id auth on taps too
                    # message_id lets us edit the tapped card in place on confirm/cancel
                    callbacks.append({"id": cb.get("id"), "data": cb.get("data", ""),
                                      "message_id": cb_msg.get("message_id")})
        return texts, callbacks, next_offset
