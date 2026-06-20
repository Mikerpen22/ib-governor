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
    d.alert = d._alerts.append
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
        return 0, "submit: BUY 100 ORCL — PLACED", ""

    monkeypatch.setattr(daemon_mod, "_gate_submit", _fake_gate_submit)

    d = _daemon(tmp_path)  # tokens gate empty -> not an action confirm
    await d.handle_telegram_text("CONFIRM ORDERTOKEN9")

    assert submit_calls == ["ORDERTOKEN9"]
    assert any("PLACED" in a for a in d._alerts)


async def test_gate_submit_error_is_relayed(tmp_path, monkeypatch):
    async def _fake_gate_submit(token, timeout):
        return 1, "", "ERROR: this order was BLOCKED by the gate"

    monkeypatch.setattr(daemon_mod, "_gate_submit", _fake_gate_submit)

    d = _daemon(tmp_path)
    await d.handle_telegram_text("CONFIRM BLOCKEDTOKEN")
    assert any("BLOCKED" in a for a in d._alerts)


async def test_natural_language_routes_to_agent(tmp_path, monkeypatch):
    seen = []

    async def _fake_agent(text, cfg):
        seen.append(text)
        return "GO — BUY 100 ORCL. Reply CONFIRM ABC123"

    monkeypatch.setattr(daemon_mod, "run_agent", _fake_agent)

    d = _daemon(tmp_path)
    await d.handle_telegram_text("buy me 100 shares of oracle")

    assert seen == ["buy me 100 shares of oracle"]
    assert any("CONFIRM ABC123" in a for a in d._alerts)


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
    assert d._alerts == []       # stays quiet
