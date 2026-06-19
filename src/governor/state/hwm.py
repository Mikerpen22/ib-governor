"""Persistent NAV high-water-mark for drawdown computation."""
from __future__ import annotations

import logging
from pathlib import Path

from .json_store import StateFileError, load_json, save_json

log = logging.getLogger("governor.state")


class HwmStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def peak(self) -> float:
        # HWM feeds only WARN-severity drawdown rules, so a bad file self-heals
        # (resets from the next NAV via update()) — but LOUDLY, never silently.
        try:
            data = load_json(self._path, {})
            return float(data["peak"]) if data else 0.0
        except StateFileError as exc:
            log.warning("HWM state unreadable (%s) — resetting from current NAV", exc)
            return 0.0
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("HWM state malformed (%s) — resetting from current NAV", exc)
            return 0.0

    def update(self, nav: float) -> tuple[float, float]:
        """Persist the new peak and return ``(peak, drawdown_pct)`` in one pass."""
        peak = max(self.peak(), nav)
        save_json(self._path, {"peak": peak})
        drawdown = 0.0 if peak <= 0 or nav >= peak else (peak - nav) / peak
        return peak, drawdown

    def drawdown_pct(self, nav: float) -> float:
        peak = self.peak()
        if peak <= 0 or nav >= peak:
            return 0.0
        return (peak - nav) / peak
