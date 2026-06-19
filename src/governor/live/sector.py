"""Resolve a stock's sector via reqContractDetails().industry.

Sector is static per ticker so we cache aggressively. Supports a file-backed
persistent cache (``cache_path``) so the expensive ``reqContractDetails`` round-
trip is only paid once across process restarts.  Pass ``cache_path=None`` for
in-memory-only mode (used in tests).

Unknown/empty -> None (fail-safe: don't block on missing sector data).
On-disk shape: ``{symbol: sector_or_null}`` — JSON null ↔ Python None.
A resolved-to-unknown symbol is cached as null so we don't re-hit the API
for known-unknowns.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ib_async import Stock

from ..state.json_store import StateFileError, load_json, save_json

log = logging.getLogger("governor.sector")

_DEFAULT_CACHE_PATH = "config/sector_cache.json"


class SectorResolver:
    def __init__(self, ib, cache_path: str | Path | None = _DEFAULT_CACHE_PATH) -> None:
        self._ib = ib
        self._cache_path: Path | None = Path(cache_path) if cache_path is not None else None
        self._cache: dict[str, str | None] = {}

        if self._cache_path is not None:
            try:
                loaded = load_json(self._cache_path, {})
                # Normalise: JSON null arrives as Python None, which is correct.
                self._cache = {k: (v if v else None) for k, v in loaded.items()}
            except StateFileError as exc:
                log.warning(
                    "sector cache at %s is corrupt — starting empty (%s)",
                    self._cache_path,
                    exc,
                )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _save(self) -> None:
        """Persist the current cache dict to disk (no-op when cache_path is None)."""
        if self._cache_path is None:
            return
        try:
            save_json(self._cache_path, self._cache)
        except Exception as exc:  # noqa: BLE001 — sector cache is advisory
            log.warning("could not persist sector cache to %s: %s", self._cache_path, exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self, symbol: str) -> str | None:
        """Return the sector for a symbol, or None if unknown/lookup failed.

        Hits the in-memory cache first; on miss calls ``reqContractDetails``
        then persists the result (including None for known-unknowns) so the
        next process skips the API call.
        """
        if symbol in self._cache:
            return self._cache[symbol]

        sector: str | None = None
        try:
            details = self._ib.reqContractDetails(Stock(symbol, "SMART", "USD"))
            if details:
                raw = getattr(details[0], "industry", None)
                sector = raw if raw else None
        except Exception as exc:  # noqa: BLE001 — fail safe; don't block on sector errors
            log.error("sector lookup failed for %s: %s", symbol, exc)

        self._cache[symbol] = sector
        self._save()
        return sector

    def map_for(self, symbols) -> dict[str, str]:
        """Return {symbol: sector} for symbols where sector is known (None entries omitted).

        Resolves all symbols first, then persists once at the end if the
        cache grew — avoids one write per symbol during batch resolution.
        """
        size_before = len(self._cache)

        result: dict[str, str] = {}
        for s in symbols:
            # Resolve without persisting per-call: temporarily suppress _save
            # by resolving through the core logic directly.
            if s not in self._cache:
                sector: str | None = None
                try:
                    details = self._ib.reqContractDetails(Stock(s, "SMART", "USD"))
                    if details:
                        raw = getattr(details[0], "industry", None)
                        sector = raw if raw else None
                except Exception as exc:  # noqa: BLE001
                    log.error("sector lookup failed for %s: %s", s, exc)
                self._cache[s] = sector

            sec = self._cache[s]
            if sec is not None:
                result[s] = sec

        if len(self._cache) > size_before:
            self._save()

        return result
