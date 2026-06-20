# src/governor/live/history.py
"""Fail-soft daily-bar fetch for the candidate symbol, on the gate's existing socket.

_request_daily_bars is the shared network layer (also used by daily.py); it
returns a raw bar list on success or None on any error.  fetch_daily_bars wraps
it with Bar conversion — the setup read degrades to 'unavailable', never sinking
the risk gate.
"""
from __future__ import annotations

import logging

from governor.technicals.types import Bar

log = logging.getLogger("governor.live.history")


def _request_daily_bars(ib, contract, duration, *, what_to_show="TRADES", use_rth=True):
    """Fail-soft reqHistoricalData → raw bar list ([] on empty), or None on error."""
    try:
        return ib.reqHistoricalData(contract, endDateTime="", durationStr=duration,
            barSizeSetting="1 day", whatToShow=what_to_show, useRTH=use_rth) or []
    except Exception:  # noqa: BLE001 — best-effort; never sink the caller
        log.warning("daily bars unavailable for %r", contract, exc_info=True)
        return None


def fetch_daily_bars(ib, contract, duration: str, *, what_to_show: str = "TRADES",
                     use_rth: bool = True) -> list[Bar] | None:
    raw = _request_daily_bars(ib, contract, duration, what_to_show=what_to_show, use_rth=use_rth)
    if raw is None:
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
