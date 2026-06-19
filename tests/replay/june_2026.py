# tests/replay/june_2026.py
"""Acceptance scenario: a multi-day overtrading-after-a-win sequence the breaker
is built to catch.

This reconstructs the canonical "give-back" pattern as a sequence of state
snapshots, each a checkpoint the acceptance test evaluates:
  • Day 1 midday: a large realized futures win (house-money trigger).
  • Day 1 close: an oversized futures position carried overnight (notional a
    high fraction of NAV).
  • Day 2: a high-frequency churn day that has turned losing (overtrading +
    same-contract churn + daily-loss stop all in play).

The test asserts which rules trip at each checkpoint. NAV and notionals are
illustrative round numbers chosen so each rule fires; they model no real
account.
"""
from governor.model import StateSnapshot

NAV = 250_000.0

# Day 1 midday — a large realized futures win lands (house-money trigger).
JUN5_WIN = StateSnapshot(
    ts="2026-06-05T13:00:00-04:00",
    nav=NAV,
    futures_realized_pnl_today=11_700.0,
    futures_trade_count_today=8,
    futures_losing_trades_today=1,
    futures_notional=80_000.0,
)

# Day 1 close — an oversized futures position carried into the overnight window
# (notional a high fraction of NAV).
JUN5_OVERNIGHT = StateSnapshot(
    ts="2026-06-05T16:50:00-04:00",
    nav=NAV,
    futures_realized_pnl_today=11_700.0,
    futures_trade_count_today=12,  # also trips overtrading-WARN (intentional realism, not isolated to notional)
    futures_losing_trades_today=2,
    futures_notional=235_000.0,
    futures_contracts_overnight=6.0,
    minutes_to_futures_close=10.0,
)

# Day 2 — a high-frequency churn day that has turned losing.
JUN10_CHURN = StateSnapshot(
    ts="2026-06-10T14:00:00-04:00",
    nav=NAV,
    futures_realized_pnl_today=-2_100.0,
    futures_trade_count_today=79,
    futures_losing_trades_today=11,
    futures_notional=120_000.0,
    contract_trade_counts_today={"MNQU6": 79},
)
