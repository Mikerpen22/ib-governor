"""The read-only natural-language ask lane: intent split + deterministic
quick-answers. Both are pure — fed plain text and a hand-built account `view`.
"""
from __future__ import annotations

import pytest

from governor.comms.ask import Intent, classify_message, quick_answer

_VIEW = {
    "date": "2026-06-23",
    "nav": 250_000.0,
    "margin_cushion": 0.45,
    "gross_leverage": 1.80,
    "realized_pnl_today": 1200.0,
    "fills": [
        {"symbol": "NVDA", "sec_type": "STK", "side": "BOT", "shares": 100, "price": 120.0,
         "realized_pnl": 0.0, "time": ""},
        {"symbol": "MNQ", "sec_type": "FUT", "side": "SLD", "shares": 2, "price": 21000.0,
         "realized_pnl": 1200.0, "time": ""},
    ],
    "positions": [
        {"symbol": "NVDA", "sec_type": "STK", "position": 100, "market_value": 12000.0,
         "unrealized_pnl": 300.0},
        {"symbol": "MNQ", "sec_type": "FUT", "position": -2, "market_value": 0.0,
         "unrealized_pnl": -150.0},
    ],
}

_PNL_VIEW = {
    "nav": 341_756.0,
    "realized_pnl_today": 265.0,
    "positions": [],
    "pnl": {"daily": -3637.0, "realized": 265.0, "unrealized": -9099.0},
}


# --- classify_message ---------------------------------------------------------

@pytest.mark.parametrize("text,intent", [
    ("buy 100 ORCL", Intent.ORDER),
    ("sell 50 SNAP at market", Intent.ORDER),
    ("grab 2 micro nasdaq", Intent.ORDER),
    ("trim 1 MNQ", Intent.ORDER),
    ("should I buy SNAP?", Intent.ASK),       # '?' wins even with a verb
    ("what's my leverage", Intent.ASK),
    ("leverage", Intent.ASK),
    ("how am I doing today", Intent.ASK),
    ("positions", Intent.ASK),
    ("", Intent.ASK),
])
def test_classify_message(text, intent):
    assert classify_message(text) is intent


# --- quick_answer: recognized questions ---------------------------------------

def test_leverage_answer_reports_ratio_and_futures_caveat():
    s = quick_answer("what's my leverage?", _VIEW)
    assert s is not None
    assert "1.80×" in s and "Leverage" in s
    assert "futures notional" in s             # MNQ position → economic-exposure caveat


def test_leverage_answer_omits_futures_caveat_when_flat_of_futures():
    view = {**_VIEW, "positions": [_VIEW["positions"][0]]}   # NVDA only
    s = quick_answer("leverage", view)
    assert "1.80×" in s and "futures notional" not in s


def test_cushion_answer_reports_percent():
    s = quick_answer("how's my margin cushion", _VIEW)
    assert s is not None and "45%" in s


def test_pnl_panel_shows_daily_realized_unrealized_with_amount_and_pct():
    s = quick_answer("how am I doing", _PNL_VIEW)
    assert s is not None
    assert "Daily" in s and "Realized" in s and "Unrealized" in s
    assert "-$3,637" in s and "+$265" in s and "-$9,099" in s
    # % of NAV: -3637/341756 = -1.06%, 265/341756 = +0.08%, -9099/341756 = -2.66%
    assert "-1.06%" in s and "+0.08%" in s and "-2.66%" in s
    assert "🔴" in s  # daily < 0 → red mood


def test_pnl_panel_missing_field_renders_na():
    view = {**_PNL_VIEW, "pnl": {"daily": None, "realized": 265.0, "unrealized": -9099.0}}
    s = quick_answer("pnl", view)
    assert "n/a" in s and "+$265" in s


def test_pnl_panel_omits_percent_when_nav_nonpositive():
    view = {**_PNL_VIEW, "nav": 0.0}
    s = quick_answer("pnl", view)
    assert "-$3,637" in s and "%" not in s


def test_pnl_falls_back_when_pnl_absent():
    """No reqPnL data (pnl all-None) → labeled fallback, never blank, never
    'so far today' on a cumulative number."""
    view = {"nav": 250_000.0, "realized_pnl_today": 1200.0,
            "positions": [{"unrealized_pnl": 150.0}],
            "pnl": {"daily": None, "realized": None, "unrealized": None}}
    s = quick_answer("how am I doing", view)
    assert s is not None
    assert "+$1,200" in s              # realized today
    assert "Open book" in s            # cumulative, honestly labeled
    assert "so far today" not in s     # the old bug must not return


def test_today_answer_lists_fills():
    s = quick_answer("what did I trade today", _VIEW)
    assert s is not None
    assert "2 fill" in s and "NVDA" in s and "MNQ" in s


def test_positions_answer_lists_book_sorted_by_size():
    s = quick_answer("show my book", _VIEW)
    assert s is not None and "Book" in s
    assert s.index("NVDA") < s.index("MNQ")     # larger market value first


def test_positions_answer_when_flat():
    s = quick_answer("positions", {**_VIEW, "positions": []})
    assert s is not None and "Flat" in s


def test_today_answer_when_no_fills():
    s = quick_answer("trades today", {**_VIEW, "fills": []})
    assert s is not None and "No trades" in s


# --- quick_answer: misses fall through (None) ---------------------------------

def test_unrecognized_question_returns_none():
    assert quick_answer("what do you think about gold", _VIEW) is None
    assert quick_answer("tell me a joke", _VIEW) is None
