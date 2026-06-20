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
from governor.config import RulesConfig
from governor.live.daemon import BrakeDaemon
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
    d.alert = d._alerts.append          # loud brake alerts
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
