"""Shared fixtures. `make_snapshot` builds a benign, flat account; a test
overrides only the fields it cares about, keeping each test focused."""
import pytest

from governor.config import RulesConfig
from governor.model import StateSnapshot


@pytest.fixture
def config() -> RulesConfig:
    return RulesConfig()


@pytest.fixture
def make_snapshot():
    def _make(**overrides) -> StateSnapshot:
        base = dict(
            ts="2026-06-17T10:00:00-04:00",
            nav=250_000.0,
            futures_realized_pnl_today=0.0,
            futures_trade_count_today=0,
            futures_losing_trades_today=0,
            futures_notional=0.0,
            futures_contracts_overnight=0.0,
            minutes_to_futures_close=None,
            contract_trade_counts_today={},
            # Plan 4a: benign portfolio defaults so engine tests stay green
            margin_cushion=0.60,
            gross_leverage=0.0,
            drawdown_pct=0.0,
        )
        base.update(overrides)
        return StateSnapshot(**base)

    return _make
