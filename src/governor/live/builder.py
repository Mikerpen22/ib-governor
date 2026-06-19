"""Shared live-snapshot builder — single source of truth for daemon.build() and gate CLI.

Both the daemon and the pre-trade gate need to compose a StateSnapshot from live IBKR
data.  They are identical except for one detail: the daemon *writes* the high-water-mark
(updates the peak), while the gate reads it without mutation.  ``build_live_snapshot``
captures that distinction via ``mutate_hwm``.
"""
from __future__ import annotations

import datetime as dt

from ib_async import ContFuture

from ..model import StateSnapshot
from .snapshot import _to_float, account_metrics, build_snapshot, equity_adds_at_loss, is_sec_type


def live_mnq_notional(ib) -> float | None:
    """Live MNQ single-contract notional (front-month price × multiplier), used to
    normalize futures exposure into MNQ-equivalent contracts. Returns None if it can't
    be fetched (caller falls back to the configured default). FAIL-SOFT: never raises."""
    try:
        qualified = ib.qualifyContracts(ContFuture("MNQ", "CME"))
        if not qualified:
            return None
        c = qualified[0]
        ticker = ib.reqTickers(c)[0]
        price = ticker.marketPrice()
        if not price or price <= 0:
            price = getattr(ticker, "close", None)
        mult = _to_float(getattr(c, "multiplier", None)) or 2.0  # MNQ multiplier is 2
        if price and price > 0:
            return float(price) * mult
        return None
    except Exception:  # noqa: BLE001 — fail soft to the configured default; mnq is non-critical
        return None


def build_live_snapshot(
    ib,
    config,
    *,
    sector_resolver,
    trade_log,
    hwm,
    now: dt.datetime,
    mutate_hwm: bool,
) -> StateSnapshot:
    """Compose a StateSnapshot from live IBKR data.

    Single source of truth for both the daemon (``mutate_hwm=True`` — updates the
    high-water-mark peak on disk) and the pre-trade gate (``mutate_hwm=False`` — reads
    drawdown without mutating peak state).

    Parameters
    ----------
    ib:
        A connected IB (ib_async) instance with accountValues/portfolio/fills methods.
    config:
        The full ``RulesConfig``; ``config.live`` is passed through to ``build_snapshot``.
    sector_resolver:
        A ``SectorResolver`` instance (or compatible duck-type) whose ``.map_for(syms)``
        returns a ``{symbol: sector}`` dict.
    trade_log:
        A ``WeeklyTradeLog`` instance (or compatible duck-type).
    hwm:
        A ``HwmStore`` instance (or compatible duck-type).
    now:
        The current ET-aware datetime (callers own clock / monkeypatching).
    mutate_hwm:
        ``True``  → call ``hwm.update(nav)`` (writes new peak to disk).
        ``False`` → call ``hwm.drawdown_pct(nav)`` (read-only; no side-effect).
    """
    account_values = ib.accountValues()
    portfolio_items = ib.portfolio()
    fills = ib.fills()

    # Derive NAV once; reuse for both HWM step and build_snapshot.
    acct_metrics = account_metrics(account_values)
    nav = acct_metrics[0]

    if mutate_hwm:
        _, hwm_drawdown_pct = hwm.update(nav)
    else:
        hwm_drawdown_pct = hwm.drawdown_pct(nav)

    # Resolve sectors for all held equity symbols (cached; fail-safe to 'unknown').
    equity_syms = [
        it.contract.symbol
        for it in portfolio_items
        if is_sec_type(it, "STK")
    ]
    sector_by_symbol = sector_resolver.map_for(equity_syms)

    # Weekly trade counts from the rolling log.
    name_trade_counts_week = trade_log.counts_within(now)

    # Equity names bought today that are currently at unrealized loss.
    adds_at_loss = equity_adds_at_loss(fills, portfolio_items)

    return build_snapshot(
        now=now,
        account_values=account_values,
        portfolio_items=portfolio_items,
        fills=fills,
        cfg=config.live,
        sector_by_symbol=sector_by_symbol,
        name_trade_counts_week=name_trade_counts_week,
        hwm_drawdown_pct=hwm_drawdown_pct,
        equity_adds_at_loss_today=adds_at_loss,
        precomputed_account_metrics=acct_metrics,
        mnq_notional_usd=live_mnq_notional(ib),
    )
