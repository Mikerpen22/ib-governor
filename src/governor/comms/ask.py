"""The natural-language "ask" lane — read-only.

Two pure pieces, both trivially unit-tested:

  classify_message(text) -> Intent
      Split a non-confirm message into ORDER vs ASK. A misclassification is NOT a
      safety event: the order lane only *stages* (placement still needs an explicit
      confirm) and the ask lane is strictly read-only — so this is a cheap, tunable
      heuristic, not a gate.

  quick_answer(text, view) -> str | None
      Answer a handful of high-frequency factual questions (leverage, margin
      cushion, P&L, today's trades, positions) directly from an account `view`
      dict — the connection-cheap subset of the daily collector
      (governor.live.daily.collect_account_view). The live daemon already holds
      this data streamed, so these answers come back in well under a second with
      no subprocess and no new TWS socket. Returns None when nothing matches, so
      the caller falls through to the slower ask agent.

Output is Telegram-HTML in the house style (see governor.comms.format).
"""
from __future__ import annotations

from enum import Enum

from .format import b, code, header, i, joinsections, pre, section


class Intent(str, Enum):
    ORDER = "order"   # "buy 100 ORCL" — propose/stage a trade
    ASK = "ask"       # "what's my leverage?" — a read-only question


# Leading verbs that mark an order. Kept deliberately small; the '?'-test and the
# read-only safety of both lanes mean we don't need this to be exhaustive.
_ORDER_VERBS = frozenset({
    "buy", "sell", "short", "long", "grab", "trim", "add", "close", "cover",
    "bought", "sold", "purchase", "scale", "flatten", "unload", "dump",
})


def classify_message(text: str) -> Intent:
    """ORDER vs ASK for a non-confirm message. A trailing/leading '?' or a
    non-order leading word reads as a question; a leading trade verb reads as an
    order."""
    t = text.strip().lower()
    if not t:
        return Intent.ASK
    if "?" in t:                       # an explicit question, even "should I buy SNAP?"
        return Intent.ASK
    first = t.split()[0].strip(".,!:;")
    return Intent.ORDER if first in _ORDER_VERBS else Intent.ASK


# --- quick-answer keyword groups (tried in this order; first hit wins) ---------

_LEVERAGE = ("leverage", "levered", "leveraged", "gearing", "geared")
_CUSHION = ("cushion", "margin", "buying power", "excess liquid")
_PNL = ("pnl", "p&l", "p and l", "how am i doing", "doing today", "how'm",
        "making money", "made money", "up or down", "green or red", "profit",
        "how much have i made", "how much did i make", "am i up", "am i down")
_TODAY_ACTIVITY = ("trade", "traded", "trades", "did i do", "activity",
                   "what happened", "do today", "done today")
_POSITIONS = ("position", "book", "holding", "what do i hold", "portfolio",
              "what am i holding", "in my account", "what i own")


def _has(t: str, kws: tuple[str, ...]) -> bool:
    return any(k in t for k in kws)


def quick_answer(text: str, view: dict) -> str | None:
    """A deterministic HTML answer for a recognized quick question, else None."""
    t = text.strip().lower()
    if _has(t, _LEVERAGE):
        return _fmt_leverage(view)
    if _has(t, _CUSHION):
        return _fmt_cushion(view)
    if _has(t, _PNL):
        return _fmt_pnl(view)
    if "fills" in t or ("today" in t and _has(t, _TODAY_ACTIVITY)):
        return _fmt_today(view)
    if _has(t, _POSITIONS):
        return _fmt_positions(view)
    return None


# --- formatting helpers --------------------------------------------------------

def _usd(x: float) -> str:
    """'$1,234' / '-$1,234' — whole dollars, the unit a trader scans fastest."""
    s = f"${abs(float(x)):,.0f}"
    return f"-{s}" if float(x) < 0 else s


def _signed_usd(x: float) -> str:
    """'+$265' / '-$3,637' / '$0' — _usd with an explicit + for positives."""
    return f"+{_usd(x)}" if float(x) > 0 else _usd(x)


def _mood(amount: float | None) -> str:
    """🟢 when non-negative (None treated as flat), else 🔴."""
    return "🟢" if (amount if amount is not None else 0.0) >= 0 else "🔴"


_PNL_ROWS = (("Daily", "daily"), ("Realized", "realized"), ("Unrealized", "unrealized"))


def _qty(x: float) -> str:
    x = float(x)
    return str(int(x)) if x.is_integer() else f"{x:g}"


def _open_positions(view: dict) -> list[dict]:
    return [p for p in view.get("positions", []) if float(p.get("position", 0) or 0)]


def _fmt_leverage(view: dict) -> str:
    lev = float(view.get("gross_leverage", 0.0))
    nav = float(view.get("nav", 0.0))
    body = [f"Gross leverage {b(f'{lev:.2f}×')} — {code(_usd(lev * nav))} gross "
            f"exposure on {code(_usd(nav))} NAV."]
    if any(p.get("sec_type") == "FUT" and float(p.get("position", 0) or 0)
           for p in view.get("positions", [])):
        body.append(i("Note: futures notional may sit outside IBKR's gross position "
                      "value — your true economic exposure can be higher."))
    return section(header("📊", "Leverage"), body)


def _fmt_cushion(view: dict) -> str:
    c = float(view.get("margin_cushion", 0.0))
    return joinsections(
        header("🛡️", "Margin cushion"),
        f"Excess liquidity is {b(f'{c:.0%}')} of NAV.",
    )


def _fmt_pnl(view: dict) -> str:
    """Daily / Realized / Unrealized, each in $ and % of NAV, in an aligned
    monospace panel. Daily is today's true account MTM (reqPnL.dailyPnL); the
    three are distinct views, NOT addends. Degrades to a labeled fallback when
    live P&L is unavailable, so the answer is never blank or misleading."""
    pnl = view.get("pnl") or {}
    nav = float(view.get("nav", 0.0) or 0.0)
    values = {key: pnl.get(key) for _, key in _PNL_ROWS}
    if all(v is None for v in values.values()):
        return _fmt_pnl_fallback(view)

    daily = values["daily"]
    mood = _mood(daily)

    rows: list[tuple[str, str, str]] = []
    for label, key in _PNL_ROWS:
        v = values[key]
        if v is None:
            rows.append((label, "n/a", ""))
        else:
            pct = f"{v / nav:+.2%}" if nav > 0 else ""
            rows.append((label, _signed_usd(v), pct))

    lw = max(len(r[0]) for r in rows)
    dw = max(len(r[1]) for r in rows)
    pw = max(len(r[2]) for r in rows)
    table = "\n".join(
        f"{label.ljust(lw)}  {dollar.rjust(dw)}  {pct.rjust(pw)}".rstrip()
        for label, dollar, pct in rows
    )
    footer = i(f"% of NAV {_usd(nav)}") if nav > 0 else ""
    return joinsections(header(mood, "P&L — today"), pre(table), footer)


def _fmt_pnl_fallback(view: dict) -> str:
    """No live reqPnL data: show realized-today + the cumulative open book, each
    HONESTLY labeled (no 'so far today' on the cumulative number)."""
    realized = float(view.get("realized_pnl_today", 0.0) or 0.0)
    open_book = sum(float(p.get("unrealized_pnl", 0.0) or 0.0) for p in view.get("positions", []))
    mood = _mood(realized)
    return section(header(mood, "P&L"), [
        f"Realized today {b(_signed_usd(realized))}.",
        f"Open book {b(_signed_usd(open_book))} {i('(unrealized, all positions)')}.",
        i("Live daily P&L unavailable right now."),
    ])


def _fmt_today(view: dict) -> str:
    fills = view.get("fills", [])
    if not fills:
        return joinsections(header("📒", "Today"), i("No trades yet today."))
    realized = float(view.get("realized_pnl_today", 0.0))
    lines = []
    for f in fills[:15]:
        side = str(f.get("side", "")).upper()
        lines.append(f"{b(f.get('symbol', '?'))} {side} {code(_qty(f.get('shares', 0)))} "
                     f"@ {_usd(f.get('price', 0))}")
    head = header("📒", f"Today — {len(fills)} fill(s), realized {_usd(realized)}")
    return section(head, lines)


def _fmt_positions(view: dict) -> str:
    positions = _open_positions(view)
    if not positions:
        return joinsections(header("💰", "Book"), i("Flat — no open positions."))
    lines = []
    for p in sorted(positions, key=lambda x: -abs(float(x.get("market_value", 0) or 0)))[:15]:
        upnl = float(p.get("unrealized_pnl", 0.0) or 0.0)
        sign = "+" if upnl >= 0 else ""
        lines.append(f"{b(p.get('symbol', '?'))} {code(_qty(p.get('position', 0)))} · "
                     f"{_usd(p.get('market_value', 0))} · {sign}{_usd(upnl)}")
    return section(header("💰", "Book"), lines)
