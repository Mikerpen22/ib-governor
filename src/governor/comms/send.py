"""One-shot Telegram + macOS notifier CLI.

Usage:
    python -m governor.comms.send "your message here"
    python -m governor.comms.send --html "<b>Market Close</b>\n· line one\n· line two"
    echo "message" | python -m governor.comms.send

Behavior:
- Loads .env file for Telegram credentials
- Reads message from argv args (joined) or stdin
- --html renders the Telegram message with HTML formatting (<b>, <i>, <code>, …)
  so it shows bold "headers" + structure instead of one squished block. In HTML
  mode, literal <, >, & in the text must be escaped (&lt; &gt; &amp;).
- If Telegram is configured, sends via Telegram
- Always calls macOS notify() (best-effort; HTML tags stripped for the banner)
- Prints whether Telegram was sent or skipped
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys

from ..config import load_env_file, telegram_from_env
from .notify import notify
from .telegram import TelegramClient

_TAG_RE = re.compile(r"<[^>]+>")


def main() -> None:
    load_env_file()

    parser = argparse.ArgumentParser(
        prog="python -m governor.comms.send",
        description="Send a one-shot Telegram message + macOS notification.",
    )
    parser.add_argument("message", nargs="*", help="message text (joined); if omitted, read from stdin")
    parser.add_argument("--html", action="store_true",
                        help="render the Telegram message with HTML formatting (<b>, <i>, <code>, …)")
    args = parser.parse_args()

    text = " ".join(args.message) if args.message else sys.stdin.read().strip()
    parse_mode = "HTML" if args.html else None

    # macOS notification (always; strip any HTML tags + truncate for the banner)
    banner = _TAG_RE.sub("", text) if args.html else text
    notify("Daily Summary", banner[:200])

    # Telegram (conditional on config)
    cfg = telegram_from_env()
    if cfg.enabled:
        async def _send() -> None:
            import httpx
            async with httpx.AsyncClient() as client:
                tg = TelegramClient(cfg, client)
                await tg.send(text, parse_mode=parse_mode)

        asyncio.run(_send())
        print("Telegram: sent")
    else:
        print("Telegram: skipped (not configured)")


if __name__ == "__main__":  # pragma: no cover
    main()
