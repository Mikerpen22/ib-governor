# tests/gate/test_staged.py
"""Tests for StagedOrderStore — single-use, TTL-bounded, file-backed, cross-process."""
import stat
import datetime
from datetime import timezone
from itertools import count

import pytest

from governor.gate.staged import StagedOrderStore


UTC = timezone.utc
TTL = 300.0
T0 = datetime.datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)


def _counter_factory(prefix="TOK"):
    """Returns a token_factory that yields TOK1, TOK2, … deterministically."""
    c = count(1)
    return lambda: f"{prefix}{next(c)}"


def _store(tmp_path, ttl=TTL, factory=None):
    kwargs = {}
    if factory is not None:
        kwargs["token_factory"] = factory
    return StagedOrderStore(tmp_path / "staged.json", ttl_seconds=ttl, **kwargs)


# ── round-trip ─────────────────────────────────────────────────────────────────

def test_stage_consume_roundtrip(tmp_path):
    """stage → consume returns a record carrying the exact intent dict."""
    store = _store(tmp_path, factory=_counter_factory())
    intent = {"symbol": "AAPL", "action": "BUY", "qty": 10}
    token = store.stage(intent, T0)
    result = store.consume(token, T0)
    assert result["intent"] == intent


def test_consume_carries_verdict(tmp_path):
    """A verdict passed at stage time is returned by consume (for BLOCK hardening)."""
    store = _store(tmp_path, factory=_counter_factory())
    intent = {"symbol": "ORCL", "action": "BUY", "qty": 5}
    token = store.stage(intent, T0, verdict="BLOCK")
    result = store.consume(token, T0)
    assert result["intent"] == intent
    assert result["verdict"] == "BLOCK"


def test_verdict_defaults_to_none(tmp_path):
    """stage without a verdict → consume returns verdict None (back-compat callers)."""
    store = _store(tmp_path, factory=_counter_factory())
    token = store.stage({"symbol": "AAPL"}, T0)
    assert store.consume(token, T0)["verdict"] is None


# ── single-use ─────────────────────────────────────────────────────────────────

def test_consume_is_single_use(tmp_path):
    """A second consume of the same token returns None."""
    store = _store(tmp_path, factory=_counter_factory())
    token = store.stage({"action": "SELL", "qty": 5}, T0)
    assert store.consume(token, T0) is not None
    assert store.consume(token, T0) is None


# ── TTL / expiry ───────────────────────────────────────────────────────────────

def test_expired_token_returns_none(tmp_path):
    """A token consumed after its TTL is expired → None."""
    ttl = 60.0
    store = _store(tmp_path, ttl=ttl, factory=_counter_factory())
    token = store.stage({"action": "BUY", "qty": 1}, T0)
    # consume at exactly TTL + 1 second after staging
    late = T0 + datetime.timedelta(seconds=ttl + 1)
    assert store.consume(token, late) is None


def test_consume_at_exact_ttl_boundary_is_expired(tmp_path):
    """now >= expires → expired (boundary is inclusive-closed on the expired side)."""
    ttl = 60.0
    store = _store(tmp_path, ttl=ttl, factory=_counter_factory())
    token = store.stage({"action": "BUY", "qty": 1}, T0)
    at_boundary = T0 + datetime.timedelta(seconds=ttl)
    assert store.consume(token, at_boundary) is None


def test_consume_just_before_expiry_succeeds(tmp_path):
    """A token consumed 1 second before expiry is still valid."""
    ttl = 60.0
    store = _store(tmp_path, ttl=ttl, factory=_counter_factory())
    token = store.stage({"action": "BUY", "qty": 1}, T0)
    just_before = T0 + datetime.timedelta(seconds=ttl - 1)
    assert store.consume(token, just_before) is not None


# ── unknown token ──────────────────────────────────────────────────────────────

def test_unknown_token_returns_none(tmp_path):
    """Consuming a garbage/unknown token returns None."""
    store = _store(tmp_path, factory=_counter_factory())
    assert store.consume("ZZZZ", T0) is None


def test_empty_store_unknown_token(tmp_path):
    """Consuming from an empty store returns None."""
    store = _store(tmp_path)
    assert store.consume("ABCD", T0) is None


# ── cross-process persistence ──────────────────────────────────────────────────

def test_persistence_across_instances(tmp_path):
    """Stage with one instance, consume with a fresh instance — proves on-disk persistence."""
    path = tmp_path / "staged.json"
    factory = _counter_factory()
    store1 = StagedOrderStore(path, ttl_seconds=TTL, token_factory=factory)
    intent = {"action": "BUY", "qty": 3, "symbol": "NVDA"}
    token = store1.stage(intent, T0)

    # Fresh instance — no shared in-memory state
    store2 = StagedOrderStore(path, ttl_seconds=TTL)
    result = store2.consume(token, T0)
    assert result["intent"] == intent


# ── file permissions ───────────────────────────────────────────────────────────

def test_file_permissions_are_0600(tmp_path):
    """The backing file must be owner-read/write only (0600)."""
    store = _store(tmp_path, factory=_counter_factory())
    store.stage({"action": "BUY", "qty": 1}, T0)
    path = tmp_path / "staged.json"
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


# ── multiple tokens coexist ────────────────────────────────────────────────────

def test_two_tokens_coexist(tmp_path):
    """Staging a second order does not clobber the first."""
    factory = _counter_factory()
    store = _store(tmp_path, factory=factory)
    intent1 = {"action": "BUY", "qty": 10, "symbol": "AAPL"}
    intent2 = {"action": "SELL", "qty": 5, "symbol": "TSLA"}
    tok1 = store.stage(intent1, T0)
    tok2 = store.stage(intent2, T0)
    assert tok1 != tok2
    # Both are retrievable independently
    assert store.consume(tok1, T0)["intent"] == intent1
    assert store.consume(tok2, T0)["intent"] == intent2


def test_two_tokens_independent_single_use(tmp_path):
    """After consuming token1, token2 is still live."""
    factory = _counter_factory()
    store = _store(tmp_path, factory=factory)
    tok1 = store.stage({"action": "BUY", "qty": 1}, T0)
    tok2 = store.stage({"action": "SELL", "qty": 2}, T0)
    store.consume(tok1, T0)
    # tok2 still retrievable, tok1 is gone
    assert store.consume(tok2, T0) is not None
    assert store.consume(tok1, T0) is None


# ── expiry pruning doesn't affect live tokens ──────────────────────────────────

def test_expired_token_pruned_live_token_survives(tmp_path):
    """An expired token is pruned on consume; a live token in the same file survives."""
    factory = _counter_factory()
    ttl = 60.0
    store = _store(tmp_path, ttl=ttl, factory=factory)
    tok_expired = store.stage({"action": "BUY", "qty": 1}, T0)
    tok_live = store.stage({"action": "SELL", "qty": 2}, T0 + datetime.timedelta(seconds=ttl - 5))

    # Consume at a time where tok_expired is past TTL but tok_live isn't
    # tok_expired was staged at T0, expires at T0 + 60s
    # tok_live was staged at T0+55s, expires at T0+115s
    now = T0 + datetime.timedelta(seconds=61)
    assert store.consume(tok_expired, now) is None   # expired
    assert store.consume(tok_live, now) is not None  # still live


# ── corrupt file propagates StateFileError ────────────────────────────────────

def test_corrupt_file_raises_state_file_error(tmp_path):
    """A corrupt backing file must propagate StateFileError (fail loud)."""
    from governor.state.json_store import StateFileError
    path = tmp_path / "staged.json"
    path.write_text("not-valid-json")
    store = StagedOrderStore(path, ttl_seconds=TTL)
    with pytest.raises(StateFileError):
        store.stage({"action": "BUY", "qty": 1}, T0)


def test_corrupt_file_on_consume_raises_state_file_error(tmp_path):
    """A corrupt backing file on consume must propagate StateFileError."""
    from governor.state.json_store import StateFileError
    path = tmp_path / "staged.json"
    path.write_text("{bad json")
    store = StagedOrderStore(path, ttl_seconds=TTL)
    with pytest.raises(StateFileError):
        store.consume("ABCD", T0)


# ── token collision (unique-token guarantee) ───────────────────────────────────

def test_token_collision_does_not_overwrite_first_entry(tmp_path):
    """When the factory yields a duplicate token, stage() retries until it gets
    a unique one — the first staged entry is never silently overwritten."""
    # Sequence: first call → "DUP", second call → "DUP" (retry), third → "UNIQUE"
    tokens = iter(["DUP", "DUP", "UNIQUE"])
    factory = lambda: next(tokens)  # noqa: E731

    store = _store(tmp_path, factory=factory)
    intent1 = {"action": "BUY", "qty": 10, "symbol": "AAPL"}
    intent2 = {"action": "SELL", "qty": 5, "symbol": "TSLA"}

    tok1 = store.stage(intent1, T0)       # consumes "DUP" → stored as "DUP"
    tok2 = store.stage(intent2, T0)       # first try "DUP" (taken) → retries → "UNIQUE"

    assert tok1 == "DUP"
    assert tok2 == "UNIQUE"
    # Both independently consumable — first entry not overwritten
    assert store.consume("DUP", T0)["intent"] == intent1
    assert store.consume("UNIQUE", T0)["intent"] == intent2


# ── naive datetime rejection ───────────────────────────────────────────────────

NAIVE_NOW = datetime.datetime(2026, 6, 17, 12, 0)  # no tzinfo


def test_stage_rejects_naive_datetime(tmp_path):
    """stage() with a naive datetime raises ValueError."""
    store = _store(tmp_path, factory=_counter_factory())
    with pytest.raises(ValueError, match="timezone-aware"):
        store.stage({"action": "BUY", "qty": 1}, NAIVE_NOW)


def test_consume_rejects_naive_datetime(tmp_path):
    """consume() with a naive datetime raises ValueError."""
    store = _store(tmp_path, factory=_counter_factory())
    with pytest.raises(ValueError, match="timezone-aware"):
        store.consume("ANYTOKEN", NAIVE_NOW)
