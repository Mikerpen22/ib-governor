"""Telegram-HTML house-style formatting helpers.

Telegram's "HTML" parse mode supports only a small tag set (<b> <i> <u> <s>
<code> <pre> <a>) and requires the three reserved characters & < > to be
escaped *everywhere they appear as literals* — including inside dynamic content
like a symbol, an error string, or an account number. An unescaped '<' or a
stray '&' makes Telegram reject the whole message with HTTP 400, which is why
TelegramClient.send falls back to plain text: a formatting bug degrades, it
never silently drops a brake alert.

House style (keep it consistent across the daemon + the skills):
  - bold section headers anchored by an emoji:  💰 <b>Book</b>
  - a blank line between sections — never decorative rules like ─────
  - italic for asides / examples
  - <code> for anything a user might copy: tokens, symbols, raw numbers
  - never hand-build a tag around un-escaped dynamic text — route it through esc()

Everything here is pure (str -> str): no I/O, no network, trivially unit-tested.
"""
from __future__ import annotations

import re

_TAG_RE = re.compile(r"<[^>]+>")


def esc(s) -> str:
    """Escape the three reserved HTML characters Telegram requires. '&' first so
    we don't double-escape the entities we introduce."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def b(s) -> str:
    """Bold, with the content escaped."""
    return f"<b>{esc(s)}</b>"


def i(s) -> str:
    """Italic, with the content escaped."""
    return f"<i>{esc(s)}</i>"


def u(s) -> str:
    """Underline, with the content escaped."""
    return f"<u>{esc(s)}</u>"


def code(s) -> str:
    """Monospace — for tokens / symbols / numbers a user might copy."""
    return f"<code>{esc(s)}</code>"


def pre(s) -> str:
    """Monospace block — for aligned numeric tables (columns line up). Content is
    escaped; Telegram requires & < > escaped even inside <pre>."""
    return f"<pre>{esc(s)}</pre>"


def header(emoji: str, title: str) -> str:
    """A bold section header anchored by an emoji: '💰 <b>Book</b>'."""
    return f"{emoji} {b(title)}"


def section(head: str, lines: list[str]) -> str:
    """A header line followed by its body lines (already formatted)."""
    return "\n".join([head, *lines])


def joinsections(*sections: str) -> str:
    """Blank-line-separated sections — the house paragraph break. Empty
    sections are dropped so an absent block doesn't leave a double gap."""
    return "\n\n".join(s for s in sections if s)


def strip_tags(s: str) -> str:
    """Render Telegram-HTML down to plain text — for the macOS banner and the
    send() plain-text fallback. Drop tags, then unescape the three entities so
    the reader sees 'AT&T', not 'AT&amp;T'. Unescape '&amp;' last (reverse of
    esc) so '&amp;lt;' can't collapse into a stray '<'."""
    plain = _TAG_RE.sub("", s)
    return plain.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
