"""Read-only daily trade-data collector for the daily-summary skill.

collect_day_data() is the single public entry point. It is designed to be
testable with a fake IB (duck-typed SimpleNamespace) — no real IBKR connection
is imported here.

The result is a plain dict of JSON-serializable primitives:
  date              — ISO date string for `now`
  nav               — Net Liquidation Value (USD)
  margin_cushion    — ExcessLiquidity / NAV
  gross_leverage    — GrossPositionValue / NAV
  realized_pnl_today — sum of commissionReport.realizedPNL for today's fills,
                        excluding the IBKR unset sentinel (abs >= 1e12)
  fills             — list of dicts {symbol, sec_type, side, shares, price,
                       realized_pnl, time} for fills whose execution.time date
                       matches now's date (ET)
  positions         — list of dicts {symbol, sec_type, position, market_value,
                       unrealized_pnl} from ib.portfolio()
  trips             — list of dicts {rule_id, severity, message} from the rule
                       engine, built on the current snapshot
  indices           — {SYM: {label, last, change_pct} | None} broad-market move
                       for SPY/QQQ/DIA/IWM (best-effort; None per missing feed)
  vix               — {level, change_pct, elevated, signal} | None — the VIX
                       level; `elevated` is level > VIX_ELEVATED_THRESHOLD (20),
                       a contrarian-long signal. None if unavailable.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
from zoneinfo import ZoneInfo

from ib_async import Index, Stock

from ..config import RulesConfig, load_config
from ..rules.engine import evaluate
from .history import _request_daily_bars
from .snapshot import (
    _to_float,
    account_metrics,
    build_snapshot,
    contract_symbol,
    is_sec_type,
)

ET = ZoneInfo("America/New_York")

log = logging.getLogger("governor.live.daily")

# IBKR leaves realizedPNL at this sentinel when it has not been computed yet.
_PNL_SENTINEL_THRESHOLD = 1e12

# The user's "elevated fear" line in the VIX. Historically, VIX > 20 marks a
# regime where contrarian longs have paid off (fear is overpriced). It's the
# operator's threshold — tunable; this is a signal, not financial advice.
VIX_ELEVATED_THRESHOLD = 20.0

# The broad-market index proxies (liquid ETFs — historical bars are broadly
# available without a live market-data subscription). Ordered SPY · QQQ · DIA · IWM.
_INDEX_PROXIES: tuple[tuple[str, str], ...] = (
    ("SPY", "S&P 500"),
    ("QQQ", "Nasdaq 100"),
    ("DIA", "Dow"),
    ("IWM", "Russell 2000"),
)


def _is_sentinel_pnl(pnl: float) -> bool:
    return abs(pnl) >= _PNL_SENTINEL_THRESHOLD


def _last_two_closes(bars) -> tuple[float, float] | None:
    """Return (prev_close, latest_close) from a daily-bar series, or None.

    Needs at least two bars to compute a day-over-day change. A missing/empty/
    single-bar series → None (the caller renders that feed as unavailable).
    """
    if not bars or len(bars) < 2:
        return None
    prev_close = _to_float(getattr(bars[-2], "close", None))
    latest_close = _to_float(getattr(bars[-1], "close", None))
    if prev_close <= 0:
        return None
    return prev_close, latest_close


def _fetch_daily_bars(ib, contract):
    """Fetch ~2 daily TRADES bars for a contract. FAIL-SOFT: returns [] on any error."""
    return _request_daily_bars(ib, contract, "2 D") or []


def collect_market_backdrop(ib) -> dict:
    """Best-effort broad-market backdrop: index moves + the VIX level.

    Returns ``{"indices": {SYM: {label, last, change_pct} | None}, "vix": {...} | None}``.
    Every fetch is wrapped so one missing/erroring feed cannot break the rest, and
    a wholly market-data-less ``ib`` yields all-None (never raises). Read-only.
    """
    indices: dict[str, dict | None] = {}
    for sym, label in _INDEX_PROXIES:
        entry: dict | None = None
        try:
            bars = _fetch_daily_bars(ib, Stock(sym, "SMART", "USD"))
            closes = _last_two_closes(bars)
            if closes is not None:
                prev_close, latest_close = closes
                entry = {
                    "label": label,
                    "last": latest_close,
                    "change_pct": (latest_close - prev_close) / prev_close * 100,
                }
        except Exception:  # noqa: BLE001 — one symbol must not sink the backdrop
            log.warning("market backdrop: index %s failed", sym, exc_info=True)
            entry = None
        indices[sym] = entry

    vix: dict | None = None
    try:
        bars = _fetch_daily_bars(ib, Index("VIX", "CBOE"))
        closes = _last_two_closes(bars)
        if closes is not None:
            prev_close, latest_close = closes
            elevated = latest_close > VIX_ELEVATED_THRESHOLD
            vix = {
                "level": latest_close,
                "change_pct": (latest_close - prev_close) / prev_close * 100,
                "elevated": elevated,
                "signal": "elevated fear — contrarian long" if elevated else "calm",
            }
    except Exception:  # noqa: BLE001 — VIX (entitlement) is optional; fail soft
        log.warning("market backdrop: VIX unavailable", exc_info=True)
        vix = None

    return {"indices": indices, "vix": vix}


def _fill_date_et(fill) -> dt.date:
    """Return the date (ET) of a fill's execution.time."""
    t = fill.execution.time
    if isinstance(t, dt.datetime):
        if t.tzinfo is None:
            t = t.replace(tzinfo=ET)
        return t.astimezone(ET).date()
    # If it's already a date (edge case in fakes), return it directly
    if isinstance(t, dt.date):
        return t
    return dt.date.min


def collect_day_data(ib, config: RulesConfig, now: dt.datetime) -> dict:
    """Read-only snapshot of the trading day for the summary skill.

    All returned values are JSON-serializable (no datetimes — use ISO strings).
    """
    today = (
        now.astimezone(ET).date() if now.tzinfo else now.replace(tzinfo=ET).astimezone(ET).date()
    )

    account_values = ib.accountValues()
    portfolio_items = ib.portfolio()
    all_fills = ib.fills()

    # --- Account metrics ---------------------------------------------------
    nav, margin_cushion, gross_leverage = account_metrics(account_values)

    # --- Today's fills only -----------------------------------------------
    today_fills = [f for f in all_fills if _fill_date_et(f) == today]

    # --- Realized P&L today (sentinel-filtered) ----------------------------
    realized_pnl_today = sum(
        _to_float(getattr(f.commissionReport, "realizedPNL", 0.0))
        for f in today_fills
        if not _is_sentinel_pnl(_to_float(getattr(f.commissionReport, "realizedPNL", 0.0)))
    )

    # --- Fills list --------------------------------------------------------
    fills: list[dict] = []
    for f in today_fills:
        pnl = _to_float(getattr(f.commissionReport, "realizedPNL", 0.0))
        ex = f.execution
        sym = contract_symbol(f.contract) or ""
        sec = getattr(f.contract, "secType", "")
        side = getattr(ex, "side", "")
        shares = _to_float(getattr(ex, "shares", 0.0))
        price = _to_float(getattr(ex, "avgPrice", None) or getattr(ex, "price", 0.0))
        exec_time = getattr(ex, "time", None)
        time_str = exec_time.isoformat() if isinstance(exec_time, (dt.datetime, dt.date)) else str(exec_time or "")
        fills.append({
            "symbol": sym,
            "sec_type": sec,
            "side": side,
            "shares": shares,
            "price": price,
            "realized_pnl": pnl if not _is_sentinel_pnl(pnl) else 0.0,
            "time": time_str,
        })

    # --- Positions ---------------------------------------------------------
    positions: list[dict] = []
    for it in portfolio_items:
        sym = contract_symbol(it.contract) or getattr(it.contract, "symbol", "")
        sec = getattr(it.contract, "secType", "")
        pos = _to_float(getattr(it, "position", 0.0))
        mv = _to_float(getattr(it, "marketValue", 0.0))
        upnl = _to_float(getattr(it, "unrealizedPNL", 0.0))
        positions.append({
            "symbol": sym,
            "sec_type": sec,
            "position": pos,
            "market_value": mv,
            "unrealized_pnl": upnl,
        })

    # --- Rule trips (read-only, sector={}) ---------------------------------
    snapshot = build_snapshot(
        now=now,
        account_values=account_values,
        portfolio_items=portfolio_items,
        fills=all_fills,
        cfg=config.live,
        sector_by_symbol={},
    )
    raw_trips = evaluate(snapshot, config)
    trips: list[dict] = [
        {
            "rule_id": t.rule_id,
            "severity": t.severity.value if hasattr(t.severity, "value") else str(t.severity),
            "message": t.message,
        }
        for t in raw_trips
    ]

    # --- Broad-market backdrop (best-effort; never breaks the collector) ---
    backdrop = collect_market_backdrop(ib)

    return {
        "date": today.isoformat(),
        "nav": nav,
        "margin_cushion": margin_cushion,
        "gross_leverage": gross_leverage,
        "realized_pnl_today": realized_pnl_today,
        "fills": fills,
        "positions": positions,
        "trips": trips,
        "indices": backdrop["indices"],
        "vix": backdrop["vix"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect today's trade data and print as JSON.",
        prog="python -m governor.live.daily",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        default=True,
        help="Output as JSON (default: on)",
    )
    parser.add_argument(
        "--config",
        default="config/rules.yaml",
        help="Path to rules config YAML",
    )
    args = parser.parse_args()

    from ..config import load_config, load_env_file
    from .connection import BrakeConnection

    load_env_file()
    config = load_config(args.config)
    # Use the daily collector's own client id (distinct from daemon=4 / gate=5) so the
    # summary can read while the always-on daemon holds client_id 4.
    daily_live = config.live.model_copy(update={"client_id": config.live.daily_client_id})
    conn = BrakeConnection(daily_live)
    conn.connect()
    try:
        now = dt.datetime.now(tz=ET)
        data = collect_day_data(conn.ib, config, now)
        print(json.dumps(data, indent=2))
    finally:
        conn.disconnect()


if __name__ == "__main__":  # pragma: no cover
    main()
