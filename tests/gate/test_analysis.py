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
        # A real held long sets both the magnitude and the signed exposure.
        snap = StateSnapshot(
            ts="t", nav=100_000.0,
            name_weights={"ORCL": 0.04}, name_exposure_signed={"ORCL": 0.04},
        )
        result = hypothetical_snapshot(snap, _stk_intent("ORCL", Action.BUY), order_notional=6_000.0)
        assert result.name_weights["ORCL"] == pytest.approx(0.10)
        assert result.name_exposure_signed["ORCL"] == pytest.approx(0.10)


# ---------------------------------------------------------------------------
# 2. STK SELL against a LONG decreases magnitude; selling PAST flat opens a short
#    (post-H1 semantics — magnitude tracked via name_exposure_signed)
# ---------------------------------------------------------------------------
class TestStkSellNameWeight:
    def test_partial_sell_reduces_long(self) -> None:
        # A real held long: both magnitude and signed exposure set together.
        snap = StateSnapshot(
            ts="t", nav=100_000.0,
            name_weights={"ORCL": 0.10}, name_exposure_signed={"ORCL": 0.10},
        )
        result = hypothetical_snapshot(snap, _stk_intent("ORCL", Action.SELL), order_notional=4_000.0)
        assert result.name_weights["ORCL"] == pytest.approx(0.06)
        assert result.name_exposure_signed["ORCL"] == pytest.approx(0.06)

    def test_sell_past_flat_opens_short_grows_magnitude(self) -> None:
        # Was the H1 bug: selling 0.10 against a 0.02 long crosses to a 0.08 SHORT.
        # Magnitude must GROW to 0.08 (old buggy code clamped to 0.0).
        snap = StateSnapshot(
            ts="t", nav=100_000.0,
            name_weights={"ORCL": 0.02}, name_exposure_signed={"ORCL": 0.02},
        )
        result = hypothetical_snapshot(snap, _stk_intent("ORCL", Action.SELL), order_notional=10_000.0)
        assert result.name_exposure_signed["ORCL"] == pytest.approx(-0.08)
        assert result.name_weights["ORCL"] == pytest.approx(0.08)

    def test_sell_non_existent_position_opens_short(self) -> None:
        # No prior position: a SELL opens a short, so magnitude grows from 0 (was the H1 bug).
        snap = StateSnapshot(ts="t", nav=100_000.0)
        result = hypothetical_snapshot(snap, _stk_intent("ORCL", Action.SELL), order_notional=5_000.0)
        assert result.name_exposure_signed["ORCL"] == pytest.approx(-0.05)
        assert result.name_weights["ORCL"] == pytest.approx(0.05)


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
        # A real net-long book: abs notional and signed notional set together.
        snap = StateSnapshot(
            ts="t", nav=500_000.0,
            futures_notional=61_000.0, futures_notional_signed=61_000.0,
        )
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

    def test_futures_sell_against_long_decreases_notional(self) -> None:
        # Net long 2 MNQ; SELL 1 reduces toward flat (covered-long behaviour).
        snap = StateSnapshot(
            ts="t", nav=500_000.0,
            futures_notional=122_000.0, futures_notional_signed=122_000.0,
        )
        result = hypothetical_snapshot(
            snap,
            _fut_intent("MNQ1!", Action.SELL),
            order_notional=61_000.0,
            mnq_notional_usd=61_000.0,
        )
        assert result.futures_notional == pytest.approx(61_000.0)
        assert result.futures_contracts_overnight == pytest.approx(1.0)

    def test_futures_sell_past_flat_opens_short_grows_notional(self) -> None:
        # Net long 30k; SELL 61k crosses to a 31k SHORT — magnitude grows (was the C1 bug).
        snap = StateSnapshot(
            ts="t", nav=500_000.0,
            futures_notional=30_000.0, futures_notional_signed=30_000.0,
        )
        result = hypothetical_snapshot(
            snap,
            _fut_intent("MNQ1!", Action.SELL),
            order_notional=61_000.0,
            mnq_notional_usd=61_000.0,
        )
        assert result.futures_notional_signed == pytest.approx(-31_000.0)
        assert result.futures_notional == pytest.approx(31_000.0)

    def test_futures_no_mnq_notional_preserves_contracts(self) -> None:
        snap = StateSnapshot(
            ts="t", nav=500_000.0,
            futures_notional=61_000.0, futures_notional_signed=61_000.0,
            futures_contracts_overnight=2.0,
        )
        result = hypothetical_snapshot(
            snap,
            _fut_intent("MNQ1!", Action.BUY),
            order_notional=61_000.0,
            mnq_notional_usd=0.0,
        )
        assert result.futures_contracts_overnight == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# 4b. [C1] FUT add-to-a-SHORT must INCREASE exposure (uses futures_notional_signed)
# ---------------------------------------------------------------------------
class TestFutShortAddIncreasesExposure:
    def test_sell_into_short_grows_notional_and_contracts(self) -> None:
        """Audit C1: short 6 MNQ (signed −252k), SELL 2 more (−84k) → signed −336k
        → abs notional 336k → 8 MNQ-equivalent contracts. The OLD code wrongly
        shrank the short on a SELL and computed 4."""
        mnq = 42_000.0
        snap = StateSnapshot(
            ts="t",
            nav=350_000.0,
            futures_notional=252_000.0,          # abs
            futures_notional_signed=-252_000.0,  # net short
            futures_contracts_overnight=6.0,
        )
        result = hypothetical_snapshot(
            snap,
            _fut_intent("MNQ1!", Action.SELL, qty=2.0),
            order_notional=84_000.0,
            mnq_notional_usd=mnq,
        )
        assert result.futures_notional_signed == pytest.approx(-336_000.0)
        assert result.futures_notional == pytest.approx(336_000.0)
        assert result.futures_contracts_overnight == pytest.approx(8.0)

    def test_buy_to_cover_a_short_shrinks_exposure(self) -> None:
        """BUY against a net short reduces magnitude toward flat (and can cross to long)."""
        mnq = 42_000.0
        snap = StateSnapshot(
            ts="t",
            nav=350_000.0,
            futures_notional=84_000.0,
            futures_notional_signed=-84_000.0,  # short 2 MNQ
            futures_contracts_overnight=2.0,
        )
        result = hypothetical_snapshot(
            snap,
            _fut_intent("MNQ1!", Action.BUY, qty=1.0),
            order_notional=42_000.0,
            mnq_notional_usd=mnq,
        )
        # −84k + 42k = −42k → abs 42k → 1 contract
        assert result.futures_notional_signed == pytest.approx(-42_000.0)
        assert result.futures_notional == pytest.approx(42_000.0)
        assert result.futures_contracts_overnight == pytest.approx(1.0)

    def test_buy_into_long_grows_exposure(self) -> None:
        """Regression: BUY into a net long still grows magnitude (signed stays correct)."""
        mnq = 61_000.0
        snap = StateSnapshot(
            ts="t",
            nav=500_000.0,
            futures_notional=61_000.0,
            futures_notional_signed=61_000.0,  # long 1
            futures_contracts_overnight=1.0,
        )
        result = hypothetical_snapshot(
            snap, _fut_intent("MNQ1!", Action.BUY), order_notional=61_000.0, mnq_notional_usd=mnq
        )
        assert result.futures_notional_signed == pytest.approx(122_000.0)
        assert result.futures_notional == pytest.approx(122_000.0)
        assert result.futures_contracts_overnight == pytest.approx(2.0)

    def test_signed_chains_correctly_across_two_hypotheticals(self) -> None:
        """The signed field is set on the result so a chained hypothetical stays correct."""
        mnq = 42_000.0
        snap = StateSnapshot(
            ts="t", nav=350_000.0,
            futures_notional=42_000.0, futures_notional_signed=-42_000.0,
            futures_contracts_overnight=1.0,
        )
        once = hypothetical_snapshot(
            snap, _fut_intent("MNQ1!", Action.SELL), order_notional=42_000.0, mnq_notional_usd=mnq
        )
        twice = hypothetical_snapshot(
            once, _fut_intent("MNQ1!", Action.SELL), order_notional=42_000.0, mnq_notional_usd=mnq
        )
        assert twice.futures_notional_signed == pytest.approx(-126_000.0)
        assert twice.futures_notional == pytest.approx(126_000.0)
        assert twice.futures_contracts_overnight == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# 2b. [H1] STK opening/growing a SHORT must INCREASE concentration
# ---------------------------------------------------------------------------
class TestStkShortIncreasesConcentration:
    def test_sell_to_open_short_grows_name_and_sector_weight(self) -> None:
        """Audit H1: SELL with no existing position opens a short — magnitude grows."""
        snap = StateSnapshot(ts="t", nav=100_000.0)
        result = hypothetical_snapshot(
            snap, _stk_intent("ORCL", Action.SELL), order_notional=6_000.0, sector="Technology"
        )
        assert result.name_weights["ORCL"] == pytest.approx(0.06)
        assert result.sector_weights["Technology"] == pytest.approx(0.06)
        assert result.name_exposure_signed["ORCL"] == pytest.approx(-0.06)

    def test_sell_grows_existing_short(self) -> None:
        """SELL into an existing short increases magnitude further."""
        snap = StateSnapshot(
            ts="t", nav=100_000.0,
            name_weights={"ORCL": 0.04},
            name_exposure_signed={"ORCL": -0.04},
            sector_weights={"Technology": 0.04},
        )
        result = hypothetical_snapshot(
            snap, _stk_intent("ORCL", Action.SELL), order_notional=6_000.0, sector="Technology"
        )
        assert result.name_exposure_signed["ORCL"] == pytest.approx(-0.10)
        assert result.name_weights["ORCL"] == pytest.approx(0.10)
        assert result.sector_weights["Technology"] == pytest.approx(0.10)

    def test_sell_reducing_a_long_decreases_magnitude(self) -> None:
        """SELL against a net LONG shrinks magnitude (the existing covered-long behaviour)."""
        snap = StateSnapshot(
            ts="t", nav=100_000.0,
            name_weights={"ORCL": 0.10},
            name_exposure_signed={"ORCL": 0.10},
            sector_weights={"Technology": 0.10},
        )
        result = hypothetical_snapshot(
            snap, _stk_intent("ORCL", Action.SELL), order_notional=4_000.0, sector="Technology"
        )
        assert result.name_exposure_signed["ORCL"] == pytest.approx(0.06)
        assert result.name_weights["ORCL"] == pytest.approx(0.06)
        assert result.sector_weights["Technology"] == pytest.approx(0.06)

    def test_buy_to_cover_a_short_decreases_magnitude(self) -> None:
        """BUY against a net SHORT reduces magnitude toward flat."""
        snap = StateSnapshot(
            ts="t", nav=100_000.0,
            name_weights={"ORCL": 0.10},
            name_exposure_signed={"ORCL": -0.10},
            sector_weights={"Technology": 0.10},
        )
        result = hypothetical_snapshot(
            snap, _stk_intent("ORCL", Action.BUY), order_notional=4_000.0, sector="Technology"
        )
        assert result.name_exposure_signed["ORCL"] == pytest.approx(-0.06)
        assert result.name_weights["ORCL"] == pytest.approx(0.06)
        assert result.sector_weights["Technology"] == pytest.approx(0.06)

    def test_buy_growing_a_long_still_grows(self) -> None:
        """Regression for the original BUY path via the signed map."""
        snap = StateSnapshot(
            ts="t", nav=100_000.0,
            name_weights={"ORCL": 0.04},
            name_exposure_signed={"ORCL": 0.04},
        )
        result = hypothetical_snapshot(
            snap, _stk_intent("ORCL", Action.BUY), order_notional=6_000.0
        )
        assert result.name_exposure_signed["ORCL"] == pytest.approx(0.10)
        assert result.name_weights["ORCL"] == pytest.approx(0.10)

    def test_immutability_signed_map_not_mutated(self) -> None:
        original = {"ORCL": 0.04}
        snap = StateSnapshot(ts="t", nav=100_000.0, name_exposure_signed=original)
        _ = hypothetical_snapshot(snap, _stk_intent("ORCL", Action.SELL), order_notional=6_000.0)
        assert original == {"ORCL": 0.04}
        assert snap.name_exposure_signed == {"ORCL": 0.04}


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
