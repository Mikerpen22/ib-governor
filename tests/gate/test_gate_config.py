"""Tests for GateRules config and its wiring into RulesConfig (Task 3).
Also covers the gate_client_id / _gate_connection_config seam (Part 1).
"""
import pytest
from pydantic import ValidationError

from governor.config import GateRules, RulesConfig
from governor.gate.cli import _gate_connection_config


class TestGateRulesDefaults:
    def test_default_max_trade_pct_nav(self) -> None:
        assert GateRules().max_trade_pct_nav == 0.015

    def test_rulesconfig_gate_default(self) -> None:
        assert RulesConfig().gate.max_trade_pct_nav == 0.015


class TestGateRulesCustom:
    def test_custom_value_via_dict(self) -> None:
        cfg = RulesConfig(gate={"max_trade_pct_nav": 0.02})
        assert cfg.gate.max_trade_pct_nav == pytest.approx(0.02)

    def test_custom_value_via_instance(self) -> None:
        cfg = RulesConfig(gate=GateRules(max_trade_pct_nav=0.05))
        assert cfg.gate.max_trade_pct_nav == pytest.approx(0.05)


class TestGateClientId:
    """Gate client_id must differ from daemon client_id so they can coexist."""

    def test_gate_connection_config_uses_gate_client_id(self) -> None:
        cfg = RulesConfig()
        gate_cfg = _gate_connection_config(cfg)
        assert gate_cfg.client_id == cfg.live.gate_client_id

    def test_gate_client_id_is_5_by_default(self) -> None:
        cfg = RulesConfig()
        assert _gate_connection_config(cfg).client_id == 5

    def test_gate_client_id_differs_from_daemon_client_id(self) -> None:
        cfg = RulesConfig()
        assert _gate_connection_config(cfg).client_id != cfg.live.client_id

    def test_gate_connection_config_does_not_mutate_live(self) -> None:
        """model_copy must return a new object; original live config is unchanged."""
        cfg = RulesConfig()
        original_daemon_id = cfg.live.client_id
        _gate_connection_config(cfg)
        assert cfg.live.client_id == original_daemon_id


class TestGateRulesValidation:
    def test_zero_raises(self) -> None:
        with pytest.raises(ValidationError):
            GateRules(max_trade_pct_nav=0.0)

    def test_negative_raises(self) -> None:
        with pytest.raises(ValidationError):
            GateRules(max_trade_pct_nav=-0.01)

    def test_above_one_raises(self) -> None:
        with pytest.raises(ValidationError):
            GateRules(max_trade_pct_nav=1.1)

    def test_exactly_one_is_valid(self) -> None:
        g = GateRules(max_trade_pct_nav=1.0)
        assert g.max_trade_pct_nav == 1.0
