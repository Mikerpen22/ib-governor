# src/governor/live/history.py
"""Fail-soft daily-bar fetch for the candidate symbol, on the gate's existing socket.

Mirrors live/daily.py::_fetch_daily_bars: reqHistoricalData (EOD bars are broadly
entitled even without live market data) wrapped so ANY error yields None — the setup
read degrades to 'unavailable', never sinking the risk gate.
"""
from __future__ import annotations

import logging

from governor.technicals.types import Bar

log = logging.getLogger("governor.live.history")


def fetch_daily_bars(ib, contract, duration: str, *, what_to_show: str = "TRADES",
                     use_rth: bool = True) -> list[Bar] | None:
    try:
        raw = ib.reqHistoricalData(
            contract, endDateTime="", durationStr=duration,
            barSizeSetting="1 day", whatToShow=what_to_show, useRTH=use_rth,
        ) or []
    except Exception:  # noqa: BLE001 — setup is best-effort; a fetch failure must not sink the gate
        log.warning("setup: historical bars unavailable for %r", contract, exc_info=True)
        return None
    bars: list[Bar] = []
    for b in raw:
        try:
            bars.append(Bar(
                date=str(getattr(b, "date", "")),
                open=float(b.open), high=float(b.high), low=float(b.low),
                close=float(b.close), volume=float(getattr(b, "volume", 0.0) or 0.0),
            ))
        except (TypeError, ValueError):
            continue
    return bars or None
