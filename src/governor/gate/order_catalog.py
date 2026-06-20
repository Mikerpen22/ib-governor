# src/governor/gate/order_catalog.py
"""The order catalog: one `OrderTypeSpec` per selectable order type — the single,
self-verifying source of truth for the *order-type surface* (the mirror image of
`rules/catalog.py`, which catalogs the *rule* surface).

Why this exists: the order types live as branches in `intent.build_order` and as
`--type`/`--adaptive` flags on the CLI. That's fine for the machine, but a trader
staring at the gate can't see *what they can ask for* or *which one fits their
intent*. A hand-written cheat-sheet would rot the first time someone adds an
order type. So the catalog is plain *data*: the `OrderType` enum is checked
against it (tests/gate/test_order_catalog.py), the CLI prints it
(`order-types`), and docs/ORDER_TYPES.md is generated from it. Drift becomes a
failing test, not a stale lie.

This module is metadata only — it imports the enums for type-safety but runs no
order logic and touches nothing in the hot path. Regenerate the doc with:

    python -m governor.gate.order_catalog
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .intent import AdaptivePriority, OrderType


@dataclass(frozen=True)
class OrderTypeSpec:
    """Describes one selectable order type for discovery and docs. Carries no
    behavior; the mapping to an ib_async Order lives in `intent.build_order`.

    `base` is the underlying `OrderType` the gate validates and sends. `adaptive`
    marks the IBKR Adaptive algo *modifier* layered on a MKT/LMT base — Adaptive
    is not its own `OrderType` (order.orderType stays MKT/LMT), so adaptive
    entries reuse a base value rather than introducing a new one. That is exactly
    what lets the sync test assert "every OrderType is cataloged" without the
    adaptive rows inflating the enum.
    """

    key: str                       # kebab CLI selector, e.g. "stop-limit", "adaptive-market"
    name: str                      # human display name, e.g. "Adaptive Market"
    base: OrderType                # the underlying order type the gate sends
    adaptive: bool                 # True → IBKR Adaptive algo layered on the MKT/LMT base
    required: tuple[str, ...]      # flags the trader MUST supply, e.g. ("--limit",)
    optional: tuple[str, ...]      # modifiers that further shape it, e.g. ("--tif", "--priority")
    description: str               # one line: what it does
    when_to_use: str               # one line: the intent it fits
    cli: str = field(default="")   # example `--type ...` invocation fragment


# The two cross-cutting modifiers (apply across the table, not to one row):
#  - Bracketing: any entry + protective legs.
#  - TIF: how long the order lives.
_BRACKET_FLAGS = ("--stop-loss", "--take-profit")
TIF_CHOICES: tuple[str, ...] = ("DAY", "GTC")  # day order vs good-till-cancelled
_PRIORITY_FLAG = "--priority"
_TIF_FLAG = "--tif"


CATALOG: tuple[OrderTypeSpec, ...] = (
    # --- plain base types (one per OrderType enum value) ---
    OrderTypeSpec(
        "market", "Market", OrderType.MARKET, False,
        required=(),
        optional=(_TIF_FLAG, *_BRACKET_FLAGS),
        description="Fill immediately at the best available price; no price guaranteed.",
        when_to_use="You need it filled now and accept whatever the book gives you.",
        cli="--type market",
    ),
    OrderTypeSpec(
        "limit", "Limit", OrderType.LIMIT, False,
        required=("--limit",),
        optional=(_TIF_FLAG, *_BRACKET_FLAGS),
        description="Fill only at your limit price or better; may not fill at all.",
        when_to_use="You have a price in mind and would rather miss the trade than chase it.",
        cli="--type limit --limit PX",
    ),
    OrderTypeSpec(
        "stop", "Stop (stop-market)", OrderType.STOP, False,
        required=("--stop",),
        optional=(_TIF_FLAG,),
        description="Resting order that becomes a market order once the stop price trades.",
        when_to_use="Trigger an entry on a breakout, or exit once a level breaks — fill speed over price.",
        cli="--type stop --stop PX",
    ),
    OrderTypeSpec(
        "stop-limit", "Stop-Limit", OrderType.STOP_LIMIT, False,
        required=("--stop", "--limit"),
        optional=(_TIF_FLAG,),
        description="Becomes a limit order (at --limit) once the stop price trades; bounds slippage but can miss.",
        when_to_use="Same trigger as a stop, but you refuse to fill worse than your limit.",
        cli="--type stop-limit --stop PX --limit PX",
    ),
    # --- Adaptive algo modifier on the MKT/LMT bases (not new enum values) ---
    OrderTypeSpec(
        "adaptive-market", "Adaptive Market", OrderType.MARKET, True,
        required=(),
        optional=(_PRIORITY_FLAG, _TIF_FLAG, *_BRACKET_FLAGS),
        description="A market order run through IBKR's Adaptive (IBALGO) for better fills than a naked market.",
        when_to_use="Want it filled now without watching the tape — better average fill than a raw market, no price to guess.",
        cli="--type market --adaptive [--priority normal]",
    ),
    OrderTypeSpec(
        "adaptive-limit", "Adaptive Limit", OrderType.LIMIT, True,
        required=("--limit",),
        optional=(_PRIORITY_FLAG, _TIF_FLAG, *_BRACKET_FLAGS),
        description="A limit order run through IBKR's Adaptive (IBALGO) to work the order toward your limit.",
        when_to_use="You have a limit but want the algo to work the order patiently/urgently rather than rest passively.",
        cli="--type limit --limit PX --adaptive [--priority normal]",
    ),
)

CATALOG_BY_KEY: dict[str, OrderTypeSpec] = {e.key: e for e in CATALOG}


def spec_for(key: str) -> OrderTypeSpec | None:
    """Look up one spec by its CLI key, or None if unknown."""
    return CATALOG_BY_KEY.get(key)


# The CLI `--type` selector for each base OrderType (kebab, not the enum value:
# STOP_LIMIT → "stop-limit"). Adaptive entries pass their base's selector + --adaptive.
_TYPE_FLAG: dict[OrderType, str] = {
    OrderType.MARKET: "market",
    OrderType.LIMIT: "limit",
    OrderType.STOP: "stop",
    OrderType.STOP_LIMIT: "stop-limit",
}


def _priorities() -> str:
    """The Adaptive priorities, in enum order, as a human list."""
    return ", ".join(p.value for p in AdaptivePriority)


def _params(e: OrderTypeSpec) -> str:
    """Render an entry's required (bold) + optional params for a table cell."""
    req = " ".join(f"**`{p}`**" for p in e.required)
    opt = " ".join(f"`{p}`" for p in e.optional)
    if req and opt:
        return f"{req} · {opt}"
    return req or opt or "—"


def render_markdown() -> str:
    """Render the catalog as a Markdown reference. Pure: same catalog in, same
    text out — which is what the in-sync doc test relies on."""
    lines = [
        "# Order Types",
        "",
        "> Generated from `src/governor/gate/order_catalog.py` — do not edit by hand.",
        "> Regenerate with `python -m governor.gate.order_catalog`.",
        "",
        f"{len(CATALOG)} selectable order types for the pre-trade gate. Pick one with "
        "`--type` (and `--adaptive` for the Adaptive variants) on "
        "`python -m governor.gate analyze …`. **Required** flags are in bold; the rest "
        "are optional modifiers. Discover the same table from the CLI with "
        "`python -m governor.gate order-types`.",
        "",
        "| Order type | `--type` | Required | Optional | What it does | When to use |",
        "|------------|----------|----------|----------|--------------|-------------|",
    ]
    for e in CATALOG:
        req = "<br>".join(f"`{p}`" for p in e.required) or "—"
        opt = "<br>".join(f"`{p}`" for p in e.optional) or "—"
        adaptive_note = " (`--adaptive`)" if e.adaptive else ""
        type_cell = f"`{_TYPE_FLAG[e.base]}`{adaptive_note}"
        lines.append(
            f"| **{e.name}** (`{e.key}`) | {type_cell} | {req} | {opt} | "
            f"{e.description} | {e.when_to_use} |"
        )
    lines += [
        "",
        "## Cross-cutting capabilities",
        "",
        "These layer on *any* entry above — they are not separate order types.",
        "",
        "### Adaptive (IBKR IBALGO)",
        "",
        "`--adaptive` runs a **Market** or **Limit** order through IBKR's Adaptive "
        "algo for better average fills; it is a *modifier*, not a base type "
        "(`order.orderType` stays MKT/LMT). It is **invalid on Stop / Stop-Limit** "
        "(TWS rejects it). Tune its aggression with `--priority`:",
        "",
        f"- **Priorities:** {_priorities()} (default **{AdaptivePriority.NORMAL.value}**). "
        "Urgent leans toward immediacy; Patient leans toward price.",
        "",
        "### Bracketing (attached protective legs)",
        "",
        "Add `--stop-loss PX` and/or `--take-profit PX` to *any entry* to attach "
        "protective child orders. They are OCA-grouped (one cancels the other) and "
        "placed GTC by default (see TIF) so a filled entry is never left unprotected "
        "after the entry's own session ends.",
        "",
        "### Time-in-force (TIF)",
        "",
        f"`--tif` sets the **entry's** lifetime ({' / '.join(TIF_CHOICES)}; default "
        "**DAY**). `--protective-tif` sets the bracket children's lifetime (default "
        "**GTC** so protective stops outlive the session — a DAY protective stop would "
        "be cancelled by TWS at the close, leaving the fill unprotected overnight).",
        "",
    ]
    return "\n".join(lines)


def render_table() -> str:
    """Render the catalog as a clean, plain-text table for the CLI `order-types`
    subcommand. No Markdown pipes — just aligned, scannable columns plus the
    cross-cutting notes. Pure (no I/O)."""
    header = "Pre-trade gate — selectable order types"
    rule = "=" * len(header)
    lines = [header, rule, ""]
    for e in CATALOG:
        flag = e.cli
        lines.append(f"  {e.name}  ({e.key})")
        lines.append(f"      flags : {flag}")
        params = _params_plain(e)
        if params:
            lines.append(f"      params: {params}")
        lines.append(f"      what  : {e.description}")
        lines.append(f"      when  : {e.when_to_use}")
        lines.append("")
    lines += [
        "Cross-cutting modifiers (layer on any type above):",
        "",
        "  Adaptive   --adaptive on MARKET/LIMIT only (rejected on stop/stop-limit).",
        f"             --priority {{{', '.join(p.value.lower() for p in AdaptivePriority)}}}"
        f"  (default {AdaptivePriority.NORMAL.value.lower()}; "
        f"priorities: {_priorities()}).",
        "  Bracket    --stop-loss PX / --take-profit PX attach OCA-grouped protective",
        "             legs to any entry (placed GTC so they outlive the session).",
        f"  TIF        --tif {{{', '.join(t.lower() for t in TIF_CHOICES)}}} sets the entry's "
        f"lifetime (default DAY);",
        "             --protective-tif sets the bracket legs' lifetime (default GTC).",
        "",
    ]
    return "\n".join(lines)


def _params_plain(e: OrderTypeSpec) -> str:
    """Plain-text required + optional params for the CLI table."""
    parts = []
    if e.required:
        parts.append("required " + " ".join(e.required))
    if e.optional:
        parts.append("optional " + " ".join(e.optional))
    return "; ".join(parts)


def main() -> None:
    """Write docs/ORDER_TYPES.md from the catalog. Bypasses the Write-tool path so
    the doc is unambiguously generated, never hand-authored — exactly like
    `python -m governor.rules.catalog`."""
    from pathlib import Path

    doc = Path(__file__).resolve().parents[3] / "docs" / "ORDER_TYPES.md"
    doc.write_text(render_markdown())
    print(f"wrote {doc} ({len(CATALOG)} order types)")


if __name__ == "__main__":
    main()
