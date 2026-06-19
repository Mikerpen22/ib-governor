# tests/actions/test_lockout.py
import datetime as dt

import pytest

from governor.actions.lockout import Lockout, LockoutStore
from governor.state.json_store import StateFileError

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 6, 17, 11, 0, tzinfo=UTC)


def test_set_and_active(tmp_path):
    store = LockoutStore(tmp_path / "lockout.json")
    assert store.active(T0) is None
    store.set(Lockout(kind="futures_48h", until=T0 + dt.timedelta(hours=48), reason="house money"))
    a = store.active(T0 + dt.timedelta(hours=1))
    assert a is not None and a.kind == "futures_48h"


def test_expires(tmp_path):
    store = LockoutStore(tmp_path / "lockout.json")
    store.set(Lockout("platform_off_today", T0 + dt.timedelta(hours=2), "loss stop"))
    assert store.active(T0 + dt.timedelta(hours=3)) is None


def test_persists_across_instances(tmp_path):
    path = tmp_path / "lockout.json"
    LockoutStore(path).set(Lockout("futures_48h", T0 + dt.timedelta(hours=48), "r"))
    assert LockoutStore(path).active(T0 + dt.timedelta(hours=1)) is not None  # reloaded from disk


def test_clear(tmp_path):
    path = tmp_path / "lockout.json"
    s = LockoutStore(path)
    s.set(Lockout("futures_48h", T0 + dt.timedelta(hours=48), "r"))
    s.clear()
    assert s.active(T0) is None


def test_present_but_unreadable_file_raises(tmp_path):
    """SAFETY: a present-but-unreadable lockout file must NOT read as 'no lockout'.
    It's indeterminate -> raise so the daemon fails CLOSED (assume locked + alert)."""
    path = tmp_path / "lockout.json"
    path.write_text('{"until": "2099-01-01T00:00:00+00:00"}')  # present dict, missing kind/reason
    with pytest.raises(StateFileError):
        LockoutStore(path).active(T0)


def test_garbage_file_raises(tmp_path):
    path = tmp_path / "lockout.json"
    path.write_text("not even json")
    with pytest.raises(StateFileError):
        LockoutStore(path).active(T0)


def test_absent_file_is_provably_clear(tmp_path):
    """No file at all = provably no lockout = None (the ONLY silent-clear case)."""
    assert LockoutStore(tmp_path / "lockout.json").active(T0) is None


def test_lockout_file_is_owner_only(tmp_path):
    import stat
    path = tmp_path / "lockout.json"
    store = LockoutStore(path)
    store.set(Lockout("futures_48h", T0 + dt.timedelta(hours=48), "r"))
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600
