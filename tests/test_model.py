import dataclasses

import pytest

from governor.model import ActionType, AssetClass, Severity, StateSnapshot, Trip


def test_snapshot_has_safe_defaults():
    s = StateSnapshot(ts="2026-06-17T10:00:00-04:00", nav=250_000.0)
    assert s.futures_realized_pnl_today == 0.0
    assert s.futures_trade_count_today == 0
    assert s.contract_trade_counts_today == {}
    assert s.minutes_to_futures_close is None
    assert s.futures_losing_trades_today == 0
    assert s.futures_notional == 0.0
    assert s.futures_notional_signed == 0.0
    assert s.futures_unrealized_pnl_today == 0.0
    assert s.futures_contracts_overnight == 0.0
    assert s.margin_cushion == 0.0
    assert s.gross_leverage == 0.0
    assert s.drawdown_pct == 0.0
    assert s.sector_weights == {}
    assert s.name_weights == {}
    assert s.name_trade_counts_week == {}
    assert s.equity_adds_at_loss_today == ()


def test_snapshot_is_immutable():
    s = StateSnapshot(ts="2026-06-17T10:00:00-04:00", nav=250_000.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.nav = 1.0  # type: ignore[misc]


def test_trip_defaults_to_alert_only():
    t = Trip(
        rule_id="x",
        asset_class=AssetClass.FUTURE,
        severity=Severity.WARN,
        message="m",
    )
    assert t.action is ActionType.ALERT_ONLY
    assert t.context == {}
