"""Tests for governor.gate.analysis.hypothetical_snapshot.

TDD: these tests are written BEFORE the implementation.
"""
import pytest

from governor.config import GateRules
from governor.gate.analysis import SizingCheck, hypothetical_snapshot, sizing
from governor.gate.intent import Action, OrderIntent, OrderType, SecType
from governor.model import StateSnapshot


def _stk_intent(symbol: str, action: Action, qty: float = 10.0) -> OrderIntent:
    return OrderIntent(
        action=action,
        symbol=symbol,
        quantity=qty,
        sec_type=SecType.STK,
        order_type=OrderType.MARKET,
    )


def _fut_intent(symbol: str, action: Action, qty: float = 1.0) -> OrderIntent:
    return OrderIntent(
        action=action,
        symbol=symbol,
        quantity=qty,
        sec_type=SecType.FUT,
        order_type=OrderType.MARKET,
    )


# ---------------------------------------------------------------------------
# 1. STK BUY increases name_weight by order_notional / nav
# ---------------------------------------------------------------------------
class TestStkBuyNameWeight:
    def test_new_position(self) -> None:
        snap = StateSnapshot(ts="t", nav=100_000.0)
        result = hypothetical_snapshot(snap, _stk_intent("ORCL", Action.BUY), order_notional=6_000.0)
        assert result.name_weights["ORCL"] == pytest.approx(0.06)

    def test_add_to_existing_position(self) -> None:
        snap = StateSnapshot(ts="t", nav=100_000.0, name_weights={"ORCL": 0.04})
        result = hypothetical_snapshot(snap, _stk_intent("ORCL", Action.BUY), order_notional=6_000.0)
        assert result.name_weights["ORCL"] == pytest.approx(0.10)


# ---------------------------------------------------------------------------
# 2. STK SELL decreases name_weight, clamped at 0.0
# ---------------------------------------------------------------------------
class TestStkSellNameWeight:
    def test_partial_sell(self) -> None:
        snap = StateSnapshot(ts="t", nav=100_000.0, name_weights={"ORCL": 0.10})
        result = hypothetical_snapshot(snap, _stk_intent("ORCL", Action.SELL), order_notional=4_000.0)
        assert result.name_weights["ORCL"] == pytest.approx(0.06)

    def test_sell_clamps_at_zero(self) -> None:
        snap = StateSnapshot(ts="t", nav=100_000.0, name_weights={"ORCL": 0.02})
        result = hypothetical_snapshot(snap, _stk_intent("ORCL", Action.SELL), order_notional=10_000.0)
        assert result.name_weights["ORCL"] == 0.0

    def test_sell_non_existent_position_clamps_at_zero(self) -> None:
        snap = StateSnapshot(ts="t", nav=100_000.0)
        result = hypothetical_snapshot(snap, _stk_intent("ORCL", Action.SELL), order_notional=5_000.0)
        assert result.name_weights.get("ORCL", 0.0) == 0.0


# ---------------------------------------------------------------------------
# 3. STK BUY with sector kwarg updates sector_weights; no sector → "unknown"
# ---------------------------------------------------------------------------
class TestStkSectorWeights:
    def test_with_named_sector(self) -> None:
        snap = StateSnapshot(ts="t", nav=100_000.0, sector_weights={"Technology": 0.20})
        result = hypothetical_snapshot(
            snap, _stk_intent("NVDA", Action.BUY), order_notional=5_000.0, sector="Technology"
        )
        assert result.sector_weights["Technology"] == pytest.approx(0.25)

    def test_without_sector_falls_into_unknown(self) -> None:
        snap = StateSnapshot(ts="t", nav=100_000.0)
        result = hypothetical_snapshot(snap, _stk_intent("ORCL", Action.BUY), order_notional=3_000.0)
        assert result.sector_weights["unknown"] == pytest.approx(0.03)

    def test_new_sector_created(self) -> None:
        snap = StateSnapshot(ts="t", nav=100_000.0)
        result = hypothetical_snapshot(
            snap, _stk_intent("NVDA", Action.BUY), order_notional=5_000.0, sector="Technology"
        )
        assert result.sector_weights["Technology"] == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# 4. FUT BUY increases futures_notional and sets futures_contracts_overnight
# ---------------------------------------------------------------------------
class TestFutBuy:
    def test_futures_notional_increases(self) -> None:
        snap = StateSnapshot(ts="t", nav=500_000.0, futures_notional=61_000.0)
        result = hypothetical_snapshot(
            snap,
            _fut_intent("MNQ1!", Action.BUY),
            order_notional=61_000.0,
            mnq_notional_usd=61_000.0,
        )
        assert result.futures_notional == pytest.approx(122_000.0)

    def test_futures_contracts_overnight_set_from_notional(self) -> None:
        snap = StateSnapshot(ts="t", nav=500_000.0, futures_notional=0.0)
        result = hypothetical_snapshot(
            snap,
            _fut_intent("MNQ1!", Action.BUY),
            order_notional=61_000.0,
            mnq_notional_usd=61_000.0,
        )
        assert result.futures_contracts_overnight == pytest.approx(1.0)

    def test_futures_sell_decreases_notional(self) -> None:
        snap = StateSnapshot(ts="t", nav=500_000.0, futures_notional=122_000.0)
        result = hypothetical_snapshot(
            snap,
            _fut_intent("MNQ1!", Action.SELL),
            order_notional=61_000.0,
            mnq_notional_usd=61_000.0,
        )
        assert result.futures_notional == pytest.approx(61_000.0)
        assert result.futures_contracts_overnight == pytest.approx(1.0)

    def test_futures_notional_clamped_at_zero(self) -> None:
        snap = StateSnapshot(ts="t", nav=500_000.0, futures_notional=30_000.0)
        result = hypothetical_snapshot(
            snap,
            _fut_intent("MNQ1!", Action.SELL),
            order_notional=61_000.0,
            mnq_notional_usd=61_000.0,
        )
        assert result.futures_notional == 0.0

    def test_futures_no_mnq_notional_preserves_contracts(self) -> None:
        snap = StateSnapshot(ts="t", nav=500_000.0, futures_notional=61_000.0, futures_contracts_overnight=2.0)
        result = hypothetical_snapshot(
            snap,
            _fut_intent("MNQ1!", Action.BUY),
            order_notional=61_000.0,
            mnq_notional_usd=0.0,
        )
        assert result.futures_contracts_overnight == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# 5. Immutability: input snapshot and its dicts are UNCHANGED
# ---------------------------------------------------------------------------
class TestImmutability:
    def test_stk_buy_does_not_mutate_current(self) -> None:
        original_weights = {"ORCL": 0.05}
        snap = StateSnapshot(ts="t", nav=100_000.0, name_weights=original_weights)
        _ = hypothetical_snapshot(snap, _stk_intent("ORCL", Action.BUY), order_notional=6_000.0)
        # original dict unchanged
        assert original_weights == {"ORCL": 0.05}
        assert snap.name_weights == {"ORCL": 0.05}

    def test_stk_buy_does_not_mutate_sector_weights(self) -> None:
        original_sectors = {"Technology": 0.20}
        snap = StateSnapshot(ts="t", nav=100_000.0, sector_weights=original_sectors)
        _ = hypothetical_snapshot(
            snap, _stk_intent("NVDA", Action.BUY), order_notional=5_000.0, sector="Technology"
        )
        assert original_sectors == {"Technology": 0.20}
        assert snap.sector_weights == {"Technology": 0.20}

    def test_fut_buy_does_not_mutate_current(self) -> None:
        snap = StateSnapshot(ts="t", nav=500_000.0, futures_notional=61_000.0, futures_contracts_overnight=1.0)
        _ = hypothetical_snapshot(
            snap,
            _fut_intent("MNQ1!", Action.BUY),
            order_notional=61_000.0,
            mnq_notional_usd=61_000.0,
        )
        assert snap.futures_notional == 61_000.0
        assert snap.futures_contracts_overnight == 1.0


# ---------------------------------------------------------------------------
# 6. NAV is unchanged on the returned snapshot
# ---------------------------------------------------------------------------
class TestNavUnchanged:
    def test_stk_buy_nav_unchanged(self) -> None:
        snap = StateSnapshot(ts="t", nav=100_000.0)
        result = hypothetical_snapshot(snap, _stk_intent("ORCL", Action.BUY), order_notional=6_000.0)
        assert result.nav == 100_000.0

    def test_fut_buy_nav_unchanged(self) -> None:
        snap = StateSnapshot(ts="t", nav=500_000.0)
        result = hypothetical_snapshot(
            snap, _fut_intent("MNQ1!", Action.BUY), order_notional=61_000.0, mnq_notional_usd=61_000.0
        )
        assert result.nav == 500_000.0

    def test_zero_nav_returns_zero_delta(self) -> None:
        snap = StateSnapshot(ts="t", nav=0.0)
        result = hypothetical_snapshot(snap, _stk_intent("ORCL", Action.BUY), order_notional=6_000.0)
        assert result.name_weights.get("ORCL", 0.0) == 0.0


# ---------------------------------------------------------------------------
# 7. sizing() — per-trade notional as fraction of NAV
# ---------------------------------------------------------------------------
class TestSizing:
    def test_under_band_not_flagged(self) -> None:
        cfg = GateRules()  # default max_trade_pct_nav=0.015
        result = sizing(order_notional=1_000.0, nav=100_000.0, cfg=cfg)
        assert isinstance(result, SizingCheck)
        assert result.pct_nav == pytest.approx(0.01)
        assert result.over_band is False

    def test_over_band_flagged(self) -> None:
        cfg = GateRules()
        result = sizing(order_notional=2_000.0, nav=100_000.0, cfg=cfg)
        assert result.pct_nav == pytest.approx(0.02)
        assert result.over_band is True

    def test_exactly_at_band_not_flagged(self) -> None:
        """Boundary: strict > means equal-to-band is NOT over."""
        cfg = GateRules()  # max_trade_pct_nav=0.015 → 1_500 / 100_000 = 0.015
        result = sizing(order_notional=1_500.0, nav=100_000.0, cfg=cfg)
        assert result.pct_nav == pytest.approx(0.015)
        assert result.over_band is False

    def test_zero_nav_returns_zero_pct(self) -> None:
        cfg = GateRules()
        result = sizing(order_notional=5_000.0, nav=0.0, cfg=cfg)
        assert result.pct_nav == 0.0
        assert result.over_band is False

    def test_negative_nav_returns_zero_pct(self) -> None:
        cfg = GateRules()
        result = sizing(order_notional=5_000.0, nav=-1.0, cfg=cfg)
        assert result.pct_nav == 0.0
        assert result.over_band is False
