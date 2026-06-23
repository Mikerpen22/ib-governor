"""House-style HTML for the daemon's own messages (pure builders).

Substring guarantees the routing/handle tests rely on must survive the HTML
wrapping: the severity tag stays a literal `[hard]`, a staged token stays
copy-pasteable, and every message round-trips through strip_tags to clean text.
"""
from __future__ import annotations

from governor.comms.format import strip_tags
from governor.live.daemon import help_message, rule_alert, staged_action_message
from governor.model import ActionType, AssetClass, Severity, Trip


def _trip():
    return Trip(rule_id="futures.daily_loss", asset_class=AssetClass.FUTURE,
                severity=Severity.HARD, message="down $1,200 & counting",
                action=ActionType.TRIM_FUTURES)


def test_rule_alert_keeps_severity_tag_literal_and_escapes_message():
    s = rule_alert(_trip())
    assert "[hard]" in s                          # severity tag stays a literal substring
    assert "<b>futures.daily_loss</b>" in s       # rule id bolded
    assert "&amp;" in s                           # the '&' in the message is escaped
    assert strip_tags(s) == "🛑 futures.daily_loss [hard] — down $1,200 & counting"


def test_staged_action_message_keeps_token_copyable():
    s = staged_action_message("trim_futures", "DRY-RUN", 300, "TOK1")
    assert "TOK1" in s                            # confirm token survives formatting
    assert "<code>trim_futures</code>" in s
    assert "DRY-RUN" in s


def test_help_message_is_html_and_mentions_the_brake():
    s = help_message()
    assert "<b>" in s and "brake" in s.lower()
    assert "&lt;token&gt;" in s                   # the literal <token> grammar is escaped


def test_help_message_covers_the_ask_lane_and_shortcuts():
    s = help_message()
    assert "read-only" in s.lower()               # advertises the question lane
    assert "/leverage" in s and "/positions" in s  # menu shortcuts listed
