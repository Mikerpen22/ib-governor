"""Immutable domain model for the circuit-breaker.

These types carry NO behavior beyond construction. The rule engine consumes a
`StateSnapshot` and produces `Trip`s. Everything is frozen — the engine derives,
never mutates (project immutability rule).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AssetClass(str, Enum):
    FUTURE = "future"
    EQUITY = "equity"
    PORTFOLIO = "portfolio"


class Severity(str, Enum):
    """How hard the brake pulls when a rule trips."""

    INFO = "info"   # briefing-level, no action
    WARN = "warn"   # alert + suggestion
    HARD = "hard"   # alert + staged stop-action awaiting confirm


class ActionType(str, Enum):
    """The corrective action a trip stages. Plan 1 only *describes* it; the
    comms/actions layer (a later plan) executes it after the user confirms."""

    NONE = "none"
    ALERT_ONLY = "alert_only"
    LOCKOUT_FUTURES_48H = "lockout_futures_48h"
    PLATFORM_OFF_TODAY = "platform_off_today"
    TRIM_FUTURES = "trim_futures"


@dataclass(frozen=True)
class Trip:
    """A single rule firing. Pure data: carries the intended action but never
    executes it."""

    rule_id: str
    asset_class: AssetClass
    severity: Severity
    message: str
    action: ActionType = ActionType.ALERT_ONLY
    # frozen=True blocks attribute reassignment but not interior dict mutation;
    # context is treated as immutable by convention — the engine never mutates it.
    context: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class StateSnapshot:
    """Immutable point-in-time view of the account, as the rule engine sees it.

    v1 carries NAV plus the fields the FUTURES rules need. Later versions extend
    it (equities). The live snapshot *builder* (Plan 2) populates these
    from IBKR data; here they default to a benign, flat account.
    """

    ts: str                                  # ISO-8601 timestamp
    nav: float                               # net liquidation value, USD
    futures_realized_pnl_today: float = 0.0  # realized futures P&L since session open
    futures_trade_count_today: int = 0
    futures_losing_trades_today: int = 0
    futures_notional: float = 0.0            # abs current futures notional, USD
    futures_contracts_overnight: float = 0.0 # MNQ-equivalent held into the close window
    minutes_to_futures_close: float | None = None
    # frozen=True blocks attribute reassignment but not interior dict mutation;
    # this dict is treated as immutable by convention — the engine never mutates it.
    contract_trade_counts_today: dict[str, int] = field(default_factory=dict)
    # Plan 2 additions: portfolio-level account metrics (forward-compatible, default to 0.0)
    margin_cushion: float = 0.0              # ExcessLiquidity / NAV ratio
    gross_leverage: float = 0.0             # GrossPositionValue / NAV ratio
    # equities / portfolio (Plan 4a)
    drawdown_pct: float = 0.0                  # fraction below NAV high-water-mark, 0..1
    sector_weights: dict[str, float] = field(default_factory=dict)   # sector -> fraction of NAV
    name_weights: dict[str, float] = field(default_factory=dict)     # symbol -> fraction of NAV
    name_trade_counts_week: dict[str, int] = field(default_factory=dict)
    equity_adds_at_loss_today: tuple[str, ...] = ()  # equity names added-to today that are at a loss
