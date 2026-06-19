"""Read-only live verification of the IBKR data contract the snapshot builder assumes.

Run manually with TWS running:  .venv/bin/python scripts/pin_contract.py
Verifies: account tags present, FUT positions carry secType+multiplier, fills expose
realizedPNL/secType. Prints a PASS/FAIL contract report. Places NO orders."""
from __future__ import annotations

import sys

from governor.config import load_config
from governor.live.connection import BrakeConnection
from governor.live.snapshot import build_snapshot
import datetime as dt
from zoneinfo import ZoneInfo

REQUIRED_TAGS = {"NetLiquidation", "ExcessLiquidity", "GrossPositionValue"}


def main() -> int:
    cfg = load_config("config/rules.yaml")
    conn = BrakeConnection(cfg.live)
    try:
        conn.connect()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: could not connect to TWS ({exc}). Is TWS running with API enabled?")
        return 2

    ib = conn.ib
    ok = True

    tags = {av.tag for av in ib.accountValues()}
    missing = REQUIRED_TAGS - tags
    print(f"account tags: {'PASS' if not missing else f'FAIL missing {missing}'}")
    ok &= not missing

    futs = [p for p in ib.portfolio() if p.contract.secType == "FUT"]
    if futs:
        sample = futs[0]
        has_mult = bool(getattr(sample.contract, "multiplier", ""))
        print(f"FUT positions: {len(futs)} found; multiplier present: "
              f"{'PASS' if has_mult else 'FAIL'} (e.g. {sample.contract.localSymbol} "
              f"mult={sample.contract.multiplier!r} px={sample.marketPrice})")
        ok &= has_mult
    else:
        print("FUT positions: none held right now (cannot verify multiplier live)")

    fills = ib.fills()
    fut_fills = [f for f in fills if f.contract.secType == "FUT"]
    print(f"fills this session: {len(fills)} ({len(fut_fills)} FUT)")
    if fut_fills:
        f = fut_fills[0]
        has_pnl = hasattr(f.commissionReport, "realizedPNL")
        print(f"  FUT fill realizedPNL present: {'PASS' if has_pnl else 'FAIL'} "
              f"(realizedPNL={getattr(f.commissionReport, 'realizedPNL', None)})")
        ok &= has_pnl

    snap = build_snapshot(now=dt.datetime.now(tz=ZoneInfo("America/New_York")),
                          account_values=ib.accountValues(),
                          portfolio_items=ib.portfolio(),
                          fills=ib.fills(), cfg=cfg.live)
    print(f"build_snapshot OK: nav={snap.nav:.0f} cushion={snap.margin_cushion:.0%} "
          f"fut_notional={snap.futures_notional:.0f} fut_trades={snap.futures_trade_count_today}")

    conn.disconnect()
    print("\nCONTRACT PIN:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
