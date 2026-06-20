"""Tests for gate/render.py — pure panel renderer (dict in, markdown str out).

Task 12 (Phase 5): render_panels emits exactly the three middle panels
(ORDER / RISK & SIZING / SETUP) and nothing else (no banner, no verdict
paragraph, no vault, no confirm line — those belong to the skill).
"""
from __future__ import annotations

import pytest

from governor.gate.render import render_panels


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _equity_preview(**over):
    """Build a representative equity preview dict."""
    base = {
        "symbol": "ORCL",
        "action": "buy",
        "quantity": 50,
        "order_type": "market",
        "order_notional": 7260.0,
        "pct_nav": 0.02,
        "buying_power_ok": True,
        "whatif_available": True,
        "init_margin": 3630.0,
        "name_weight_before": 0.041,
        "name_weight_after": 0.058,
        "trips": [],
        "verdict": "CAUTION",
        "reasons": ["trade is 2.0% of NAV (over the sizing band)"],
        "setup": {
            "available": True,
            "asset_class": "equity",
            "poor": True,
            "caution_reasons": ["setup: not a confirmed Stage 2 (5/7)"],
            "equity": {
                "stage2": {
                    "classification": "candidate",
                    "pass_count": 5,
                    "position_pct": 0.68,
                    "range_ratio": 1.9,
                    "slope_up": True,
                    "ma50": 140.0,
                    "ma150": 135.0,
                    "ma200": 130.0,
                    "price": 145.2,
                    "criteria": [
                        ["price > MA50", True],
                        ["price > MA150", True],
                        ["price > MA200", True],
                        ["MA50>MA150>MA200", True],
                        ["MA200 rising", True],
                        ["52wk position", False],
                        ["range >= ratio", True],
                    ],
                },
                "vcp": {
                    "available": True,
                    "pivot": 147.8,
                    "distance_pct": 0.07,
                    "distance_band": "extended",
                    "last_contraction_pct": 0.06,
                    "last_grade": "excellent",
                    "volume_dryup": True,
                    "contractions": [],
                },
                "extended": True,
            },
        },
    }
    base.update(over)
    return base


def _futures_preview(**over):
    """Build a representative futures preview dict."""
    base = {
        "symbol": "MNQ",
        "action": "sell",
        "quantity": 2,
        "order_type": "market",
        "order_notional": 42000.0,
        "pct_nav": 0.11,
        "buying_power_ok": True,
        "whatif_available": True,
        "init_margin": 3200.0,
        "name_weight_before": 0.0,
        "name_weight_after": 0.11,
        "trips": [],
        "verdict": "CAUTION",
        "reasons": ["setup: counter-trend (uptrend)"],
        "setup": {
            "available": True,
            "asset_class": "future",
            "poor": True,
            "caution_reasons": ["setup: counter-trend (uptrend)"],
            "futures": {
                "with_trend": False,
                "counter_trend": True,
                "trend_label": "uptrend",
                "atr": 85.0,
                "atr_pctile": 0.78,
                "vol_label": "normal",
                "vol_expanding": True,
                "vol_elevated": False,
                "dist_from_high_pct": 0.004,
                "dist_from_low_pct": 0.12,
                "chasing": False,
                "rsi": 71.0,
                "roc": 0.022,
                "momentum_label": "overbought",
            },
        },
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# Task 12 core tests — three panels always present
# ---------------------------------------------------------------------------


class TestRenderPanelsThreePanels:
    """render_panels always emits ORDER, RISK, and SETUP panels."""

    def test_renders_order_panel(self):
        out = render_panels(_equity_preview())
        assert "ORDER" in out

    def test_renders_risk_panel(self):
        out = render_panels(_equity_preview())
        assert "RISK" in out

    def test_renders_setup_panel(self):
        out = render_panels(_equity_preview())
        assert "SETUP" in out

    def test_renders_symbol_in_output(self):
        out = render_panels(_equity_preview())
        assert "ORCL" in out

    def test_renders_stage2_header(self):
        out = render_panels(_equity_preview())
        assert "Stage 2" in out


class TestEquitySetupPanel:
    """Equity SETUP panel matches the spec mockup."""

    def test_stage2_classification_and_pass_count_in_header(self):
        out = render_panels(_equity_preview())
        # Header: "Stage 2: 5/7 · candidate"
        assert "5/7" in out
        assert "candidate" in out

    def test_ma_stack_line(self):
        out = render_panels(_equity_preview())
        # MA check lines should appear
        assert "MA50" in out or "MA" in out

    def test_range_ratio_rendered_as_nx(self):
        out = render_panels(_equity_preview())
        # range_ratio 1.9 must appear as "1.9x", NOT "190%"
        assert "1.9x" in out
        assert "190%" not in out

    def test_vcp_pivot_line_shown(self):
        out = render_panels(_equity_preview())
        assert "147.80" in out or "147.8" in out

    def test_vcp_distance_pct_shown(self):
        out = render_panels(_equity_preview())
        # +7% extended
        assert "7%" in out or "+7%" in out

    def test_position_pct_shown(self):
        out = render_panels(_equity_preview())
        # 68% 52-week position
        assert "68%" in out

    def test_extended_glyph_shown(self):
        out = render_panels(_equity_preview())
        # extended pivot -> ⚠️ or 🟡 glyph
        assert "⚠️" in out or "🟡" in out

    def test_no_banner_in_output(self):
        """render_panels must NOT emit the banner line (that is the skill's job)."""
        out = render_panels(_equity_preview())
        assert "CAUTION" not in out
        assert "GO" not in out
        assert "BLOCK" not in out

    def test_no_verdict_paragraph(self):
        """render_panels must NOT emit the 🧭 VERDICT section."""
        out = render_panels(_equity_preview())
        assert "🧭" not in out

    def test_no_vault_section(self):
        """render_panels must NOT emit the 📓 VAULT section."""
        out = render_panels(_equity_preview())
        assert "📓" not in out

    def test_no_confirm_line(self):
        """render_panels must NOT emit the confirm line."""
        out = render_panels(_equity_preview())
        assert "Confirm?" not in out


class TestSetupUnavailablePanel:
    """When setup is unavailable, SETUP panel shows a clear 'unavailable' message."""

    def test_unavailable_setup_says_unavailable(self):
        preview = _equity_preview(setup={
            "available": False,
            "asset_class": "equity",
            "poor": False,
            "caution_reasons": [],
        })
        out = render_panels(preview)
        assert "unavailable" in out.lower() or "insufficient" in out.lower()

    def test_unavailable_setup_still_shows_setup_header(self):
        preview = _equity_preview(setup={
            "available": False,
            "asset_class": "equity",
            "poor": False,
            "caution_reasons": [],
        })
        out = render_panels(preview)
        assert "SETUP" in out

    def test_no_raise_when_equity_subdict_missing(self):
        """Must not raise if 'equity' key is absent (available=False path)."""
        preview = _equity_preview(setup={
            "available": False,
            "asset_class": "equity",
            "poor": False,
            "caution_reasons": [],
        })
        out = render_panels(preview)  # must not raise
        assert isinstance(out, str)


class TestFuturesSetupPanel:
    """Futures SETUP panel shows the four factors."""

    def test_renders_symbol_mnq(self):
        out = render_panels(_futures_preview())
        assert "MNQ" in out

    def test_renders_setup_header(self):
        out = render_panels(_futures_preview())
        assert "SETUP" in out

    def test_trend_factor_shown(self):
        out = render_panels(_futures_preview())
        # Trend line should contain "uptrend" or "Trend"
        assert "uptrend" in out or "Trend" in out

    def test_counter_trend_red_glyph(self):
        out = render_panels(_futures_preview())
        # counter_trend=True should show 🔴
        assert "🔴" in out

    def test_vol_regime_shown(self):
        out = render_panels(_futures_preview())
        assert "ATR" in out or "Vol" in out or "vol" in out.lower()

    def test_atr_percentile_shown(self):
        out = render_panels(_futures_preview())
        # atr_pctile 0.78 -> "78" something
        assert "78" in out

    def test_location_factor_shown(self):
        out = render_panels(_futures_preview())
        # dist_from_high_pct 0.004 -> some location line
        assert "Location" in out or "location" in out.lower() or "%" in out

    def test_momentum_factor_shown(self):
        out = render_panels(_futures_preview())
        assert "RSI" in out or "overbought" in out or "momentum" in out.lower()

    def test_rsi_value_shown(self):
        out = render_panels(_futures_preview())
        # rsi=71.0 should appear
        assert "71" in out


class TestRiskPanel:
    """RISK & SIZING panel carries the key numbers."""

    def test_notional_shown(self):
        out = render_panels(_equity_preview())
        assert "7,260" in out or "7260" in out

    def test_pct_nav_shown(self):
        out = render_panels(_equity_preview())
        assert "2.0%" in out or "2%" in out

    def test_init_margin_shown(self):
        out = render_panels(_equity_preview())
        assert "3,630" in out or "3630" in out

    def test_name_weights_shown(self):
        out = render_panels(_equity_preview())
        # 4.1% -> 5.8%
        assert "4.1%" in out
        assert "5.8%" in out

    def test_buying_power_ok_glyph(self):
        out = render_panels(_equity_preview())
        assert "✅" in out

    def test_buying_power_fail_glyph(self):
        preview = _equity_preview(buying_power_ok=False)
        out = render_panels(preview)
        assert "🔴" in out


class TestOrderPanel:
    """ORDER panel carries the order details."""

    def test_quantity_shown(self):
        out = render_panels(_equity_preview())
        assert "50" in out

    def test_action_shown(self):
        out = render_panels(_equity_preview())
        assert "buy" in out.lower() or "Buy" in out

    def test_order_type_shown(self):
        out = render_panels(_equity_preview())
        assert "market" in out.lower() or "Market" in out


class TestPureFunction:
    """render_panels is pure — no I/O, no ib_async, safe to call repeatedly."""

    def test_same_input_same_output(self):
        p = _equity_preview()
        assert render_panels(p) == render_panels(p)

    def test_returns_str(self):
        assert isinstance(render_panels(_equity_preview()), str)

    def test_futures_returns_str(self):
        assert isinstance(render_panels(_futures_preview()), str)

    def test_no_ib_async_import(self):
        """render.py must not import ib_async."""
        import governor.gate.render as mod
        import sys
        # ib_async must not appear as an attribute of the module (i.e. not imported)
        assert "ib_async" not in dir(mod)
        # inspect the module's actual imports via __dict__; ib_async must not be a key
        assert "ib_async" not in mod.__dict__
