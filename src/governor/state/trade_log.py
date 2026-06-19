"""Rolling per-symbol trade history (file-backed) for weekly-churn detection.
Stores [order_id, iso_timestamp] pairs per symbol; prunes entries older than the
retention window on each record(). Counting is by distinct order_id so partial
fills (multiple fills for one order) count as a single trade."""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

from .json_store import StateFileError, load_json, save_json

log = logging.getLogger("governor.state")

_RETENTION_DAYS = 14   # keep a little more than the 7-day query window


class WeeklyTradeLog:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def _load(self) -> dict[str, list[list[str]]]:
        # Feeds only the WARN churn rule -> a bad file self-heals to empty counts
        # (loudly), never crashes the build/evaluate path. load_json now guarantees
        # a dict or raises, so the old isinstance guard is unnecessary.
        try:
            return load_json(self._path, {})
        except StateFileError as exc:
            log.warning("trade-log state unreadable (%s) — starting from empty counts", exc)
            return {}

    def record(self, symbol: str, order_id: str | int, when: dt.datetime) -> None:
        data = self._load()
        entries: list[list[str]] = data.get(symbol, [])
        entries.append([str(order_id), when.isoformat()])
        cutoff = when - dt.timedelta(days=_RETENTION_DAYS)
        data[symbol] = [
            e for e in entries if dt.datetime.fromisoformat(e[1]) >= cutoff
        ]
        save_json(self._path, data)

    def count(self, symbol: str, now: dt.datetime, days: int = 7) -> int:
        cutoff = now - dt.timedelta(days=days)
        seen: set[str] = set()
        for e in self._load().get(symbol, []):
            oid, ts = e[0], e[1]
            if dt.datetime.fromisoformat(ts) >= cutoff:
                seen.add(oid)
        return len(seen)

    def counts_within(self, now: dt.datetime, days: int = 7) -> dict[str, int]:
        """Return per-symbol trade counts for the past *days* days (single disk read)."""
        cutoff = now - dt.timedelta(days=days)
        result: dict[str, int] = {}
        for sym, entries in self._load().items():
            seen: set[str] = set()
            for e in entries:
                if dt.datetime.fromisoformat(e[1]) >= cutoff:
                    seen.add(e[0])
            if seen:
                result[sym] = len(seen)
        return result
