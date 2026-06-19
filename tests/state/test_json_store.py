"""Direct tests for the shared atomic JSON store: a present-but-unreadable file is
LOUD (StateFileError), writes are atomic + optionally durable, the parent dir is
auto-created, and a failed write leaves no debris and never silently 'succeeds'."""
import stat

import pytest

from governor.state.json_store import StateFileError, load_json, save_json


def test_load_absent_returns_default(tmp_path):
    assert load_json(tmp_path / "nope.json", {}) == {}
    assert load_json(tmp_path / "nope.json", {"a": 1}) == {"a": 1}


def test_load_valid_roundtrips(tmp_path):
    p = tmp_path / "s.json"
    save_json(p, {"k": "v"})
    assert load_json(p, {}) == {"k": "v"}


def test_load_corrupt_present_file_raises(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{not json")
    with pytest.raises(StateFileError):
        load_json(p, {})


def test_load_wrong_top_level_type_raises(tmp_path):
    """A present file whose top-level JSON isn't the expected type is corruption."""
    p = tmp_path / "s.json"
    p.write_text("[1, 2, 3]")        # a list where a dict was expected
    with pytest.raises(StateFileError):
        load_json(p, {})


def test_save_creates_missing_parent_dir(tmp_path):
    p = tmp_path / "nested" / "deep" / "s.json"   # parents do not exist
    save_json(p, {"k": 1})
    assert load_json(p, {}) == {"k": 1}


def test_save_is_owner_only(tmp_path):
    p = tmp_path / "s.json"
    save_json(p, {"k": 1})
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_save_durable_writes_correctly(tmp_path):
    p = tmp_path / "s.json"
    save_json(p, {"k": 1}, durable=True)
    assert load_json(p, {}) == {"k": 1}
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_failed_write_leaves_no_tmp_and_keeps_target(tmp_path):
    """An unserializable payload must raise, leave the prior target intact, and
    leave no orphaned .tmp behind — never a silent partial success."""
    p = tmp_path / "s.json"
    save_json(p, {"good": 1})

    class Unserializable:
        pass

    with pytest.raises(TypeError):
        save_json(p, {"bad": Unserializable()})
    assert not p.with_suffix(p.suffix + ".tmp").exists()
    assert load_json(p, {}) == {"good": 1}        # untouched
