"""GOVERNOR_AGENT_SANDBOX forces dry-run so the headless NL agent can never place
an order, independent of (unreliable) Claude Code headless permission matching."""
from __future__ import annotations

from governor.config import RulesConfig
from governor.gate.cli import _maybe_agent_sandbox


def _armed() -> RulesConfig:
    return RulesConfig.model_validate({"live": {"dry_run": False, "readonly": False}})


def test_sandbox_env_forces_dry_run(monkeypatch):
    monkeypatch.setenv("GOVERNOR_AGENT_SANDBOX", "1")
    out = _maybe_agent_sandbox(_armed())
    assert out.live.dry_run is True            # forced safe
    assert out.live.readonly is False          # only dry_run is forced; analyze stays accurate


def test_without_sandbox_env_config_is_untouched(monkeypatch):
    monkeypatch.delenv("GOVERNOR_AGENT_SANDBOX", raising=False)
    out = _maybe_agent_sandbox(_armed())
    assert out.live.dry_run is False           # the daemon's own CONFIRM->submit stays armed


def test_sandbox_env_other_value_is_ignored(monkeypatch):
    monkeypatch.setenv("GOVERNOR_AGENT_SANDBOX", "0")
    assert _maybe_agent_sandbox(_armed()).live.dry_run is False
