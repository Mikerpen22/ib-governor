"""One-shot Telegram + macOS notifier CLI.

Usage:
    python -m governor.comms.send "your message here"
    echo "message" | python -m governor.comms.send

Behavior:
- Loads .env file for Telegram credentials
- Reads message from argv args (joined) or stdin
- If Telegram is configured, sends via Telegram
- Always calls macOS notify() (best-effort)
- Prints whether Telegram was sent or skipped
"""
from __future__ import annotations

import asyncio
import sys

from ..config import load_env_file, telegram_from_env
from .notify import notify
from .telegram import TelegramClient


def main() -> None:
    load_env_file()

    # Collect message from argv or stdin
    argv_args = sys.argv[1:]
    if argv_args:
        text = " ".join(argv_args)
    else:
        text = sys.stdin.read().strip()

    # macOS notification (always; truncate to 200 chars for the banner)
    notify("Daily Summary", text[:200])

    # Telegram (conditional on config)
    cfg = telegram_from_env()
    if cfg.enabled:
        async def _send() -> None:
            import httpx
            async with httpx.AsyncClient() as client:
                tg = TelegramClient(cfg, client)
                await tg.send(text)

        asyncio.run(_send())
        print("Telegram: sent")
    else:
        print("Telegram: skipped (not configured)")


if __name__ == "__main__":  # pragma: no cover
    main()
