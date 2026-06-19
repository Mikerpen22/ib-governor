"""Atomic JSON read/write helpers shared by all file-backed state stores.

Design stance for a safety system: a *present* state file that can't be read as
the expected type is **corruption, not 'no state'**. `load_json` raises
`StateFileError` rather than silently returning the default, so each caller picks
its own policy — the safety-critical lockout store fails CLOSED + loud, the
advisory hwm/trade-log stores log and self-heal. Only an *absent* file is the
clean 'no state yet' case, which returns the default.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


class StateFileError(Exception):
    """A state file exists but is unreadable, not valid JSON, or the wrong shape.

    Callers MUST treat this as INDETERMINATE — for a safety interlock that means
    fail closed (assume locked + alert), never silently treat it as 'all clear'.
    """


def load_json(path, default):
    """Absent file -> *default*. Present file -> its parsed JSON, which must be the
    same top-level type as *default*. A present file that is unreadable, not valid
    JSON, or a different top-level type raises StateFileError (never a silent default)."""
    p = Path(path)
    if not p.exists():
        return default
    try:
        value = json.loads(p.read_text())
    except (OSError, ValueError) as exc:  # JSONDecodeError is a ValueError subclass
        raise StateFileError(f"{p} is present but not readable JSON: {exc}") from exc
    if type(value) is not type(default):
        raise StateFileError(
            f"{p}: expected top-level {type(default).__name__}, got {type(value).__name__}"
        )
    return value


def save_json(path, data, *, durable: bool = False) -> None:
    """Write *data* as JSON to *path* atomically and with 0600 permissions.

    Atomic: write a sibling .tmp in the same directory, then POSIX-rename over the
    target, so a reader never sees a torn file. The parent directory is created if
    missing. On ANY write failure the .tmp is removed and the error re-raised — no
    silent partial success, no orphaned debris, target left intact.

    durable=True additionally fsyncs the file and its directory before returning, so
    the write survives power-loss / kernel panic. Use it for state whose loss is
    dangerous (the lockout flag); skip it for high-frequency advisory writes.
    """
    payload = json.dumps(data)            # fail fast on unserializable data, before touching disk
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        # O_CREAT with 0600 avoids a window where the tmp is world-readable.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(payload)
            fh.flush()
            if durable:
                os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)              # enforce mode regardless of umask
        os.replace(tmp, p)               # atomic same-filesystem rename
        if durable:
            dir_fd = os.open(p.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)         # make the rename itself durable
            finally:
                os.close(dir_fd)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
