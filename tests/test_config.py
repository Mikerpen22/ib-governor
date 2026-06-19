import os
from pathlib import Path

import pytest

from governor.config import FuturesRules, RulesConfig, load_config, load_env_file


def test_defaults_match_vault_trigger_card():
    f = FuturesRules()
    assert f.house_money_win_usd == 3000.0
    assert f.daily_loss_usd == 1500.0
    assert f.max_overnight_contracts == 2.0
    assert f.overtrading_hard == 20


def test_rulesconfig_has_futures_by_default():
    assert isinstance(RulesConfig().futures, FuturesRules)


def test_load_config_reads_yaml(tmp_path: Path):
    p = tmp_path / "rules.yaml"
    p.write_text("futures:\n  house_money_win_usd: 5000\n")
    cfg = load_config(p)
    assert cfg.futures.house_money_win_usd == 5000.0
    # unspecified fields keep their defaults
    assert cfg.futures.daily_loss_usd == 1500.0


def test_load_config_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.yaml")


def test_load_config_rejects_bad_value(tmp_path: Path):
    p = tmp_path / "rules.yaml"
    p.write_text("futures:\n  house_money_win_usd: -1\n")  # must be positive
    with pytest.raises(ValueError):
        load_config(p)


def test_shipped_rules_yaml_is_valid():
    cfg = load_config(Path("config/rules.yaml"))
    assert cfg.futures.house_money_win_usd > 0


def test_load_config_rejects_malformed_yaml(tmp_path: Path):
    p = tmp_path / "rules.yaml"
    p.write_text("futures:\n  house_money_win_usd: [\n")  # unclosed bracket
    with pytest.raises(ValueError, match="invalid YAML"):
        load_config(p)


# ---------------------------------------------------------------------------
# load_env_file tests
# ---------------------------------------------------------------------------

class TestLoadEnvFile:
    def test_populates_key_from_env_file(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("MY_TEST_VAR=hello\n")
        monkeypatch.delenv("MY_TEST_VAR", raising=False)
        load_env_file(env)
        assert os.environ["MY_TEST_VAR"] == "hello"

    def test_ignores_comment_lines(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("# this is a comment\nMY_REAL_VAR=world\n")
        monkeypatch.delenv("MY_REAL_VAR", raising=False)
        load_env_file(env)
        assert os.environ["MY_REAL_VAR"] == "world"

    def test_does_not_override_existing_env_var(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("EXISTING_VAR=from_file\n")
        monkeypatch.setenv("EXISTING_VAR", "original")
        load_env_file(env)
        assert os.environ["EXISTING_VAR"] == "original"

    def test_absent_file_is_noop(self, tmp_path, monkeypatch):
        """Missing .env must not raise."""
        load_env_file(tmp_path / ".env")  # file does not exist — should be silent

    def test_skips_blank_lines(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("\n\nBLANK_LINE_VAR=yes\n\n")
        monkeypatch.delenv("BLANK_LINE_VAR", raising=False)
        load_env_file(env)
        assert os.environ["BLANK_LINE_VAR"] == "yes"

    def test_value_with_equals_sign_is_preserved(self, tmp_path, monkeypatch):
        """Values containing '=' (e.g. base64) must be preserved intact."""
        env = tmp_path / ".env"
        env.write_text("BOT_TOKEN=abc=def==\n")
        monkeypatch.delenv("BOT_TOKEN", raising=False)
        load_env_file(env)
        assert os.environ["BOT_TOKEN"] == "abc=def=="
