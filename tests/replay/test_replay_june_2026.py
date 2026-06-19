# tests/replay/test_replay_june_2026.py
"""ACCEPTANCE TEST: prove the breaker fires across a multi-day
overtrading-after-a-win sequence (see june_2026.py for the scenario)."""
from governor.config import RulesConfig
from governor.model import ActionType, Severity
from governor.rules.engine import evaluate

from .june_2026 import JUN5_OVERNIGHT, JUN5_WIN, JUN10_CHURN

CFG = RulesConfig()


def _ids(trips):
    return {t.rule_id for t in trips}


def test_jun5_win_would_have_locked_out_futures():
    trips = evaluate(JUN5_WIN, CFG)
    assert "futures.house_money_lockout" in _ids(trips)
    assert any(
        t.rule_id == "futures.house_money_lockout"
        and t.severity is Severity.HARD
        and t.action is ActionType.LOCKOUT_FUTURES_48H
        for t in trips
    )


def test_jun5_overnight_would_have_flagged_size():
    trips = evaluate(JUN5_OVERNIGHT, CFG)
    ids = _ids(trips)
    assert "futures.overnight_notional" in ids
    assert "futures.live_notional" in ids
    overnight = next(t for t in trips if t.rule_id == "futures.overnight_notional")
    assert overnight.severity is Severity.HARD


def test_jun10_would_have_halted_overtrading():
    trips = evaluate(JUN10_CHURN, CFG)
    ids = _ids(trips)
    assert "futures.overtrading" in ids
    assert "futures.same_contract_churn" in ids
    assert "futures.daily_loss_stop" in ids
    overtrading = next(t for t in trips if t.rule_id == "futures.overtrading")
    assert overtrading.severity is Severity.HARD  # trade count far exceeds the hard limit
