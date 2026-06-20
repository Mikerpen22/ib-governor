# src/governor/rules/catalog.py
"""The rule catalog: one `RuleSpec` per rule — the single, self-verifying source
of truth for the safeguard surface.

Why this exists: the rules live as pure functions scattered across three modules
(futures / equities / portfolio). That's good for testing but bad for discovery —
you can't see the whole brake at a glance, and a hand-written summary would rot the
first time someone adds a rule. So the catalog is plain *data*, the engine
registries are checked against it (tests/rules/test_catalog.py), and docs/RULES.md
is generated from it. Drift becomes a failing test, not a silent lie.

This module is metadata only — it imports no rule logic and runs nothing in the
hot path. Regenerate the human-readable table with:

    python -m governor.rules.catalog
"""
from __future__ import annotations

from dataclasses import dataclass

from ..model import ActionType, AssetClass, Severity


@dataclass(frozen=True)
class RuleSpec:
    """Describes one rule for discovery and docs. Carries no behavior; the logic
    lives in the rules.* modules. `rule_id` is '<section>.<function_name>', which
    is exactly what the rule emits and what the engine registry holds — that
    convention is what lets the sync test bind catalog to code."""

    rule_id: str
    asset_class: AssetClass
    severities: tuple[Severity, ...]   # every severity the rule can emit (overtrading escalates WARN→HARD)
    actions: tuple[ActionType, ...]    # every action the rule can stage
    config_keys: tuple[str, ...]       # dotted rules.yaml paths the rule reads, e.g. "futures.daily_loss_usd"
    summary: str                       # one line: what trips it and what happens


CATALOG: tuple[RuleSpec, ...] = (
    # --- futures ---
    RuleSpec(
        "futures.house_money_lockout", AssetClass.FUTURE,
        (Severity.HARD,), (ActionType.LOCKOUT_FUTURES_48H,),
        ("futures.house_money_win_usd",),
        "Realized futures win exceeds the house-money threshold → stage a 48h futures lockout.",
    ),
    RuleSpec(
        "futures.daily_loss_stop", AssetClass.FUTURE,
        (Severity.HARD,), (ActionType.PLATFORM_OFF_TODAY,),
        ("futures.daily_loss_usd", "futures.max_losing_trades"),
        "Daily loss limit (realized + open futures P&L) or losing-streak limit hit → platform off for the day.",
    ),
    RuleSpec(
        "futures.overtrading", AssetClass.FUTURE,
        (Severity.WARN, Severity.HARD),
        (ActionType.ALERT_ONLY, ActionType.PLATFORM_OFF_TODAY),
        ("futures.overtrading_warn", "futures.overtrading_hard"),
        "Futures trade count crosses the warn threshold (alert), then the hard threshold (platform off).",
    ),
    RuleSpec(
        "futures.overnight_notional", AssetClass.FUTURE,
        (Severity.HARD,), (ActionType.TRIM_FUTURES,),
        ("futures.max_overnight_contracts", "futures.close_window_min"),
        "Oversized futures position still on near the close → stage a trim to the overnight cap.",
    ),
    RuleSpec(
        "futures.live_notional", AssetClass.FUTURE,
        (Severity.WARN,), (ActionType.ALERT_ONLY,),
        ("futures.max_notional_pct",),
        "Intraday futures notional exceeds its allowed share of NAV → leverage-creep alert.",
    ),
    RuleSpec(
        "futures.same_contract_churn", AssetClass.FUTURE,
        (Severity.WARN,), (ActionType.ALERT_ONLY,),
        ("futures.churn_count",),
        "One contract traded too many times in a day → scalping / churn alert.",
    ),
    # --- equities ---
    RuleSpec(
        "equities.single_name", AssetClass.EQUITY,
        (Severity.WARN,), (ActionType.ALERT_ONLY,),
        ("equities.single_name_pct",),
        "A single name exceeds its share-of-NAV cap → concentration alert.",
    ),
    RuleSpec(
        "equities.sector_concentration", AssetClass.EQUITY,
        (Severity.WARN,), (ActionType.ALERT_ONLY,),
        ("equities.sector_pct",),
        "A single sector exceeds its share-of-NAV cap → concentration alert.",
    ),
    RuleSpec(
        "equities.retrade_churn", AssetClass.EQUITY,
        (Severity.WARN,), (ActionType.ALERT_ONLY,),
        ("equities.retrade_per_week",),
        "A name is traded too many times in one week → churn alert.",
    ),
    RuleSpec(
        "equities.add_into_drawdown", AssetClass.EQUITY,
        (Severity.WARN,), (ActionType.ALERT_ONLY,),
        ("equities.drawdown_for_add_flag",),
        "Adding to a losing name while the book is in drawdown → averaging-down alert.",
    ),
    # --- portfolio ---
    RuleSpec(
        "portfolio.margin_cushion", AssetClass.PORTFOLIO,
        (Severity.WARN,), (ActionType.ALERT_ONLY,),
        ("portfolio.min_cushion",),
        "Margin cushion (excess liquidity / NAV) falls below the floor → thin-buffer alert.",
    ),
    RuleSpec(
        "portfolio.gross_leverage", AssetClass.PORTFOLIO,
        (Severity.WARN,), (ActionType.ALERT_ONLY,),
        ("portfolio.max_gross_leverage",),
        "Gross leverage (gross position value / NAV) exceeds its cap → leverage alert.",
    ),
    RuleSpec(
        "portfolio.drawdown_moratorium", AssetClass.PORTFOLIO,
        (Severity.WARN,), (ActionType.ALERT_ONLY,),
        ("portfolio.drawdown_moratorium_pct",),
        "Drawdown from the high-water mark exceeds the limit → new-overlay moratorium alert.",
    ),
)

CATALOG_BY_ID: dict[str, RuleSpec] = {s.rule_id: s for s in CATALOG}

# Section ordering for rendering, paired with the human-facing heading.
_SECTIONS: tuple[tuple[AssetClass, str], ...] = (
    (AssetClass.FUTURE, "Futures"),
    (AssetClass.EQUITY, "Equities"),
    (AssetClass.PORTFOLIO, "Portfolio"),
)


def catalog_for(asset_class: AssetClass) -> tuple[RuleSpec, ...]:
    """Every spec for one asset class, in catalog order."""
    return tuple(s for s in CATALOG if s.asset_class == asset_class)


def render_markdown() -> str:
    """Render the catalog as a grouped Markdown reference. Pure: same catalog in,
    same text out — which is what the in-sync doc test relies on."""
    lines = [
        "# Rule Catalog",
        "",
        "> Generated from `src/governor/rules/catalog.py` — do not edit by hand.",
        "> Regenerate with `python -m governor.rules.catalog`.",
        "",
        f"{len(CATALOG)} rules across futures, equities, and portfolio. Every threshold "
        "lives in `config/rules.yaml`; the **Config keys** column is the path to tune.",
        "",
    ]
    for asset_class, title in _SECTIONS:
        lines += [
            f"## {title}",
            "",
            "| Rule | Severity | Action | Config keys | What trips it |",
            "|------|----------|--------|-------------|---------------|",
        ]
        for s in catalog_for(asset_class):
            sev = " / ".join(x.value for x in s.severities)
            act = " / ".join(x.value for x in s.actions)
            keys = "<br>".join(f"`{k}`" for k in s.config_keys)
            lines.append(f"| `{s.rule_id}` | {sev} | {act} | {keys} | {s.summary} |")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    """Write docs/RULES.md from the catalog. Bypasses the Write-tool path so the
    doc is unambiguously generated, never hand-authored."""
    from pathlib import Path

    doc = Path(__file__).resolve().parents[3] / "docs" / "RULES.md"
    doc.write_text(render_markdown())
    print(f"wrote {doc} ({len(CATALOG)} rules)")


if __name__ == "__main__":
    main()
