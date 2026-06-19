# src/governor/rules/engine.py
"""The rule registry and the single entry point: evaluate().

evaluate() is a pure function — no I/O, no mutation, deterministic. Later plans
(live wiring, comms) call only this.
"""
from __future__ import annotations

from collections.abc import Callable

from ..config import EquitiesRules, FuturesRules, PortfolioRules, RulesConfig
from ..model import StateSnapshot, Trip
from . import equities, futures, portfolio

FuturesRule = Callable[[StateSnapshot, FuturesRules], "Trip | None"]
EquityRule = Callable[[StateSnapshot, EquitiesRules], "Trip | None"]
PortfolioRule = Callable[[StateSnapshot, PortfolioRules], "Trip | None"]

FUTURES_RULES: tuple[FuturesRule, ...] = (
    futures.house_money_lockout,
    futures.daily_loss_stop,
    futures.overtrading,
    futures.overnight_notional,
    futures.live_notional,
    futures.same_contract_churn,
)

EQUITY_RULES: tuple[EquityRule, ...] = (
    equities.single_name,
    equities.sector_concentration,
    equities.retrade_churn,
    equities.add_into_drawdown,
)

PORTFOLIO_RULES: tuple[PortfolioRule, ...] = (
    portfolio.margin_cushion,
    portfolio.gross_leverage,
    portfolio.drawdown_moratorium,
)


def evaluate(snapshot: StateSnapshot, config: RulesConfig) -> list[Trip]:
    """Run every registered rule against the snapshot; return all trips, in
    registry order. Empty list means nothing tripped."""
    trips: list[Trip] = []
    for rule in FUTURES_RULES:
        t = rule(snapshot, config.futures)
        if t is not None:
            trips.append(t)
    for rule in EQUITY_RULES:
        t = rule(snapshot, config.equities)
        if t is not None:
            trips.append(t)
    for rule in PORTFOLIO_RULES:
        t = rule(snapshot, config.portfolio)
        if t is not None:
            trips.append(t)
    return trips
