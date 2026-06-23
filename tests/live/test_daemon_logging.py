"""The daemon must not leak the bot token into its logs. The Telegram Bot API
embeds the token in the request URL path (/bot<TOKEN>/getUpdates), and httpx logs
every request URL at INFO — so on every 30s poll the token was written to
logs/governor.err.log in plaintext. The daemon's logging setup must raise the
httpx logger to WARNING so those request lines (and the token) stop, while real
HTTP problems (WARNING+) still surface."""
from __future__ import annotations

import logging

from governor.live.daemon import _configure_logging


def test_httpx_logger_is_raised_to_warning():
    # Start from a deliberately noisy state so the assertion is meaningful: if the
    # setup forgot to silence httpx, its effective level would stay at INFO.
    logging.getLogger("httpx").setLevel(logging.INFO)
    _configure_logging()
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
