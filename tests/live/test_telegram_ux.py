"""Pure helpers behind the Telegram UX improvements:
- _friendly_submit_reply: turn gate-submit output into a normie-readable line that
  answers "did money move?" in the first words.
- _is_fast_message: classify a message as cheap (confirm/command, handle inline)
  vs an agent request (slow, run off the poll loop) so a CONFIRM never waits
  ~70s behind an in-flight analysis and expire while queued.
"""
from __future__ import annotations

import json

import pytest

from governor.live.daemon import _friendly_submit_reply, _is_fast_message


def _ok(placed, dry_run):
    return json.dumps({"action": "BUY", "symbol": "SNAP", "quantity": 1.0,
                       "placed": placed, "dry_run": dry_run})


def test_placed_live_says_placed():
    msg = _friendly_submit_reply(0, _ok(placed=True, dry_run=False), "")
    assert msg.lower().startswith("✅".lower()) or "PLACED" in msg
    assert "SNAP" in msg


def test_dry_run_says_practice_not_failure():
    msg = _friendly_submit_reply(0, _ok(placed=False, dry_run=True), "")
    assert "PRACTICE" in msg.upper()
    assert "untouched" in msg.lower()


def _err(reason):
    return json.dumps({"ok": False, "reason": reason, "message": "..."})


def test_block_error_is_clear_no_money_moved():
    msg = _friendly_submit_reply(1, _err("BLOCKED"), "")
    assert "BLOCK" in msg.upper()
    assert "nothing" in msg.lower() or "did not" in msg.lower()


def test_expired_token_is_actionable():
    msg = _friendly_submit_reply(1, _err("EXPIRED"), "")
    assert "expired" in msg.lower() or "again" in msg.lower()


def test_readonly_error_is_clear():
    msg = _friendly_submit_reply(1, _err("READONLY"), "")
    assert "read-only" in msg.lower() or "safe mode" in msg.lower()


def test_unparseable_output_is_uncertain_never_false_success():
    msg = _friendly_submit_reply(0, "garbage not json", "")
    assert "uncertain" in msg.lower()
    assert "✅" not in msg                                   # never assert success on garbage


@pytest.mark.parametrize("text,fast", [
    ("/start", True),
    ("/help", True),
    ("help", True),
    ("CONFIRM A1B2C3D4E5F60789", True),     # order confirm
    ("confirm 1a2b3c4d", True),             # action confirm (8-hex), case-insensitive
    ("buy 100 ORCL", False),                # agent request
    ("should I buy snap?", False),
    ("hello", False),
])
def test_is_fast_message(text, fast):
    assert _is_fast_message(text) is fast
