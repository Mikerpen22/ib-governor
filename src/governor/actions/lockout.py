"""Persistent lockout flag. File-backed so it survives daemon restarts."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from ..state.json_store import StateFileError, load_json, save_json


@dataclass(frozen=True)
class Lockout:
    kind: str                 # "futures_48h" | "platform_off_today"
    until: dt.datetime
    reason: str


class LockoutStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def set(self, lockout: Lockout) -> None:
        # durable=True: a lockout we just confirmed must survive a crash an instant later.
        save_json(self._path, {
            "kind": lockout.kind,
            "until": lockout.until.isoformat(),
            "reason": lockout.reason,
        }, durable=True)

    def active(self, now: dt.datetime) -> Lockout | None:
        """The active Lockout, or None when there is PROVABLY no lockout (no file, or a
        cleanly-parsed lockout that has expired). Raises StateFileError when the file is
        PRESENT but unreadable/garbled — callers MUST treat that as 'assume locked'
        (fail closed), never as clear. A blind brake must stop you, not wave you through."""
        if not self._path.exists():
            return None                               # provably clear: no file
        data = load_json(self._path, {})              # corrupt / wrong-type -> StateFileError
        try:
            until = dt.datetime.fromisoformat(data["until"])
            kind = data["kind"]
            reason = data["reason"]
        except (ValueError, KeyError, TypeError) as exc:
            raise StateFileError(f"{self._path}: lockout fields unreadable: {exc}") from exc
        if until <= now:
            return None                               # cleanly expired
        return Lockout(kind=kind, until=until, reason=reason)

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)
