"""The order catalog is the single source of truth for the order-type surface —
what types the gate accepts, which prices each needs, and the cross-cutting
modifiers (Adaptive, bracketing, TIF). These tests turn drift into a failure:
every OrderType enum value must be cataloged, the Adaptive entries must layer on
the right base types, the priorities/TIFs documented must match the enums, and
the generated docs/ORDER_TYPES.md can't fall out of sync.

Mirrors tests/rules/test_catalog.py.
"""
from __future__ import annotations

from pathlib import Path

from governor.gate.intent import AdaptivePriority, OrderType
from governor.gate.order_catalog import (
    CATALOG,
    CATALOG_BY_KEY,
    TIF_CHOICES,
    render_markdown,
    render_table,
)


# ---------------------------------------------------------------------------
# Structural invariants
# ---------------------------------------------------------------------------


def test_catalog_keys_are_unique():
    keys = [e.key for e in CATALOG]
    assert len(keys) == len(set(keys))


def test_catalog_by_key_indexes_every_entry():
    assert set(CATALOG_BY_KEY) == {e.key for e in CATALOG}
    for e in CATALOG:
        assert CATALOG_BY_KEY[e.key] is e


def test_entries_are_well_formed():
    for e in CATALOG:
        assert e.key.strip(), "entry has empty key"
        assert e.name.strip(), f"{e.key} has no display name"
        assert isinstance(e.base, OrderType), f"{e.key} base is not an OrderType"
        assert isinstance(e.adaptive, bool)
        assert e.description.strip(), f"{e.key} has no description"
        assert e.when_to_use.strip(), f"{e.key} has no when_to_use note"
        assert isinstance(e.required, tuple)
        assert isinstance(e.optional, tuple)


# ---------------------------------------------------------------------------
# Content: every base type cataloged; adaptive maps to MARKET/LIMIT; priorities
# ---------------------------------------------------------------------------


def test_every_order_type_enum_value_is_cataloged():
    # A new OrderType added without a catalog entry fails here — discovery can't
    # silently lag the enum.
    cataloged_bases = {e.base for e in CATALOG}
    assert cataloged_bases == set(OrderType)


def test_adaptive_entries_map_to_market_or_limit_base():
    adaptive_entries = [e for e in CATALOG if e.adaptive]
    assert adaptive_entries, "expected at least one adaptive entry"
    for e in adaptive_entries:
        assert e.base in (OrderType.MARKET, OrderType.LIMIT), (
            f"adaptive entry {e.key} layers on {e.base}; adaptive is MKT/LMT only"
        )
    # Both an adaptive-market and an adaptive-limit should exist (the two valid bases).
    adaptive_bases = {e.base for e in adaptive_entries}
    assert adaptive_bases == {OrderType.MARKET, OrderType.LIMIT}


def test_non_adaptive_entries_cover_each_plain_base_once():
    plain = [e for e in CATALOG if not e.adaptive]
    bases = [e.base for e in plain]
    assert sorted(b.value for b in bases) == sorted(b.value for b in OrderType)


def test_catalog_keys_are_kebab_case_and_expected_set():
    assert {e.key for e in CATALOG} >= {
        "market", "limit", "stop", "stop-limit",
        "adaptive-market", "adaptive-limit",
    }


# ---------------------------------------------------------------------------
# Markdown rendering: every type + the cross-cutting sections appear
# ---------------------------------------------------------------------------


def test_render_markdown_includes_every_entry():
    md = render_markdown()
    for e in CATALOG:
        assert e.name in md, f"{e.name} missing from rendered markdown"
        assert e.key in md, f"{e.key} missing from rendered markdown"


def test_render_markdown_documents_adaptive_priorities():
    md = render_markdown()
    for p in AdaptivePriority:
        assert p.value in md, f"adaptive priority {p.value} not documented"


def test_render_markdown_documents_bracketing_and_tif():
    md = render_markdown()
    lower = md.lower()
    assert "bracket" in lower, "bracketing not documented"
    assert "--stop-loss" in md and "--take-profit" in md
    # TIF cross-cutting capability
    assert "TIF" in md or "time-in-force" in lower
    for tif in TIF_CHOICES:
        assert tif in md, f"TIF {tif} not documented"


# ---------------------------------------------------------------------------
# Table rendering for the CLI: human-readable, covers types + the notes
# ---------------------------------------------------------------------------


def test_render_table_lists_every_type_and_the_notes():
    table = render_table()
    for e in CATALOG:
        assert e.name in table
    lower = table.lower()
    assert "bracket" in lower
    assert "adaptive" in lower
    for p in AdaptivePriority:
        assert p.value in table
    for tif in TIF_CHOICES:
        assert tif in table


# ---------------------------------------------------------------------------
# Generated doc stays in sync (mirrors test_catalog.test_committed_doc_in_sync)
# ---------------------------------------------------------------------------


def test_committed_doc_is_in_sync_with_catalog():
    # Regenerate with: python -m governor.gate.order_catalog
    doc = Path(__file__).resolve().parents[2] / "docs" / "ORDER_TYPES.md"
    assert doc.exists(), (
        "docs/ORDER_TYPES.md missing — run: python -m governor.gate.order_catalog"
    )
    assert doc.read_text() == render_markdown(), (
        "docs/ORDER_TYPES.md is stale — regenerate it"
    )
