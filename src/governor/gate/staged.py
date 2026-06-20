"""File-backed, single-use, TTL-bounded staged-order store.

The gate's `analyze` step (one process) stages an order + confirmation token;
the `submit` step (a separate, later process) consumes it. Persistence across
process boundaries is mandatory, so every write goes to disk via json_store.

On-disk shape:
    { "<TOKEN>": {"intent": {...}, "expires": "<iso8601-utc>"} }

Safety invariants:
- single-use: `consume` removes the entry atomically (load → pop → save).
- TTL: entries whose `expires` timestamp is <= `now` are treated as expired
  and pruned on the next stage or consume call.
- durable write: `save_json(..., durable=True)` fsyncs before returning, so
  the staged entry survives a crash immediately after staging.
- corrupt file: `load_json` raises `StateFileError` — we let it propagate
  (fail loud). A corrupt staged-order file must NOT silently lose a pending
  order; the caller must investigate.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from ..state.json_store import load_json, save_json

# Repo-anchored default path for the staged-order file — the single source of
# truth shared by the gate (analyze stages, submit consumes) and the daemon
# (cancel consumes), so they never diverge on which file they touch regardless
# of the process's working directory. src/governor/gate/staged.py -> repo root.
DEFAULT_STAGED_PATH = Path(__file__).resolve().parents[3] / "config" / "staged_orders.json"


class StagedOrderStore:
    def __init__(
        self,
        path: str | Path,
        ttl_seconds: float = 300.0,
        token_factory: Callable[[], str] = lambda: secrets.token_hex(8).upper(),
    ) -> None:
        self._path = Path(path)
        self._ttl = ttl_seconds
        self._make_token = token_factory

    # ── internal helpers ───────────────────────────────────────────────────────

    def _load(self) -> dict:
        """Load the on-disk dict. Absent file -> {}. Corrupt file -> StateFileError."""
        return load_json(self._path, {})

    def _prune(self, data: dict, now: datetime) -> dict:
        """Return a new dict with all expired entries removed (immutable pattern)."""
        return {
            token: entry
            for token, entry in data.items()
            if datetime.fromisoformat(entry["expires"]) > now
        }

    def _save(self, data: dict) -> None:
        """Durable write — fsyncs before returning (safety-critical state)."""
        save_json(self._path, data, durable=True)

    # ── public API ─────────────────────────────────────────────────────────────

    def stage(self, intent: dict, now: datetime, verdict: str | None = None) -> str:
        """Persist *intent* (and its gate *verdict*) under a fresh token; return it.

        `verdict` is the gate verdict at stage time (e.g. "GO"/"CAUTION"/"BLOCK").
        It is persisted so a later, separate `consume`/`submit` process can refuse
        to place a BLOCK-staged order without an explicit override — the safety
        guarantee does not depend on the caller remembering the verdict.

        Prunes already-expired entries while writing.
        The write is durable (fsync) because this is safety-critical state.
        """
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware (e.g. datetime.now(timezone.utc))")
        data = self._load()
        data = self._prune(data, now)
        token = self._make_token()
        while token in data:
            token = self._make_token()
        expires = now + timedelta(seconds=self._ttl)
        data = {
            **data,
            token: {
                "intent": intent,
                "verdict": verdict,
                "expires": expires.isoformat(),
            },
        }
        self._save(data)
        return token

    def consume(self, token: str, now: datetime) -> dict | None:
        """Return the staged record for *token* and remove it (single-use).

        The record is `{"intent": <dict>, "verdict": <str|None>}`. Returns None
        if the token is unknown, already consumed, or expired. Prunes expired
        entries on the way through.
        """
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware (e.g. datetime.now(timezone.utc))")
        raw = self._load()
        data = self._prune(raw, now)

        entry = data.get(token)
        if entry is None:
            # Token unknown or was just pruned as expired — only write if pruning
            # actually changed the data (avoid needless durable writes on probes)
            if data != raw:
                self._save(data)
            return None

        # Pop the entry (single-use enforcement via removal)
        remaining = {k: v for k, v in data.items() if k != token}
        self._save(remaining)
        return {"intent": entry["intent"], "verdict": entry.get("verdict")}
