# tests/rules/test_equities.py
from governor.config import EquitiesRules
from governor.model import StateSnapshot
from governor.rules import equities

C = EquitiesRules()


def _snap(**kw):
    base = dict(ts="t", nav=250_000.0)
    base.update(kw)
    return StateSnapshot(**base)


def test_single_name_concentration():
    assert equities.single_name(_snap(name_weights={"NVDA": 0.20}), C) is not None
    assert equities.single_name(_snap(name_weights={"NVDA": 0.10}), C) is None


def test_sector_concentration_and_unknown_is_flagged():
    assert equities.sector_concentration(_snap(sector_weights={"Technology": 0.30}), C) is not None
    assert equities.sector_concentration(_snap(sector_weights={"Technology": 0.20}), C) is None
    # an "unknown" sded bucket over the limit still trips (fail-loud, not silent pass)
    t = equities.sector_concentration(_snap(sector_weights={"unknown": 0.40}), C)
    assert t is not None and "unknown" in t.message.lower()


def test_retrade_churn():
    assert equities.retrade_churn(_snap(name_trade_counts_week={"NVDA": 3}), C) is not None
    assert equities.retrade_churn(_snap(name_trade_counts_week={"NVDA": 2}), C) is None


def test_add_into_drawdown():
    s = _snap(drawdown_pct=0.12, equity_adds_at_loss_today=("SHOP",))
    assert equities.add_into_drawdown(s, C) is not None
    assert equities.add_into_drawdown(_snap(drawdown_pct=0.12), C) is None       # no adds
    assert equities.add_into_drawdown(_snap(equity_adds_at_loss_today=("SHOP",)), C) is None  # DD low
