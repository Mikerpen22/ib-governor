# tests/rules/test_portfolio.py
from governor.config import PortfolioRules
from governor.model import Severity, StateSnapshot
from governor.rules import portfolio

C = PortfolioRules()


def _snap(**kw):
    base = dict(ts="t", nav=250_000.0, margin_cushion=0.55, gross_leverage=1.0, drawdown_pct=0.0)
    base.update(kw)
    return StateSnapshot(**base)


def test_cushion_alerts_when_low():
    assert portfolio.margin_cushion(_snap(margin_cushion=0.20), C) is not None
    assert portfolio.margin_cushion(_snap(margin_cushion=0.30), C) is None


def test_leverage_alerts_when_high():
    assert portfolio.gross_leverage(_snap(gross_leverage=2.5), C) is not None
    assert portfolio.gross_leverage(_snap(gross_leverage=1.5), C) is None


def test_drawdown_moratorium():
    t = portfolio.drawdown_moratorium(_snap(drawdown_pct=0.12), C)
    assert t is not None and t.severity is Severity.WARN
    assert portfolio.drawdown_moratorium(_snap(drawdown_pct=0.08), C) is None


def test_margin_cushion_returns_none_when_nav_zero():
    # A blind/no-data snapshot (nav=0) must not fire — the BRAKE-BLIND path owns that signal.
    assert portfolio.margin_cushion(_snap(nav=0, margin_cushion=0.0), C) is None


def test_drawdown_moratorium_returns_none_when_nav_zero():
    # A blind/no-data snapshot (nav=0) must not fire — the BRAKE-BLIND path owns that signal.
    assert portfolio.drawdown_moratorium(_snap(nav=0, drawdown_pct=0.15), C) is None
