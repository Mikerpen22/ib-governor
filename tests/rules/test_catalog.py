"""The catalog is the single source of truth for the rule surface. These tests
turn drift into a failure: a rule can't enter the engine without a catalog entry
(or vice versa), the "where to tune it" column can't point at a config field that
doesn't exist, and the generated docs/RULES.md can't fall out of sync."""
from __future__ import annotations

from pathlib import Path

from governor.config import RulesConfig
from governor.model import ActionType, AssetClass, Severity
from governor.rules.catalog import CATALOG, CATALOG_BY_ID, render_markdown
from governor.rules.engine import EQUITY_RULES, FUTURES_RULES, PORTFOLIO_RULES

_PREFIX = {
    AssetClass.FUTURE: "futures",
    AssetClass.EQUITY: "equities",
    AssetClass.PORTFOLIO: "portfolio",
}
_REGISTRIES = {
    AssetClass.FUTURE: FUTURES_RULES,
    AssetClass.EQUITY: EQUITY_RULES,
    AssetClass.PORTFOLIO: PORTFOLIO_RULES,
}


def _registry_ids() -> set[str]:
    """rule_id == '<section>.<function_name>' — the convention every rule follows."""
    return {
        f"{_PREFIX[asset]}.{fn.__name__}"
        for asset, registry in _REGISTRIES.items()
        for fn in registry
    }


def test_catalog_covers_exactly_the_registered_rules():
    # Add a rule to the engine without cataloging it (or leave a stale catalog
    # entry behind) and this assertion fails — drift can't slip through.
    assert {s.rule_id for s in CATALOG} == _registry_ids()


def test_catalog_ids_are_unique():
    ids = [s.rule_id for s in CATALOG]
    assert len(ids) == len(set(ids))


def test_catalog_by_id_indexes_every_spec():
    assert set(CATALOG_BY_ID) == {s.rule_id for s in CATALOG}
    for s in CATALOG:
        assert CATALOG_BY_ID[s.rule_id] is s


def test_specs_are_well_formed():
    for s in CATALOG:
        assert s.rule_id.startswith(_PREFIX[s.asset_class] + "."), s.rule_id
        assert s.severities and all(isinstance(x, Severity) for x in s.severities)
        assert s.actions and all(isinstance(x, ActionType) for x in s.actions)
        assert s.config_keys, f"{s.rule_id} lists no config keys"
        assert s.summary.strip(), f"{s.rule_id} has no summary"


def test_every_config_key_resolves_against_the_schema():
    # A typo in a config path fails here, so the "Config keys" column never lies.
    cfg = RulesConfig()
    sections = set(_PREFIX.values())
    for s in CATALOG:
        for key in s.config_keys:
            section, _, field = key.partition(".")
            assert section in sections, key
            assert hasattr(getattr(cfg, section), field), f"{key} not in schema"


def test_render_markdown_includes_every_rule_and_section():
    md = render_markdown()
    for s in CATALOG:
        assert s.rule_id in md
    for title in ("Futures", "Equities", "Portfolio"):
        assert f"## {title}" in md


def test_committed_doc_is_in_sync_with_catalog():
    # Regenerate with: python -m governor.rules.catalog
    doc = Path(__file__).resolve().parents[2] / "docs" / "RULES.md"
    assert doc.exists(), "docs/RULES.md missing — run: python -m governor.rules.catalog"
    assert doc.read_text() == render_markdown(), "docs/RULES.md is stale — regenerate it"
