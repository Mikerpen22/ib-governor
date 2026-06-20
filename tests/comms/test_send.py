"""Tests for governor.comms.send — one-shot Telegram + macOS notifier CLI.

Tests run without real network or Telegram config; monkeypatching isolates side
effects.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from governor.config import TelegramConfig


# ---------------------------------------------------------------------------
# Helper: run main() with controlled env
# ---------------------------------------------------------------------------

def _run_main(args: list[str], monkeypatch, notify_mock, cfg: TelegramConfig):
    """Import and call governor.comms.send.main() with argv and patched dependencies.

    Returns the list of messages passed to notify_mock (first arg) and any
    TelegramClient.send calls recorded on tg_send_mock.
    """
    import sys

    from governor.comms import send as send_mod

    recorded_sends: list[str] = []

    async def _fake_tg_send(text: str, parse_mode: str | None = None) -> None:
        recorded_sends.append(text)

    with (
        patch.object(send_mod, "load_env_file", return_value=None),
        patch.object(send_mod, "telegram_from_env", return_value=cfg),
        patch.object(send_mod, "notify", notify_mock),
        patch("sys.argv", ["governor.comms.send"] + args),
    ):
        if cfg.enabled:
            # Patch TelegramClient.send to record calls without real network
            with patch("governor.comms.send.TelegramClient") as MockTG:
                instance = MockTG.return_value
                instance.send = AsyncMock(side_effect=_fake_tg_send)
                send_mod.main()
            return notify_mock, recorded_sends
        else:
            send_mod.main()
            return notify_mock, recorded_sends


# ---------------------------------------------------------------------------
# Tests: macOS notify always called
# ---------------------------------------------------------------------------

class TestNotifyAlwaysCalled:
    def test_notify_called_when_telegram_disabled(self, monkeypatch):
        cfg = TelegramConfig()  # not enabled
        notify_calls: list[tuple] = []
        notify_mock = lambda title, text: notify_calls.append((title, text))

        _run_main(["hello world"], monkeypatch, notify_mock, cfg)

        assert len(notify_calls) == 1
        title, text = notify_calls[0]
        assert title == "Daily Summary"
        assert "hello world" in text

    def test_notify_called_when_telegram_enabled(self, monkeypatch):
        cfg = TelegramConfig(bot_token="bot123", chat_id="42")
        notify_calls: list[tuple] = []
        notify_mock = lambda title, text: notify_calls.append((title, text))

        _run_main(["test message"], monkeypatch, notify_mock, cfg)

        assert len(notify_calls) == 1

    def test_notify_truncates_text_to_200_chars(self, monkeypatch):
        cfg = TelegramConfig()
        notify_calls: list[tuple] = []
        notify_mock = lambda title, text: notify_calls.append((title, text))

        long_msg = "x" * 300
        _run_main([long_msg], monkeypatch, notify_mock, cfg)

        _, text = notify_calls[0]
        assert len(text) <= 200

    def test_multi_arg_joined(self, monkeypatch):
        """Multiple argv args are joined with spaces."""
        cfg = TelegramConfig()
        notify_calls: list[tuple] = []
        notify_mock = lambda title, text: notify_calls.append((title, text))

        _run_main(["hello", "world", "foo"], monkeypatch, notify_mock, cfg)

        _, text = notify_calls[0]
        assert "hello world foo" in text


# ---------------------------------------------------------------------------
# Tests: Telegram send
# ---------------------------------------------------------------------------

class TestTelegramSend:
    def test_skipped_when_not_configured(self, monkeypatch, capsys):
        cfg = TelegramConfig()  # disabled
        notify_mock = MagicMock()

        _run_main(["msg"], monkeypatch, notify_mock, cfg)

        out = capsys.readouterr().out
        assert "skipped" in out.lower() or "not configured" in out.lower()

    def test_sends_when_configured(self, monkeypatch, capsys):
        cfg = TelegramConfig(bot_token="bot123", chat_id="42")
        notify_mock = MagicMock()

        _, recorded = _run_main(["my trade recap"], monkeypatch, notify_mock, cfg)

        assert len(recorded) == 1
        assert "my trade recap" in recorded[0]

    def test_prints_sent_when_configured(self, monkeypatch, capsys):
        cfg = TelegramConfig(bot_token="bot123", chat_id="42")
        notify_mock = MagicMock()

        _run_main(["test"], monkeypatch, notify_mock, cfg)

        out = capsys.readouterr().out
        assert "sent" in out.lower()

    def test_html_flag_passes_parse_mode(self):
        """--html makes the CLI send with Telegram HTML parse_mode."""
        from governor.comms import send as send_mod

        cfg = TelegramConfig(bot_token="bot123", chat_id="42")
        recorded: list[tuple[str, str | None]] = []

        async def _fake(text: str, parse_mode: str | None = None) -> None:
            recorded.append((text, parse_mode))

        with (
            patch.object(send_mod, "load_env_file", return_value=None),
            patch.object(send_mod, "telegram_from_env", return_value=cfg),
            patch.object(send_mod, "notify", MagicMock()),
            patch("sys.argv", ["governor.comms.send", "--html", "<b>hi</b>"]),
            patch("governor.comms.send.TelegramClient") as MockTG,
        ):
            MockTG.return_value.send = AsyncMock(side_effect=_fake)
            send_mod.main()

        assert recorded == [("<b>hi</b>", "HTML")]

    def test_no_html_flag_sends_plain(self):
        """Without --html, parse_mode stays None (plain text — back-compat)."""
        from governor.comms import send as send_mod

        cfg = TelegramConfig(bot_token="bot123", chat_id="42")
        recorded: list[tuple[str, str | None]] = []

        async def _fake(text: str, parse_mode: str | None = None) -> None:
            recorded.append((text, parse_mode))

        with (
            patch.object(send_mod, "load_env_file", return_value=None),
            patch.object(send_mod, "telegram_from_env", return_value=cfg),
            patch.object(send_mod, "notify", MagicMock()),
            patch("sys.argv", ["governor.comms.send", "plain message"]),
            patch("governor.comms.send.TelegramClient") as MockTG,
        ):
            MockTG.return_value.send = AsyncMock(side_effect=_fake)
            send_mod.main()

        assert recorded == [("plain message", None)]


# ---------------------------------------------------------------------------
# Tests: stdin fallback
# ---------------------------------------------------------------------------

class TestStdinFallback:
    def test_reads_from_stdin_when_no_args(self, monkeypatch, capsys):
        """When no argv args, main() reads from stdin."""
        import io
        import sys

        from governor.comms import send as send_mod

        cfg = TelegramConfig()
        notify_calls: list[tuple] = []
        notify_mock = lambda title, text: notify_calls.append((title, text))

        with (
            patch.object(send_mod, "load_env_file", return_value=None),
            patch.object(send_mod, "telegram_from_env", return_value=cfg),
            patch.object(send_mod, "notify", notify_mock),
            patch("sys.argv", ["governor.comms.send"]),
            patch("sys.stdin", io.StringIO("stdin message\n")),
        ):
            send_mod.main()

        assert len(notify_calls) == 1
        _, text = notify_calls[0]
        assert "stdin message" in text
