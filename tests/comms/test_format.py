"""Telegram-HTML house-style formatting helpers (pure str -> str).

The load-bearing property is escaping: any literal & < > in dynamic content
(symbols, error text, account numbers) must be escaped so Telegram's HTML parser
doesn't reject the whole message — and strip_tags must invert it cleanly for the
plain-text fallback + macOS banner.
"""
from __future__ import annotations

from governor.comms.format import (
    b, code, esc, header, i, joinsections, section, strip_tags, u,
)


def test_esc_escapes_the_three_reserved_chars():
    assert esc("a < b & c > d") == "a &lt; b &amp; c &gt; d"


def test_esc_puts_ampersand_first_so_it_does_not_double_escape():
    assert esc("<") == "&lt;"          # not "&amp;lt;"


def test_esc_coerces_non_str():
    assert esc(ValueError("bad <thing>")) == "bad &lt;thing&gt;"


def test_wrappers_wrap_and_escape():
    assert b("AT&T") == "<b>AT&amp;T</b>"
    assert i("x<y") == "<i>x&lt;y</i>"
    assert u("a") == "<u>a</u>"
    assert code("BUY 1 SNAP") == "<code>BUY 1 SNAP</code>"


def test_header_anchors_emoji_and_bolds_title():
    assert header("💰", "Book") == "💰 <b>Book</b>"


def test_section_joins_header_then_lines():
    assert section("Try:", ["• a", "• b"]) == "Try:\n• a\n• b"


def test_joinsections_blank_line_separates_and_drops_empties():
    assert joinsections("a", "", "b") == "a\n\nb"


def test_strip_tags_removes_tags_and_unescapes_entities():
    assert strip_tags("<b>AT&amp;T</b> &lt;x&gt;") == "AT&T <x>"


def test_strip_tags_inverts_esc_for_the_plain_fallback():
    # esc → wrap → strip must yield the original plain text (what the fallback sends)
    assert strip_tags(b("P&L > 0")) == "P&L > 0"
