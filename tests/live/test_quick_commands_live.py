"""Real-IBKR correctness of the Telegram slash-command pipeline.

The slash commands (/leverage, /pnl, /cushion, /positions, /today) are answered
by the daemon's quick-answer fast-path:

    /leverage -> collect_account_view(self.ib)  [data fetch, governor.live.daily]
              -> account_metrics(accountValues) [calc,       governor.live.snapshot]
              -> quick_answer / _fmt_leverage    [render,     governor.comms.ask]

The unit suites cover the calc + render with hand-built views (deterministic, good)
but never against a real account. These tests close that gap: they fetch from a
real TWS and verify the fetched numbers and the derived ratios are CORRECT against
the broker — by re-deriving the metrics independently from the raw account rows,
cross-checking the separate accountSummary endpoint, and confirming the rendered
slash-command answer carries the right figure. A mock can't catch a wrong tag, a
currency mix-up, or a flipped ratio against real multi-currency account data.

Read-only, distinct client id (runs alongside the live daemon on clientId 4).
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from governor.comms.ask import quick_answer
from governor.config import load_config
from governor.live.connection import BrakeConnection
from governor.live.daemon import _QUICK_COMMANDS
from governor.live.daily import collect_account_view, fetch_account_pnl

pytestmark = pytest.mark.integration

ET = ZoneInfo("America/New_York")
_CONFIG = "config/rules.yaml"
_TEST_CLIENT_ID = 16  # clear of daemon=4/gate=5/daily=6/technicals=7, reads 11/12/15, paper 13


@pytest.fixture(scope="module")
def ib():
    cfg = load_config(_CONFIG)
    test_live = cfg.live.model_copy(update={"client_id": _TEST_CLIENT_ID, "readonly": True})
    c = BrakeConnection(test_live)
    try:
        c.connect()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"TWS not reachable: {exc}")
    yield c.ib
    c.disconnect()


def _pick(rows, tag: str) -> float | None:
    """Independent re-implementation of account_metrics' tag pick (BASE→USD→first),
    so a bug in the production picker can't hide behind itself. Returns None if the
    tag is absent or unparseable."""
    matching = [r for r in rows if getattr(r, "tag", None) == tag]
    for cur in ("BASE", "USD"):
        for r in matching:
            if getattr(r, "currency", None) == cur:
                try:
                    return float(r.value)
                except (TypeError, ValueError):
                    return None
    for r in matching:
        try:
            return float(r.value)
        except (TypeError, ValueError):
            continue
    return None


def test_collect_account_view_metrics_are_correct_vs_real_broker(ib):
    """nav/cushion/leverage from collect_account_view must equal the values
    independently re-derived from the raw account rows the broker returned —
    proving the fetch reads the right tags and the calc applies the right ratios
    on real (multi-currency) data. Plus internal self-consistency + plausibility."""
    cfg = load_config(_CONFIG)
    now = dt.datetime.now(tz=ET)
    view = collect_account_view(ib, cfg, now)

    # --- independent re-derivation from the SAME real account rows -------------
    rows = ib.accountValues()
    nav = _pick(rows, "NetLiquidation")
    excess = _pick(rows, "ExcessLiquidity")
    gross = _pick(rows, "GrossPositionValue")
    assert nav and nav > 0, "real account should report a positive NetLiquidation"

    assert view["nav"] == pytest.approx(nav, rel=1e-9), "fetched NAV != broker NetLiquidation"
    if excess is not None:
        assert view["margin_cushion"] == pytest.approx(excess / nav, rel=1e-9), \
            "margin_cushion must be ExcessLiquidity / NAV"
    if gross is not None:
        assert view["gross_leverage"] == pytest.approx(gross / nav, rel=1e-9), \
            "gross_leverage must be GrossPositionValue / NAV"

    # --- cross-check the SEPARATE accountSummary endpoint (when populated) -----
    try:
        summary = ib.accountSummary()
    except Exception:  # noqa: BLE001
        summary = []
    nav_sum = _pick(summary, "NetLiquidation") if summary else None
    if nav_sum is not None:
        # Same broker truth via a different IBKR endpoint — should agree closely.
        assert view["nav"] == pytest.approx(nav_sum, rel=0.01), \
            "NAV disagrees between accountValues and accountSummary endpoints"

    # --- plausibility (catches a units/ratio blunder a fixture wouldn't) -------
    assert 0.0 <= view["margin_cushion"] <= 5.0, view["margin_cushion"]
    assert 0.0 <= view["gross_leverage"] <= 20.0, view["gross_leverage"]

    # --- realized P&L self-consistency: the scalar equals the sum of the fills -
    fills_sum = sum(float(f["realized_pnl"]) for f in view["fills"])
    assert view["realized_pnl_today"] == pytest.approx(fills_sum, rel=1e-9, abs=1e-6), \
        "realized_pnl_today must equal the sum of today's serialized fill P&Ls"

    # --- positions are well-formed numeric rows -------------------------------
    for p in view["positions"]:
        assert isinstance(p["position"], (int, float))
        assert isinstance(p["market_value"], (int, float))
        assert isinstance(p["unrealized_pnl"], (int, float))


def test_slash_commands_render_correct_figures_from_real_data(ib):
    """End-to-end: a real account view through the actual slash-command mapping
    produces an answer carrying the SAME figure the view holds — so what the user
    reads in Telegram matches the broker, not just an internal dict."""
    cfg = load_config(_CONFIG)
    view = collect_account_view(ib, cfg, dt.datetime.now(tz=ET))

    # /leverage → "<lev>×"
    lev_answer = quick_answer(_QUICK_COMMANDS["/leverage"], view)
    assert lev_answer is not None
    assert f"{view['gross_leverage']:.2f}×" in lev_answer, lev_answer

    # /cushion → "<cushion>%"
    cushion_answer = quick_answer(_QUICK_COMMANDS["/cushion"], view)
    assert cushion_answer is not None
    assert f"{view['margin_cushion']:.0%}" in cushion_answer, cushion_answer

    # /pnl → net = realized_today + Σ open unrealized, formatted as whole dollars
    pnl_answer = quick_answer(_QUICK_COMMANDS["/pnl"], view)
    assert pnl_answer is not None
    realized = float(view["realized_pnl_today"])
    unrealized = sum(float(p.get("unrealized_pnl", 0.0) or 0.0) for p in view["positions"])
    net = realized + unrealized
    expected = f"${abs(net):,.0f}"
    expected = f"-{expected}" if net < 0 else expected
    # The new panel only falls back to this net when reqPnL is unavailable.
    if view.get("pnl", {}).get("daily") is None:
        assert expected in pnl_answer, f"expected fallback net {expected!r} in: {pnl_answer!r}"


def test_pnl_panel_matches_real_reqpnl(ib):
    """fetch_account_pnl mirrors a fresh reqPnL read, and the rendered /pnl panel
    carries the real daily figure + the correct % of NAV."""
    account = ib.managedAccounts()[0]
    pnl = ib.reqPnL(account)
    ib.sleep(2.0)  # let the subscription settle before reading
    if pnl.dailyPnL != pnl.dailyPnL:  # nan -> not settled on this connection
        pytest.skip("reqPnL did not settle a daily figure on this connection")

    got = fetch_account_pnl(ib, account)
    assert got["daily"] == pytest.approx(pnl.dailyPnL, rel=1e-6)

    cfg = load_config(_CONFIG)
    view = collect_account_view(ib, cfg, dt.datetime.now(tz=ET))
    answer = quick_answer(_QUICK_COMMANDS["/pnl"], view)
    assert answer is not None
    daily = view["pnl"]["daily"]
    assert daily is not None, "warm reqPnL should give a daily figure in the view"
    # whole-dollar signed form, exactly as _signed_usd renders it
    s = f"${abs(daily):,.0f}"
    expected = f"+{s}" if daily > 0 else (f"-{s}" if daily < 0 else s)
    assert expected in answer, f"panel missing daily {expected!r}: {answer!r}"
    if view["nav"] > 0:
        assert f"{daily / view['nav']:+.2%}" in answer
