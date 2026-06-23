"""Tests for the telegram_agent config block (natural-language order path)."""
from __future__ import annotations

from governor.config import RulesConfig, TelegramAgentConfig


def test_defaults_ship_present_and_safe():
    cfg = RulesConfig().telegram_agent
    assert isinstance(cfg, TelegramAgentConfig)
    assert cfg.enabled is True
    assert cfg.claude_bin == "claude"
    # 240s, not 120: the order agent's measured latency runs 60s–180s+; a 120s
    # cap timed out on the slow tail and fell back to the failure message.
    assert cfg.timeout_seconds == 240


def test_overrides_from_yaml_dict():
    cfg = RulesConfig.model_validate(
        {"telegram_agent": {"enabled": False, "claude_bin": "/opt/claude", "timeout_seconds": 60}}
    ).telegram_agent
    assert cfg.enabled is False
    assert cfg.claude_bin == "/opt/claude"
    assert cfg.timeout_seconds == 60
