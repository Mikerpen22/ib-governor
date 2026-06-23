"""Routing tests for the daemon's Telegram message handler.

Three branches: circuit-breaker action confirm (in-memory token), order confirm
(CONFIRM <token> -> gate submit subprocess), and a natural-language request
(-> headless agent). Subprocess + agent are monkeypatched — no real claude, no
real gate process, no network.
"""
from __future__ import annotations

import governor.live.daemon as daemon_mod
from governor.actions.lockout import LockoutStore
from governor.actions.tokens import ConfirmTokenGate
from governor.config import RulesConfig, TelegramConfig
from governor.live.daemon import BrakeDaemon, _confirm_keyboard
from governor.model import ActionType, AssetClass, Severity, StateSnapshot, Trip


def _trip(action):
    return Trip(rule_id="r", asset_class=AssetClass.FUTURE, severity=Severity.HARD,
               message="m", action=action)


def _snap():
    return StateSnapshot(ts="2026-06-17T15:50:00-04:00", nav=250_000.0)


class FakeExecutor:
    def __init__(self):
        self.trims = []

    def cancel_all_orders(self):
        return True

    def trim_futures(self, target_contracts):
        self.trims.append(target_contracts)
        return True


def _daemon(tmp_path, config=None, token="TOK1"):
    d = BrakeDaemon(config or RulesConfig())
    d.executor = FakeExecutor()
    d.tokens = ConfirmTokenGate(300, token_factory=lambda: token)
    d.lockout_store = LockoutStore(tmp_path / "l.json")
    d._alerts = []
    d.alert = lambda text, **kw: d._alerts.append(text)   # loud brake alerts (drop kwargs)
    d._replies = []                     # chat replies (telegram-only)
    d._reply_tokens = []                # token passed per reply (None unless buttons attached)

    async def _cap(text, token=None):
        d._replies.append(text)
        d._reply_tokens.append(token)

    d._reply = _cap
    return d


async def test_action_confirm_routes_to_executor_not_agent(tmp_path, monkeypatch):
    agent_calls = []

    async def _fake_agent(text, cfg):
        agent_calls.append(text)
        return "should not run"

    monkeypatch.setattr(daemon_mod, "run_agent", _fake_agent)

    d = _daemon(tmp_path)
    d.handle([_trip(ActionType.TRIM_FUTURES)], _snap(), "briefing")  # stages TOK1
    await d.handle_telegram_text("CONFIRM TOK1")

    assert len(d.executor.trims) == 1     # the circuit-breaker action executed
    assert agent_calls == []              # never went to the agent


async def test_order_confirm_routes_to_gate_submit(tmp_path, monkeypatch):
    submit_calls = []

    async def _fake_gate_submit(token, timeout):
        submit_calls.append(token)
        return 0, '{"action":"BUY","symbol":"ORCL","quantity":100,"placed":true,"dry_run":false}', ""

    monkeypatch.setattr(daemon_mod, "_gate_submit", _fake_gate_submit)

    d = _daemon(tmp_path)  # tokens gate empty -> not an action confirm
    await d.handle_telegram_text("CONFIRM A1B2C3D4E5F60789")

    assert submit_calls == ["A1B2C3D4E5F60789"]
    assert any("PLACED" in r for r in d._replies)


async def test_gate_submit_error_is_relayed(tmp_path, monkeypatch):
    async def _fake_gate_submit(token, timeout):
        return 1, '{"ok":false,"reason":"BLOCKED","message":"blocked"}', ""

    monkeypatch.setattr(daemon_mod, "_gate_submit", _fake_gate_submit)

    d = _daemon(tmp_path)
    await d.handle_telegram_text("CONFIRM DEADBEEF12345678")
    assert any("BLOCKED" in r for r in d._replies)


async def test_natural_language_routes_to_agent(tmp_path, monkeypatch):
    seen = []

    async def _fake_agent(text, cfg):
        seen.append(text)
        return "GO — BUY 100 ORCL. Reply CONFIRM ABC123"

    monkeypatch.setattr(daemon_mod, "run_agent", _fake_agent)

    d = _daemon(tmp_path)
    await d.handle_telegram_text("buy me 100 shares of oracle")

    assert seen == ["buy me 100 shares of oracle"]
    assert any("CONFIRM ABC123" in r for r in d._replies)


async def test_confirm_prefixed_sentence_is_not_treated_as_a_submit(tmp_path, monkeypatch):
    """A natural-language message that merely starts with 'confirm' must go to the
    agent — not be parsed as `CONFIRM <token>` and pushed at gate submit."""
    submit_calls, agent_seen = [], []

    async def _fake_gate_submit(token, timeout):
        submit_calls.append(token)
        return 0, "PLACED", ""

    async def _fake_agent(text, cfg):
        agent_seen.append(text)
        return "ok"

    monkeypatch.setattr(daemon_mod, "_gate_submit", _fake_gate_submit)
    monkeypatch.setattr(daemon_mod, "run_agent", _fake_agent)

    d = _daemon(tmp_path)
    await d.handle_telegram_text("confirm that oracle is a good buy here")

    assert submit_calls == []                         # not a submit
    assert agent_seen == ["confirm that oracle is a good buy here"]


async def test_help_command_replies_help_without_agent(tmp_path, monkeypatch):
    called = []

    async def _fake_agent(text, cfg):
        called.append(text)
        return "x"

    monkeypatch.setattr(daemon_mod, "run_agent", _fake_agent)
    d = _daemon(tmp_path)
    await d.handle_telegram_text("/start")

    assert called == []                                   # help is not an order
    assert any("brake" in r.lower() for r in d._replies)  # onboarding text


async def test_agent_path_sends_instant_ack_before_the_analysis(tmp_path, monkeypatch):
    async def _fake_agent(text, cfg):
        return "GO — BUY 100 ORCL. Reply CONFIRM ABC123"

    monkeypatch.setattr(daemon_mod, "run_agent", _fake_agent)
    d = _daemon(tmp_path)
    await d.handle_telegram_text("buy 100 ORCL")

    assert len(d._replies) >= 2
    assert "analyz" in d._replies[0].lower()              # ack arrives first (kills the silent void)


async def test_agent_reply_with_token_attaches_confirm_buttons(tmp_path, monkeypatch):
    async def _fake_agent(text, cfg):
        return "🟡 CAUTION — BUY 100 ORCL.\n\nReply CONFIRM A1B2C3D4E5F60789"

    monkeypatch.setattr(daemon_mod, "run_agent", _fake_agent)
    d = _daemon(tmp_path)
    await d.handle_telegram_text("buy 100 ORCL")

    # the analysis reply (last one) carries the token → buttons are attached
    assert d._reply_tokens[-1] == "A1B2C3D4E5F60789"


async def test_block_reply_attaches_no_buttons(tmp_path, monkeypatch):
    async def _fake_agent(text, cfg):
        return "🛑 BLOCKED — a lockout is active. No token provided."

    monkeypatch.setattr(daemon_mod, "run_agent", _fake_agent)
    d = _daemon(tmp_path)
    await d.handle_telegram_text("buy 100 ORCL")

    assert d._reply_tokens[-1] is None          # no token in a BLOCK reply → no buttons


async def test_confirm_button_tap_routes_to_submit(tmp_path, monkeypatch):
    submit_calls = []

    async def _fake_gate_submit(token, timeout):
        submit_calls.append(token)
        return 0, '{"action":"BUY","symbol":"ORCL","quantity":100,"placed":true,"dry_run":false}', ""

    monkeypatch.setattr(daemon_mod, "_gate_submit", _fake_gate_submit)
    d = _daemon(tmp_path)
    await d.handle_callback("confirm:A1B2C3D4E5F60789", callback_id="cb1")

    assert submit_calls == ["A1B2C3D4E5F60789"]
    assert any("PLACED" in r for r in d._replies)


async def test_cancel_button_tap_discards_staged_order(tmp_path, monkeypatch):
    consumed = []

    class _FakeStore:
        def __init__(self, *a, **k):
            pass

        def consume(self, token, now):
            consumed.append(token)
            return {"intent": {"symbol": "ORCL"}, "verdict": "GO"}

    monkeypatch.setattr(daemon_mod, "StagedOrderStore", _FakeStore)
    d = _daemon(tmp_path)
    await d.handle_callback("cancel:A1B2C3D4E5F60789", callback_id="cb1")

    assert consumed == ["A1B2C3D4E5F60789"]
    assert any("ancel" in r for r in d._replies)   # "Cancelled"


class _FakeTelegram:
    """Records edit_message / send so the in-place card-edit path is observable."""
    def __init__(self, edit_ok=True):
        self.edits = []
        self.sends = []
        self._edit_ok = edit_ok

    async def edit_message(self, message_id, text, parse_mode=None, reply_markup=None):
        self.edits.append((message_id, text))
        return self._edit_ok

    async def send(self, text, parse_mode=None, reply_markup=None):
        self.sends.append(text)
        return 1

    async def answer_callback(self, *a, **k):
        pass


# --- namespaced keyboards: order taps vs circuit-breaker action taps ---

def test_order_keyboard_uses_confirm_namespace():
    kb = _confirm_keyboard("A1B2C3D4E5F60789")            # default kind="order"
    btns = kb["inline_keyboard"][0]
    assert btns[0]["callback_data"] == "confirm:A1B2C3D4E5F60789"   # → gate submit
    assert btns[1]["callback_data"] == "cancel:A1B2C3D4E5F60789"


def test_action_keyboard_uses_action_namespace():
    kb = _confirm_keyboard("A1B2C3D4", kind="action")
    btns = kb["inline_keyboard"][0]
    assert btns[0]["callback_data"] == "action:A1B2C3D4"            # → in-memory execute
    assert btns[1]["callback_data"] == "cancel:A1B2C3D4"


async def test_action_button_tap_executes_breaker_not_submit(tmp_path, monkeypatch):
    """An 'action:<token>' tap runs the in-memory circuit-breaker action (trim) —
    NOT the order gate-submit path."""
    submit_calls = []

    async def _fake_gate_submit(token, timeout):
        submit_calls.append(token)
        return 0, "{}", ""

    monkeypatch.setattr(daemon_mod, "_gate_submit", _fake_gate_submit)

    d = _daemon(tmp_path, token="A1B2C3D4")              # hex token: survives _normalize_token
    d.handle([_trip(ActionType.TRIM_FUTURES)], _snap(), "briefing")   # stages it
    await d.handle_callback("action:A1B2C3D4", callback_id="cb1")

    assert len(d.executor.trims) == 1                    # circuit-breaker action executed
    assert submit_calls == []                            # never touched the order path
    assert any("xecuted" in r for r in d._replies)       # "executed" outcome reported


async def test_confirm_tap_edits_card_in_place_when_message_id_known(tmp_path, monkeypatch):
    async def _fake_gate_submit(token, timeout):
        return 0, '{"action":"BUY","symbol":"ORCL","quantity":100,"placed":true,"dry_run":false}', ""

    monkeypatch.setattr(daemon_mod, "_gate_submit", _fake_gate_submit)
    d = _daemon(tmp_path)
    d._telegram_cfg = TelegramConfig(bot_token="T", chat_id="1")     # enable telegram path
    fake = _FakeTelegram(edit_ok=True)
    d.telegram = fake
    await d.handle_callback("confirm:A1B2C3D4E5F60789", callback_id="cb1", message_id=99)

    assert len(fake.edits) == 1 and fake.edits[0][0] == 99           # the tapped card was edited
    assert "PLACED" in fake.edits[0][1]
    assert d._replies == []                                          # no second message spawned


async def test_tap_falls_back_to_reply_when_edit_fails(tmp_path):
    d = _daemon(tmp_path)
    d._telegram_cfg = TelegramConfig(bot_token="T", chat_id="1")
    d.telegram = _FakeTelegram(edit_ok=False)                        # edit rejected
    await d._render_outcome("✅ Placed", message_id=555)
    assert d._replies == ["✅ Placed"]                               # fell back to a fresh reply


async def test_disabled_agent_ignores_natural_language(tmp_path, monkeypatch):
    called = []

    async def _fake_agent(text, cfg):
        called.append(text)
        return "x"

    monkeypatch.setattr(daemon_mod, "run_agent", _fake_agent)

    cfg = RulesConfig.model_validate({"telegram_agent": {"enabled": False}})
    d = _daemon(tmp_path, config=cfg)
    await d.handle_telegram_text("buy me 100 oracle")

    assert called == []          # agent not invoked
    assert d._replies == []      # stays quiet


# --- read-only ask lane: deterministic fast-path off the live connection ------

_FAST_VIEW = {"nav": 250_000.0, "gross_leverage": 1.80, "margin_cushion": 0.45,
              "realized_pnl_today": 0.0, "fills": [], "positions": []}


async def test_ask_question_answered_by_quick_path_without_agent(tmp_path, monkeypatch):
    """A recognized factual question is answered instantly from the account view —
    the agent subprocess is never spawned."""
    agent_calls = []

    async def _fake_agent(text, cfg):
        agent_calls.append(text)
        return "should not run"

    monkeypatch.setattr(daemon_mod, "run_agent", _fake_agent)
    d = _daemon(tmp_path)
    d._account_view = lambda: _FAST_VIEW
    await d.handle_telegram_text("what's my leverage?")

    assert agent_calls == []                          # answered without the agent
    assert any("1.80×" in r for r in d._replies)


async def test_order_message_skips_quick_path_even_with_a_view(tmp_path, monkeypatch):
    seen = []

    async def _fake_agent(text, cfg):
        seen.append(text)
        return "GO — BUY 100 ORCL. Reply CONFIRM A1B2C3D4E5F60789"

    monkeypatch.setattr(daemon_mod, "run_agent", _fake_agent)
    d = _daemon(tmp_path)
    d._account_view = lambda: _FAST_VIEW
    await d.handle_telegram_text("buy 100 ORCL")      # classified ORDER

    assert seen == ["buy 100 ORCL"]                   # routed to the agent, not quick-answer


async def test_unrecognized_ask_falls_through_to_agent(tmp_path, monkeypatch):
    seen = []

    async def _fake_agent(text, cfg):
        seen.append(text)
        return "hmm"

    monkeypatch.setattr(daemon_mod, "run_agent", _fake_agent)
    d = _daemon(tmp_path)
    d._account_view = lambda: _FAST_VIEW
    await d.handle_telegram_text("what do you think about gold")

    assert seen == ["what do you think about gold"]   # no quick pattern → agent


async def test_quick_path_works_even_with_agent_disabled(tmp_path):
    """The deterministic read-only answer doesn't depend on the order agent flag."""
    cfg = RulesConfig.model_validate({"telegram_agent": {"enabled": False}})
    d = _daemon(tmp_path, config=cfg)
    d._account_view = lambda: _FAST_VIEW
    await d.handle_telegram_text("positions?")

    assert any("Book" in r for r in d._replies)       # answered despite agent disabled


async def test_quick_path_skipped_when_view_unavailable(tmp_path, monkeypatch):
    """Disconnected / unreadable account view → fall through to the agent, no crash."""
    seen = []

    async def _fake_agent(text, cfg):
        seen.append(text)
        return "ok"

    monkeypatch.setattr(daemon_mod, "run_agent", _fake_agent)
    d = _daemon(tmp_path)
    d._account_view = lambda: None                    # e.g. not connected
    await d.handle_telegram_text("leverage?")

    assert seen == ["leverage?"]                      # no view → agent fallback
