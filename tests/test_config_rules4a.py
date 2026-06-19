from governor.config import EquitiesRules, PortfolioRules, RulesConfig


def test_defaults():
    e, p = EquitiesRules(), PortfolioRules()
    assert e.single_name_pct == 0.15
    assert e.sector_pct == 0.25
    assert e.retrade_per_week == 2
    assert p.min_cushion == 0.25
    assert p.max_gross_leverage == 2.0
    assert p.drawdown_moratorium_pct == 0.10


def test_rulesconfig_wires_them():
    c = RulesConfig()
    assert isinstance(c.equities, EquitiesRules) and isinstance(c.portfolio, PortfolioRules)
