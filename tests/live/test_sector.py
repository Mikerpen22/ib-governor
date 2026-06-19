"""Tests for SectorResolver persistent cache.

All tests pass cache_path=None or a tmp_path so the real
config/sector_cache.json is never touched during the test run.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from governor.live.sector import SectorResolver


# ---------------------------------------------------------------------------
# Fake IB helpers
# ---------------------------------------------------------------------------

def _make_ib(industry: str | None = "Technology"):
    """Return a fake IB object whose reqContractDetails call is counted."""
    calls = []

    def req(contract):
        calls.append(contract)
        if industry is None:
            return []
        return [SimpleNamespace(industry=industry)]

    ib = SimpleNamespace(reqContractDetails=req)
    ib._calls = calls  # expose for assertions
    return ib


# ---------------------------------------------------------------------------
# Basic in-memory behaviour (cache_path=None)
# ---------------------------------------------------------------------------

def test_resolve_returns_sector(tmp_path):
    ib = _make_ib("Technology")
    sr = SectorResolver(ib, cache_path=None)
    assert sr.resolve("AAPL") == "Technology"
    assert len(ib._calls) == 1


def test_resolve_in_memory_cache_hit(tmp_path):
    ib = _make_ib("Technology")
    sr = SectorResolver(ib, cache_path=None)
    sr.resolve("AAPL")
    sr.resolve("AAPL")  # second call: should be served from memory
    assert len(ib._calls) == 1


def test_cache_path_none_no_file_created(tmp_path):
    ib = _make_ib("Technology")
    sr = SectorResolver(ib, cache_path=None)
    sr.resolve("AAPL")
    assert list(tmp_path.iterdir()) == [], "no file should be written when cache_path=None"


# ---------------------------------------------------------------------------
# Persistent cache — cross-instance hit
# ---------------------------------------------------------------------------

def test_persistence_second_instance_zero_api_calls(tmp_path):
    """Resolve via instance 1; a fresh instance 2 must serve from disk with zero API calls."""
    cache = tmp_path / "c.json"

    ib1 = _make_ib("Technology")
    sr1 = SectorResolver(ib1, cache_path=cache)
    result1 = sr1.resolve("AAPL")
    assert result1 == "Technology"
    assert len(ib1._calls) == 1
    assert cache.exists()

    ib2 = _make_ib("Technology")
    sr2 = SectorResolver(ib2, cache_path=cache)
    result2 = sr2.resolve("AAPL")
    assert result2 == "Technology"
    assert len(ib2._calls) == 0, "second instance must not call reqContractDetails"


def test_persistence_file_shape(tmp_path):
    """Disk file must be a JSON object mapping symbol -> sector string."""
    cache = tmp_path / "c.json"
    ib = _make_ib("Technology")
    SectorResolver(ib, cache_path=cache).resolve("AAPL")

    data = json.loads(cache.read_text())
    assert data == {"AAPL": "Technology"}


# ---------------------------------------------------------------------------
# Known-unknown caching (None → JSON null → not re-fetched)
# ---------------------------------------------------------------------------

def test_known_unknown_cached_as_null(tmp_path):
    """A symbol that resolves to None is stored as null in JSON."""
    cache = tmp_path / "c.json"
    ib = _make_ib(industry=None)  # reqContractDetails returns []
    sr = SectorResolver(ib, cache_path=cache)
    result = sr.resolve("UNKN")
    assert result is None
    assert len(ib._calls) == 1

    data = json.loads(cache.read_text())
    assert data == {"UNKN": None}


def test_known_unknown_not_refetched(tmp_path):
    """After caching a None, a second instance must not call the API again."""
    cache = tmp_path / "c.json"

    ib1 = _make_ib(industry=None)
    SectorResolver(ib1, cache_path=cache).resolve("UNKN")
    assert len(ib1._calls) == 1

    ib2 = _make_ib(industry=None)
    result = SectorResolver(ib2, cache_path=cache).resolve("UNKN")
    assert result is None
    assert len(ib2._calls) == 0


# ---------------------------------------------------------------------------
# Corrupt cache → start empty, no crash
# ---------------------------------------------------------------------------

def test_corrupt_cache_starts_empty(tmp_path):
    cache = tmp_path / "c.json"
    cache.write_text("not valid json {{{")

    ib = _make_ib("Technology")
    sr = SectorResolver(ib, cache_path=cache)
    # Must not raise; cache is empty
    assert sr._cache == {}

    # Resolving still works (falls back to API)
    result = sr.resolve("AAPL")
    assert result == "Technology"
    assert len(ib._calls) == 1


def test_corrupt_cache_wrong_type(tmp_path):
    """A valid JSON file that is not a dict (e.g. a list) is also treated as corrupt."""
    cache = tmp_path / "c.json"
    cache.write_text("[1, 2, 3]")

    ib = _make_ib("Technology")
    sr = SectorResolver(ib, cache_path=cache)
    assert sr._cache == {}


# ---------------------------------------------------------------------------
# map_for — batch resolution + single persist + return contract
# ---------------------------------------------------------------------------

def test_map_for_returns_only_known_sectors(tmp_path):
    def req(contract):
        sym = contract.symbol
        if sym == "AAPL":
            return [SimpleNamespace(industry="Technology")]
        return []  # UNKN → None

    ib = SimpleNamespace(reqContractDetails=req)
    sr = SectorResolver(ib, cache_path=None)
    result = sr.map_for(["AAPL", "UNKN"])
    assert result == {"AAPL": "Technology"}
    assert "UNKN" not in result


def test_map_for_persists_once(tmp_path):
    """map_for should persist exactly once at the end, not once per symbol."""
    cache = tmp_path / "c.json"

    write_count = []

    import governor.live.sector as sector_module

    original_save = sector_module.save_json

    def counting_save(path, data, **kwargs):
        write_count.append(1)
        original_save(path, data, **kwargs)

    sector_module.save_json = counting_save
    try:
        ib = _make_ib("Technology")
        sr = SectorResolver(ib, cache_path=cache)
        sr.map_for(["AAPL", "MSFT", "GOOG"])
    finally:
        sector_module.save_json = original_save

    # One write for loading (none), then one write at the end of map_for
    assert sum(write_count) == 1, f"expected 1 write, got {sum(write_count)}"


def test_map_for_no_persist_when_all_cached(tmp_path):
    """map_for must not write to disk when nothing was newly resolved."""
    cache = tmp_path / "c.json"

    # Pre-populate with AAPL
    ib1 = _make_ib("Technology")
    sr1 = SectorResolver(ib1, cache_path=cache)
    sr1.resolve("AAPL")

    import governor.live.sector as sector_module

    original_save = sector_module.save_json
    write_count = []

    def counting_save(path, data, **kwargs):
        write_count.append(1)
        original_save(path, data, **kwargs)

    sector_module.save_json = counting_save
    try:
        ib2 = _make_ib("Technology")
        sr2 = SectorResolver(ib2, cache_path=cache)
        sr2.map_for(["AAPL"])  # already in cache → no new entries → no write
    finally:
        sector_module.save_json = original_save

    assert sum(write_count) == 0, "no write expected when cache already covers all symbols"


# ---------------------------------------------------------------------------
# Default path still resolves correctly (no file I/O in this assertion)
# ---------------------------------------------------------------------------

def test_default_cache_path_is_set():
    """Constructing without cache_path uses the default string path."""
    from governor.live.sector import _DEFAULT_CACHE_PATH
    from pathlib import Path

    ib = _make_ib()
    # Don't actually let it load/write the real config file — just check the attribute
    import unittest.mock as mock

    with mock.patch("governor.live.sector.load_json", return_value={}):
        sr = SectorResolver(ib)  # default cache_path
        assert sr._cache_path == Path(_DEFAULT_CACHE_PATH)
