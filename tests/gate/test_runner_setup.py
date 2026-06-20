# tests/gate/test_runner_setup.py
"""Phase 2 — Task 7: assert that analyze_intent's preview now contains a 'setup' block.

Reuses the FakeIB harness from test_runner.py, extending it with a
reqHistoricalData method that returns synthetic rising daily bars so the equity
Stage-2 assessment can compute with available=True.
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from governor.config import RulesConfig
from governor.gate.analysis import Verdict
from governor.gate.intent import Action, OrderIntent, OrderType, SecType
from governor.model import StateSnapshot

# Import the shared fakes from test_runner — these are the established harness.
from tests.gate.test_runner import (
    FakeIB,
    FakeLockoutStore,
    _NOW,
    _snap,
    _stk_intent,
)

import governor.gate.runner as runner


# ---------------------------------------------------------------------------
# Extended FakeIB with reqHistoricalData
# ---------------------------------------------------------------------------

def _rising_bars(n: int = 260, start: float = 50.0, step: float = 0.5):
    """~260 rising daily bars as SimpleNamespace objects (what reqHistoricalData returns).

    Each bar is open=close=c, high=c+1, low=c-1 — simple monotone uptrend so
    Stage-2 runs 'confirmed' (price well above all MAs; slope up; near 52-week high).
    """
    bars = []
    for i in range(n):
        c = start + i * step
        bars.append(SimpleNamespace(
            date=f"2026-01-{(i % 28) + 1:02d}",
            open=c, high=c + 1.0, low=c - 1.0, close=c, volume=100_000.0,
        ))
    return bars


def _falling_bars(n: int = 260, start: float = 180.0, step: float = -0.5):
    """~260 falling daily bars — clear Stage-2 failure (downtrend)."""
    bars = []
    for i in range(n):
        c = max(start + i * step, 0.01)
        bars.append(SimpleNamespace(
            date=f"2026-01-{(i % 28) + 1:02d}",
            open=c, high=c + 1.0, low=c - 1.0, close=c, volume=100_000.0,
        ))
    return bars


class SetupFakeIB(FakeIB):
    """FakeIB that also implements reqHistoricalData for the setup bar fetch.

    bar_factory: callable() -> list[SimpleNamespace]
    Defaults to 260 rising bars (equity confirmed Stage 2).
    """

    def __init__(self, bar_factory=None):
        super().__init__()
        self._bar_factory = bar_factory or _rising_bars

    def reqHistoricalData(self, contract, *, endDateTime="", durationStr="",
                          barSizeSetting="", whatToShow="", useRTH=True, **kwargs):
        return self._bar_factory()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPreviewIncludesSetup:
    """Verify the preview dict carries a 'setup' key after Phase-2 wiring."""

    def test_preview_includes_setup(self):
        ib = SetupFakeIB()
        verdict, preview = runner.analyze_intent(
            ib, _stk_intent(qty=1.0), _snap(), RulesConfig(),
            FakeLockoutStore(), now=_NOW,
        )
        assert "setup" in preview

    def test_setup_available_true_for_uptrend_stk(self):
        ib = SetupFakeIB(bar_factory=_rising_bars)
        _, preview = runner.analyze_intent(
            ib, _stk_intent(qty=1.0), _snap(), RulesConfig(),
            FakeLockoutStore(), now=_NOW,
        )
        assert preview["setup"]["available"] is True

    def test_setup_asset_class_equity_for_stk(self):
        ib = SetupFakeIB(bar_factory=_rising_bars)
        _, preview = runner.analyze_intent(
            ib, _stk_intent(qty=1.0), _snap(), RulesConfig(),
            FakeLockoutStore(), now=_NOW,
        )
        assert preview["setup"]["asset_class"] == "equity"

    def test_setup_available_false_when_hist_errors(self):
        """If reqHistoricalData raises, fetch_daily_bars returns None -> available=False."""
        class ErrorHistIB(FakeIB):
            def reqHistoricalData(self, *a, **k):
                raise RuntimeError("TWS disconnected")

        ib = ErrorHistIB()
        _, preview = runner.analyze_intent(
            ib, _stk_intent(qty=1.0), _snap(), RulesConfig(),
            FakeLockoutStore(), now=_NOW,
        )
        assert preview["setup"]["available"] is False

    def test_setup_block_is_json_serializable(self):
        """The setup dict must be JSON-serializable (no dataclasses, no Bar objects)."""
        import json
        ib = SetupFakeIB()
        _, preview = runner.analyze_intent(
            ib, _stk_intent(qty=1.0), _snap(), RulesConfig(),
            FakeLockoutStore(), now=_NOW,
        )
        # Must not raise
        serialized = json.dumps(preview["setup"])
        parsed = json.loads(serialized)
        assert "available" in parsed


# ---------------------------------------------------------------------------
# Task 10 tests — setup wired into GateFacts / verdict
# ---------------------------------------------------------------------------

class TestSetupWiredIntoVerdict:
    """Verify that setup now flows through GateFacts and changes the verdict."""

    def test_downtrend_buy_is_caution_via_setup(self):
        """A BUY into a clear downtrend stock escalates to CAUTION (Stage-2 reason)."""
        ib = SetupFakeIB(bar_factory=_falling_bars)
        _, preview = runner.analyze_intent(
            ib, _stk_intent(qty=1.0), _snap(), RulesConfig(),
            FakeLockoutStore(), now=_NOW,
        )
        assert preview["verdict"] == "CAUTION"
        assert any("Stage 2" in r for r in preview["reasons"])

    def test_uptrend_buy_stays_go_when_no_risk_flags(self):
        """A BUY into a confirmed uptrend with no risk flags stays GO."""
        ib = SetupFakeIB(bar_factory=_rising_bars)
        _, preview = runner.analyze_intent(
            ib, _stk_intent(qty=1.0), _snap(), RulesConfig(),
            FakeLockoutStore(), now=_NOW,
        )
        assert preview["verdict"] == "GO"


# ---------------------------------------------------------------------------
# Task 13 tests — preview carries a "panels" key
# ---------------------------------------------------------------------------


class TestPreviewIncludesPanels:
    """Verify analyze_intent adds preview['panels'] = render_panels(preview) (str)."""

    def test_panels_key_present(self):
        ib = SetupFakeIB()
        _, preview = runner.analyze_intent(
            ib, _stk_intent(qty=1.0), _snap(), RulesConfig(),
            FakeLockoutStore(), now=_NOW,
        )
        assert "panels" in preview

    def test_panels_is_str(self):
        ib = SetupFakeIB()
        _, preview = runner.analyze_intent(
            ib, _stk_intent(qty=1.0), _snap(), RulesConfig(),
            FakeLockoutStore(), now=_NOW,
        )
        assert isinstance(preview["panels"], str)

    def test_panels_contains_three_section_headers(self):
        ib = SetupFakeIB()
        _, preview = runner.analyze_intent(
            ib, _stk_intent(qty=1.0), _snap(), RulesConfig(),
            FakeLockoutStore(), now=_NOW,
        )
        panels = preview["panels"]
        assert "ORDER" in panels
        assert "RISK" in panels
        assert "SETUP" in panels

    def test_panels_not_empty(self):
        ib = SetupFakeIB()
        _, preview = runner.analyze_intent(
            ib, _stk_intent(qty=1.0), _snap(), RulesConfig(),
            FakeLockoutStore(), now=_NOW,
        )
        assert len(preview["panels"]) > 50
