# tests/test_config_setup.py
"""Task 8: SetupRules config defaults + override.

The three config models (EquitySetupRules, FuturesSetupRules, SetupRules) and
RulesConfig.setup are implemented in config.py (Task 3 / Phase 1). This file
is the dedicated test for those models.
"""
from governor.config import RulesConfig, SetupRules, EquitySetupRules, FuturesSetupRules


def test_defaults_present():
    c = RulesConfig()
    assert isinstance(c.setup, SetupRules)
    assert c.setup.min_bars == 200
    assert c.setup.equities.stage2_confirmed_min == 6
    assert c.setup.futures.atr_elevated_pctile == 0.70


def test_override_via_validate():
    c = RulesConfig.model_validate({"setup": {"equities": {"stage2_confirmed_min": 7}}})
    assert c.setup.equities.stage2_confirmed_min == 7
