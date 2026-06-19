"""Single-use, time-limited confirmation tokens. Pure: `now` and the token
factory are injected, so the whole gate is deterministically testable."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Pending:
    token: str
    payload: object       # what to execute on confirm (e.g. a Trip)
    issued_at: dt.datetime
    dedup_key: str | None = None


class ConfirmTokenGate:
    def __init__(self, ttl_seconds: float, token_factory: Callable[[], str]) -> None:
        self._ttl = ttl_seconds
        self._make = token_factory
        self._pending: dict[str, Pending] = {}

    def issue(self, payload: object, now: dt.datetime, dedup_key: str | None = None) -> str:
        if dedup_key is not None:
            stale = [k for k, p in self._pending.items() if p.dedup_key == dedup_key]
            for k in stale:
                del self._pending[k]
        token = self._make()
        self._pending[token.upper()] = Pending(token=token, payload=payload, issued_at=now,
                                               dedup_key=dedup_key)
        return token

    def verify(self, reply_text: str, now: dt.datetime) -> Pending | None:
        """If `reply_text` contains a live (unexpired, unused) token, consume and return it."""
        words = {w.upper() for w in reply_text.split()}
        for key in list(self._pending):
            if key in words:
                p = self._pending.pop(key)  # single-use: remove on match
                if (now - p.issued_at).total_seconds() > self._ttl:
                    return None
                return p
        return None
