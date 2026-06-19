# tests/test_engine.py
from governor.config import RulesConfig
from governor.rules.engine import FUTURES_RULES, evaluate


def test_flat_account_trips_nothing(make_snapshot):
    assert evaluate(make_snapshot(), RulesConfig()) == []


def test_multiple_rules_can_trip_at_once(make_snapshot):
    # A big win AND an oversized overnight book at the close window.
    s = make_snapshot(
        futures_realized_pnl_today=12_000.0,
        futures_contracts_overnight=6.0,
        minutes_to_futures_close=10.0,
        futures_notional=349_000.0,
    )
    ids = {t.rule_id for t in evaluate(s, RulesConfig())}
    assert "futures.house_money_lockout" in ids
    assert "futures.overnight_notional" in ids
    assert "futures.live_notional" in ids


def test_every_registered_rule_is_callable(make_snapshot):
    assert len(FUTURES_RULES) == 6
    cfg = RulesConfig()
    for rule in FUTURES_RULES:
        # Each rule must accept (snapshot, futures_cfg) and return Trip|None.
        assert rule(make_snapshot(), cfg.futures) is None


def test_engine_runs_equities_and_portfolio_rules():
    from governor.config import RulesConfig
    from governor.model import StateSnapshot
    from governor.rules.engine import evaluate
    s = StateSnapshot(ts="t", nav=250_000.0, margin_cushion=0.10,
                      name_weights={"NVDA": 0.30}, sector_weights={"Technology": 0.40})
    ids = {t.rule_id for t in evaluate(s, RulesConfig())}
    assert "portfolio.margin_cushion" in ids
    assert "equities.single_name" in ids
    assert "equities.sector_concentration" in ids
