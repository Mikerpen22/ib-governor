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
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from zoneinfo import ZoneInfo

from ..config import RulesConfig, load_config
from ..rules.engine import evaluate
from .snapshot import (
    _to_float,
    account_metrics,
    build_snapshot,
    contract_symbol,
    is_sec_type,
)

ET = ZoneInfo("America/New_York")

# IBKR leaves realizedPNL at this sentinel when it has not been computed yet.
_PNL_SENTINEL_THRESHOLD = 1e12


def _is_sentinel_pnl(pnl: float) -> bool:
    return abs(pnl) >= _PNL_SENTINEL_THRESHOLD


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

    return {
        "date": today.isoformat(),
        "nav": nav,
        "margin_cushion": margin_cushion,
        "gross_leverage": gross_leverage,
        "realized_pnl_today": realized_pnl_today,
        "fills": fills,
        "positions": positions,
        "trips": trips,
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
    conn = BrakeConnection(config.live)
    conn.connect()
    try:
        now = dt.datetime.now(tz=ET)
        data = collect_day_data(conn.ib, config, now)
        print(json.dumps(data, indent=2))
    finally:
        conn.disconnect()


if __name__ == "__main__":  # pragma: no cover
    main()
