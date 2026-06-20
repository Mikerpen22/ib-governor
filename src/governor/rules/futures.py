# src/governor/rules/futures.py
"""Futures safeguard rules. Each is a pure function `(snapshot, cfg) -> Trip|None`
reading only the snapshot and the FuturesRules config. No I/O, no mutation."""
from __future__ import annotations

from ..config import FuturesRules
from ..model import ActionType, AssetClass, Severity, StateSnapshot, Trip


def house_money_lockout(snapshot: StateSnapshot, cfg: FuturesRules) -> Trip | None:
    if snapshot.futures_realized_pnl_today > cfg.house_money_win_usd:
        return Trip(
            rule_id="futures.house_money_lockout",
            asset_class=AssetClass.FUTURE,
            severity=Severity.HARD,
            message=(
                f"Realized futures P&L today is "
                f"+${snapshot.futures_realized_pnl_today:,.0f} "
                f"(> +${cfg.house_money_win_usd:,.0f}). House-money zone — exactly how "
                f"Jun 5–12 started. Staging a 48h futures lockout."
            ),
            action=ActionType.LOCKOUT_FUTURES_48H,
            context={
                "realized_pnl": f"{snapshot.futures_realized_pnl_today:.2f}",
                "threshold": f"{cfg.house_money_win_usd:.2f}",
            },
        )
    return None


def daily_loss_stop(snapshot: StateSnapshot, cfg: FuturesRules) -> Trip | None:
    # [H2] Total day P&L = realized + open (mark-to-market) futures P&L. An open losing
    # position should be able to trip the stop before it is realized (user decision).
    total_pnl = (
        snapshot.futures_realized_pnl_today + snapshot.futures_unrealized_pnl_today
    )
    hit_loss = total_pnl < -cfg.daily_loss_usd
    hit_streak = snapshot.futures_losing_trades_today >= cfg.max_losing_trades
    if hit_loss or hit_streak:
        if hit_loss and hit_streak:
            reason = "daily loss limit and losing-streak limit"
        elif hit_loss:
            reason = "daily loss limit"
        else:
            reason = "losing-streak limit"
        return Trip(
            rule_id="futures.daily_loss_stop",
            asset_class=AssetClass.FUTURE,
            severity=Severity.HARD,
            message=(
                f"Futures {reason} hit: total P&L "
                f"${total_pnl:,.0f} "
                f"(realized ${snapshot.futures_realized_pnl_today:,.0f} + open "
                f"${snapshot.futures_unrealized_pnl_today:,.0f} mark-to-market), "
                f"{snapshot.futures_losing_trades_today} losing trades. "
                f"Platform OFF for the day."
            ),
            action=ActionType.PLATFORM_OFF_TODAY,
            context={
                "total_pnl": f"{total_pnl:.2f}",
                "realized_pnl": f"{snapshot.futures_realized_pnl_today:.2f}",
                "unrealized_pnl": f"{snapshot.futures_unrealized_pnl_today:.2f}",
                "losing_trades": str(snapshot.futures_losing_trades_today),
            },
        )
    return None


def overtrading(snapshot: StateSnapshot, cfg: FuturesRules) -> Trip | None:
    n = snapshot.futures_trade_count_today
    if n >= cfg.overtrading_hard:
        return Trip(
            rule_id="futures.overtrading",
            asset_class=AssetClass.FUTURE,
            severity=Severity.HARD,
            message=(
                f"{n} futures trades today (≥ {cfg.overtrading_hard}). This is churn — "
                f"Jun 10 was 79. Platform OFF."
            ),
            action=ActionType.PLATFORM_OFF_TODAY,
            context={"trades": str(n), "hard_limit": str(cfg.overtrading_hard)},
        )
    if n >= cfg.overtrading_warn:
        return Trip(
            rule_id="futures.overtrading",
            asset_class=AssetClass.FUTURE,
            severity=Severity.WARN,
            message=f"{n} futures trades today (≥ {cfg.overtrading_warn}). Slow down.",
            action=ActionType.ALERT_ONLY,
            context={"trades": str(n), "warn_limit": str(cfg.overtrading_warn)},
        )
    return None


def overnight_notional(snapshot: StateSnapshot, cfg: FuturesRules) -> Trip | None:
    if snapshot.minutes_to_futures_close is None:
        return None
    in_window = snapshot.minutes_to_futures_close <= cfg.close_window_min
    oversized = snapshot.futures_contracts_overnight > cfg.max_overnight_contracts
    if in_window and oversized:
        pct = snapshot.futures_notional / snapshot.nav if snapshot.nav > 0 else 0.0
        return Trip(
            rule_id="futures.overnight_notional",
            asset_class=AssetClass.FUTURE,
            severity=Severity.HARD,
            message=(
                f"{snapshot.futures_contracts_overnight:g} contracts heading overnight "
                f"(> {cfg.max_overnight_contracts:g} cap ≈ ⅓ NAV at live MNQ notional "
                f"~$61k/contract) — "
                f"≈{pct:.0%} of NAV in notional. Trim to "
                f"≤{cfg.max_overnight_contracts:g}?"
            ),
            action=ActionType.TRIM_FUTURES,
            context={
                "contracts": f"{snapshot.futures_contracts_overnight:g}",
                "notional_pct": f"{pct:.4f}",
            },
        )
    return None


def live_notional(snapshot: StateSnapshot, cfg: FuturesRules) -> Trip | None:
    if snapshot.nav <= 0:
        return None
    pct = snapshot.futures_notional / snapshot.nav
    if pct > cfg.max_notional_pct:
        return Trip(
            rule_id="futures.live_notional",
            asset_class=AssetClass.FUTURE,
            severity=Severity.WARN,
            message=(
                f"Futures notional ≈{pct:.0%} of NAV (> {cfg.max_notional_pct:.0%}). "
                f"Leverage is creeping up."
            ),
            action=ActionType.ALERT_ONLY,
            context={"notional_pct": f"{pct:.4f}"},
        )
    return None


def same_contract_churn(snapshot: StateSnapshot, cfg: FuturesRules) -> Trip | None:
    hot = {k: v for k, v in snapshot.contract_trade_counts_today.items()
           if v >= cfg.churn_count}
    if not hot:
        return None
    contract, count = max(hot.items(), key=lambda kv: kv[1])
    return Trip(
        rule_id="futures.same_contract_churn",
        asset_class=AssetClass.FUTURE,
        severity=Severity.WARN,
        message=(
            f"You've traded {contract} {count}× today (≥ {cfg.churn_count}). "
            f"You're scalping it."
        ),
        action=ActionType.ALERT_ONLY,
        context={"contract": contract, "count": str(count)},
    )
