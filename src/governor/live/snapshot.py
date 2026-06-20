"""Pure transforms from fetched IBKR objects to StateSnapshot fields.

Everything here is a pure function of its inputs — no IB connection, no I/O —
so the risky derivation logic (futures P&L from fills, notional, minutes-to-close)
is unit-testable with fakes, exactly like Plan 1's rules.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
from collections import Counter
from zoneinfo import ZoneInfo

from ..config import LiveConfig
from ..model import StateSnapshot

ET = ZoneInfo("America/New_York")

log = logging.getLogger("governor.live.snapshot")

# IBKR leaves numeric fields (e.g. realizedPNL) at this UNSET sentinel when they
# have not been computed yet (≈1.79e308). Treat anything at/above _PNL_SENTINEL,
# or non-finite (nan/inf), as "no value" rather than a real number.
_PNL_SENTINEL = 1e12


def _to_float(s: object) -> float:
    try:
        return float(s)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _finite_float(x: object, default: float = 0.0) -> float:
    """Like _to_float, but maps non-finite (nan/inf) and the IBKR UNSET sentinel
    (abs >= _PNL_SENTINEL) to *default*, so they cannot leak into the rules as
    real numbers (a phantom NAV, a phantom realized loss, etc.)."""
    v = _to_float(x)
    if not math.isfinite(v) or abs(v) >= _PNL_SENTINEL:
        return default
    return v


# ── shared predicates ─────────────────────────────────────────────────────────

def is_sec_type(item, sec_type: str) -> bool:
    """True if *item* has a .contract.secType matching *sec_type*."""
    return getattr(getattr(item, "contract", None), "secType", None) == sec_type


def contract_symbol(contract) -> str | None:
    """Return the display symbol from a contract, trying .symbol then .localSymbol."""
    return getattr(contract, "symbol", None) or getattr(contract, "localSymbol", None)


# ── metric helpers ─────────────────────────────────────────────────────────────

def account_metrics(account_values) -> tuple[float, float, float]:
    """Return (nav, margin_cushion, gross_leverage). Missing tags -> 0; nav<=0 -> safe zeros.

    This account is multi-currency, so resolve each tag from the BASE/consolidated
    row (IBKR's whole-account summary line) when present — USD-only understates NAV
    and skews every %-of-NAV rule. Fall back to USD, then to any row.
    """
    def pick(tag):
        rows = [av for av in account_values if av.tag == tag]
        for cur in ("BASE", "USD"):
            for av in rows:
                if av.currency == cur:
                    return _finite_float(av.value)
        return _finite_float(rows[0].value) if rows else 0.0

    nav = pick("NetLiquidation")
    if nav <= 0:
        return 0.0, 0.0, 0.0
    return nav, pick("ExcessLiquidity") / nav, pick("GrossPositionValue") / nav


def futures_exposure(portfolio_items, mnq_notional_usd: float) -> tuple[float, float]:
    """Return (futures_notional_usd, mnq_equivalent_contracts).

    Notional = sum over FUT positions of |position| * multiplier * marketPrice.
    MNQ-equivalent = total futures notional / a reference MNQ contract notional, so the
    overnight rule can reason in "MNQ contracts" regardless of which future is held.
    """
    notional = 0.0
    for it in portfolio_items:
        if not is_sec_type(it, "FUT"):
            continue
        mult = _to_float(getattr(it.contract, "multiplier", "1")) or 1.0
        px = _to_float(getattr(it, "marketPrice", 0.0))
        # Finite-guard the price: a nan/inf or ≤0 mark would silently zero (or
        # NaN-poison) the futures-notional rules. Skip + warn so it fails loud.
        if not math.isfinite(px) or px <= 0:
            log.warning(
                "skipping FUT %s: non-finite/≤0 marketPrice %r (futures notional may be understated)",
                getattr(it.contract, "localSymbol", "?"), px,
            )
            continue
        notional += abs(it.position) * mult * px
    contracts = notional / mnq_notional_usd if mnq_notional_usd > 0 else 0.0
    return notional, contracts


def futures_activity(fills) -> tuple[float, int, int, dict[str, int]]:
    """Return (realized_pnl_today, trade_count, losing_trades, per_contract_counts) for FUT only.

    - realized_pnl_today: sum of commissionReport.realizedPNL over FUT fills (current session).
    - trade_count: distinct orderIds (a multi-fill order is one trade), the overtrading proxy.
    - losing_trades: count of FUT fills with negative realized P&L.
    - per_contract_counts: FUT fill counts keyed by contract localSymbol (churn detection).
    """
    realized = 0.0
    losers = 0
    order_ids: set = set()
    counts: Counter = Counter()
    for f in fills:
        if not is_sec_type(f, "FUT"):
            continue
        pnl = _to_float(getattr(f.commissionReport, "realizedPNL", 0.0))
        # IBKR leaves realizedPNL at the UNSET sentinel (≈1.79e308) until it is
        # computed. Skip it: don't sum (would dwarf NAV) and don't count as a
        # loser/winner — a not-yet-computed P&L must not fire a phantom lockout.
        # (Mirrors what daily.py already does for the same field.)
        if not math.isfinite(pnl) or abs(pnl) >= _PNL_SENTINEL:
            continue
        realized += pnl
        if pnl < 0:
            losers += 1
        order_ids.add(f.execution.orderId)
        counts[f.contract.localSymbol] += 1
    return realized, len(order_ids), losers, dict(counts)


def minutes_to_close(now: dt.datetime, session_close_et: str) -> float | None:
    """Minutes from `now` until today's session close (ET). None if already past close.

    A naive `now` is interpreted as ET. The overnight rule only acts inside its close
    window, so 'past close' correctly yields None (don't nag overnight)."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=ET)
    now_et = now.astimezone(ET)
    hh, mm = (int(x) for x in session_close_et.split(":"))
    close_et = now_et.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if now_et > close_et:
        return None
    return (close_et - now_et).total_seconds() / 60.0


def equity_adds_at_loss(fills_today, portfolio_items) -> tuple[str, ...]:
    """Return a tuple of STK symbols that were bought today AND are currently at unrealized loss.

    Pure: given today's fills + current portfolio snapshot values.

    A symbol qualifies if:
      1. There is at least one STK fill with a buy execution today (side == "BOT").
      2. The current portfolio item for that symbol has unrealizedPNL < 0.

    Fills without a positive position (sells/shorts) do not qualify.
    """
    # Collect STK symbols that had buy fills today.
    bought_today: set[str] = set()
    for f in fills_today:
        if not is_sec_type(f, "STK"):
            continue
        exec_ = getattr(f, "execution", None)
        if exec_ is None:
            continue
        # IB side values: "BOT" for buy, "SLD" for sell; always present on real Executions.
        side = getattr(exec_, "side", None)
        if side is not None and str(side).upper().startswith("B"):
            sym = contract_symbol(f.contract)
            if sym:
                bought_today.add(sym)

    if not bought_today:
        return ()

    # Cross-reference with current portfolio for unrealized loss.
    at_loss: list[str] = []
    for it in portfolio_items:
        if not is_sec_type(it, "STK"):
            continue
        sym = contract_symbol(it.contract)
        if sym in bought_today:
            # Finite-guard unrealizedPNL: a nan compares False to < 0, so an
            # at-loss add could be silently hidden. _finite_float maps it to 0.0.
            upnl = _finite_float(getattr(it, "unrealizedPNL", 0.0))
            if upnl < 0:
                at_loss.append(sym)

    return tuple(sorted(at_loss))


def equity_weights(
    portfolio_items,
    nav: float,
    sector_by_symbol: dict[str, str],
) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    """Return (name_weights, sector_weights, name_exposure_signed) as fractions of NAV,
    for STK positions only.

    - name_weights / sector_weights carry the ABSOLUTE magnitude (a short adds to
      concentration, not cancels it).
    - name_exposure_signed carries the SIGNED exposure (+ net long, − net short) so the
      pre-trade gate's hypothetical snapshot can tell covering-a-long from opening-a-short
      (audit H1). A short position has a negative marketValue, so do NOT abs it here.

    A symbol with no known sector goes into the 'unknown' bucket so concentration
    there still surfaces (fail-loud, not silent pass).
    """
    name_w: dict[str, float] = {}
    sector_w: dict[str, float] = {}
    name_signed: dict[str, float] = {}
    if nav <= 0:
        return name_w, sector_w, name_signed
    for it in portfolio_items:
        if not is_sec_type(it, "STK"):
            continue
        sym = getattr(it.contract, "symbol", None)
        if sym is None:
            continue
        # Finite-guard marketValue: a nan would silently drop the name from the
        # concentration rules (and NaN-poison its sector bucket).
        mv = _finite_float(getattr(it, "marketValue", 0.0)) / nav
        name_signed[sym] = name_signed.get(sym, 0.0) + mv  # SIGNED — short stays negative
        w = abs(mv)
        name_w[sym] = name_w.get(sym, 0.0) + w
        sector = sector_by_symbol.get(sym) or "unknown"
        sector_w[sector] = sector_w.get(sector, 0.0) + w
    return name_w, sector_w, name_signed


def build_snapshot(
    *,
    now: dt.datetime,
    account_values,
    portfolio_items,
    fills,
    cfg: LiveConfig,
    sector_by_symbol: dict[str, str] | None = None,
    name_trade_counts_week: dict[str, int] | None = None,
    hwm_drawdown_pct: float = 0.0,
    equity_adds_at_loss_today: tuple[str, ...] = (),
    precomputed_account_metrics: tuple[float, float, float] | None = None,
    mnq_notional_usd: float | None = None,
) -> StateSnapshot:
    """Compose the pure transforms into a StateSnapshot. Pure: deterministic in its inputs.

    The new keyword params (sector_by_symbol, name_trade_counts_week, hwm_drawdown_pct,
    equity_adds_at_loss_today) are all defaulted so existing Plan-2 callers/tests continue
    to work unchanged.  Pass precomputed_account_metrics=(nav,cushion,gross_leverage) to
    skip the redundant recompute when the caller already has those values.

    Pass mnq_notional_usd to override cfg.mnq_notional_usd for futures normalization
    (e.g. a live-fetched MNQ front-month price × multiplier).  None (the default) falls
    back to cfg.mnq_notional_usd, keeping all existing callers unaffected.
    """
    nav, cushion, gross_leverage = (
        precomputed_account_metrics if precomputed_account_metrics is not None
        else account_metrics(account_values)
    )
    mnq = mnq_notional_usd if mnq_notional_usd is not None else cfg.mnq_notional_usd
    fut_notional, contracts_overnight = futures_exposure(portfolio_items, mnq)
    realized, trades, losers, counts = futures_activity(fills)
    mins = minutes_to_close(now, cfg.session_close_et)
    name_w, sector_w, name_signed = equity_weights(portfolio_items, nav, sector_by_symbol or {})

    # Signed futures notional (+ net long, − net short) for hypothetical-exposure
    # reasoning, and mark-to-market open futures P&L. Same finite-guard on price
    # as futures_exposure (#4) so a nan/≤0 mark can't poison the figures.
    fut_notional_signed = 0.0
    fut_unrealized = 0.0
    for it in portfolio_items:
        if not is_sec_type(it, "FUT"):
            continue
        fut_unrealized += _finite_float(getattr(it, "unrealizedPNL", 0.0))
        mult = _to_float(getattr(it.contract, "multiplier", "1")) or 1.0
        px = _to_float(getattr(it, "marketPrice", 0.0))
        if not math.isfinite(px) or px <= 0:
            continue  # already warned in futures_exposure
        fut_notional_signed += _to_float(getattr(it, "position", 0.0)) * mult * px

    return StateSnapshot(
        ts=now.isoformat(),
        nav=nav,
        margin_cushion=cushion,
        gross_leverage=gross_leverage,
        futures_realized_pnl_today=realized,
        futures_trade_count_today=trades,
        futures_losing_trades_today=losers,
        futures_notional=fut_notional,
        futures_notional_signed=fut_notional_signed,
        futures_unrealized_pnl_today=fut_unrealized,
        futures_contracts_overnight=contracts_overnight,
        minutes_to_futures_close=mins,
        contract_trade_counts_today=counts,
        drawdown_pct=hwm_drawdown_pct,
        sector_weights=sector_w,
        name_weights=name_w,
        name_exposure_signed=name_signed,
        name_trade_counts_week=name_trade_counts_week or {},
        equity_adds_at_loss_today=equity_adds_at_loss_today,
    )
