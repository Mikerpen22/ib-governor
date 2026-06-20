"""_confirm_token must tolerate how a real phone user actually sends a confirm:
lowercase, backtick-wrapped (copied from the markdown reply), or with a leading
word like 'Reply CONFIRM ...'. A miss here silently routes the message to the
~70s agent instead of placing the order the user believes they confirmed."""
from __future__ import annotations

import pytest

from governor.live.daemon import _confirm_token

TOK = "A1B2C3D4E5F60789"  # 16-hex, the real staged-order token shape


@pytest.mark.parametrize("text,expected", [
    (f"CONFIRM {TOK}", TOK),
    (f"confirm {TOK.lower()}", TOK),                 # lowercase keyword + token
    (f"Reply CONFIRM {TOK}", TOK),                   # the exact line the agent suggests
    (f"Reply CONFIRM `{TOK}`", TOK),                 # copied with markdown backticks
    (f"please confirm {TOK.lower()}", TOK),          # leading word + lowercase
    (f"CONFIRM {TOK} thanks", TOK),                  # trailing chatter
    (f"  confirm   {TOK}  ", TOK),                   # stray whitespace
])
def test_extracts_token_from_realistic_inputs(text, expected):
    assert _confirm_token(text) == expected


@pytest.mark.parametrize("text", [
    "buy 100 ORCL",          # an order request, not a confirm
    "hello there",           # chatter
    "confirm",               # keyword, no token
    "CONFIRM hi",            # token too short / not a token
    "is this confirmed?",    # 'confirm' substring, no token word
    "",
])
def test_non_confirm_messages_return_none(text):
    assert _confirm_token(text) is None
