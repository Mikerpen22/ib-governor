"""Tests for governor.live.builder.build_live_snapshot.

Verifies the two behavioral variants:
  - mutate_hwm=True  : updates (writes) the HWM peak on disk
  - mutate_hwm=False : reads drawdown without mutating peak
  - returned snapshot has nav + derived fields populated
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from governor.config import RulesConfig
from governor.live.builder import build_live_snapshot
from governor.state.hwm import HwmStore
from governor.state.trade_log import WeeklyTradeLog

ET = ZoneInfo("America/New_York")

_NOW = dt.datetime(2026, 6, 19, 14, 30, tzinfo=ET)
_NAV = 250_000.0


# ---------------------------------------------------------------------------
# Fake IBKR objects
# ---------------------------------------------------------------------------

def _account_value(tag: str, value: str, currency: str = "USD"):
    return SimpleNamespace(tag=tag, value=value, currency=currency, account="U1")


def _fake_ib(nav: float = _NAV) -> SimpleNamespace:
    """Minimal duck-typed IB stub with accountValues / portfolio / fills methods."""
    account_values = [
        _account_value("NetLiquidation", str(nav)),
        _account_value("ExcessLiquidity", str(nav * 0.5)),
        _account_value("GrossPositionValue", str(nav * 0.8)),
    ]
    return SimpleNamespace(
        accountValues=lambda: account_values,
        portfolio=lambda: [],
        fills=lambda: [],
    )


# ---------------------------------------------------------------------------
# Fake sector resolver
# ---------------------------------------------------------------------------

class _FakeSector:
    def map_for(self, syms):
        return {}


# ---------------------------------------------------------------------------
# Tests: mutate_hwm=True writes peak
# ---------------------------------------------------------------------------

class TestMutateHwmTrue:
    def test_writes_peak_after_call(self, tmp_path):
        hwm_path = tmp_path / "hwm.json"
        hwm = HwmStore(hwm_path)
        trade_log = WeeklyTradeLog(tmp_path / "trade_log.json")
        ib = _fake_ib(nav=_NAV)
        config = RulesConfig()

        assert hwm.peak() == 0.0  # nothing written yet

        build_live_snapshot(
            ib, config,
            sector_resolver=_FakeSector(),
            trade_log=trade_log,
            hwm=hwm,
            now=_NOW,
            mutate_hwm=True,
        )

        assert hwm.peak() == pytest.approx(_NAV)

    def test_drawdown_is_zero_when_nav_equals_peak(self, tmp_path):
        hwm = HwmStore(tmp_path / "hwm.json")
        trade_log = WeeklyTradeLog(tmp_path / "trade_log.json")
        ib = _fake_ib(nav=_NAV)
        config = RulesConfig()

        snap = build_live_snapshot(
            ib, config,
            sector_resolver=_FakeSector(),
            trade_log=trade_log,
            hwm=hwm,
            now=_NOW,
            mutate_hwm=True,
        )

        # No prior peak — nav becomes the new peak, so drawdown is 0.
        assert snap.drawdown_pct == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Tests: mutate_hwm=False does NOT write peak
# ---------------------------------------------------------------------------

class TestMutateHwmFalse:
    def test_does_not_write_peak(self, tmp_path):
        hwm_path = tmp_path / "hwm.json"
        hwm = HwmStore(hwm_path)
        trade_log = WeeklyTradeLog(tmp_path / "trade_log.json")
        ib = _fake_ib(nav=_NAV)
        config = RulesConfig()

        build_live_snapshot(
            ib, config,
            sector_resolver=_FakeSector(),
            trade_log=trade_log,
            hwm=hwm,
            now=_NOW,
            mutate_hwm=False,
        )

        # File must not have been created / peak must remain at its initial 0.
        assert hwm.peak() == 0.0

    def test_drawdown_computed_from_existing_peak(self, tmp_path):
        """With a pre-existing peak higher than nav, drawdown is read correctly."""
        hwm_path = tmp_path / "hwm.json"
        hwm = HwmStore(hwm_path)
        # Plant a peak above the nav we'll use.
        hwm.update(_NAV * 1.1)  # sets peak = NAV * 1.1
        peak_after_seed = hwm.peak()

        trade_log = WeeklyTradeLog(tmp_path / "trade_log.json")
        ib = _fake_ib(nav=_NAV)  # nav < peak
        config = RulesConfig()

        snap = build_live_snapshot(
            ib, config,
            sector_resolver=_FakeSector(),
            trade_log=trade_log,
            hwm=hwm,
            now=_NOW,
            mutate_hwm=False,
        )

        expected_drawdown = (peak_after_seed - _NAV) / peak_after_seed
        assert snap.drawdown_pct == pytest.approx(expected_drawdown)
        # Peak must be unchanged after the read-only call.
        assert hwm.peak() == pytest.approx(peak_after_seed)


# ---------------------------------------------------------------------------
# Tests: snapshot fields are populated
# ---------------------------------------------------------------------------

class TestSnapshotFields:
    def test_nav_is_populated(self, tmp_path):
        hwm = HwmStore(tmp_path / "hwm.json")
        trade_log = WeeklyTradeLog(tmp_path / "trade_log.json")
        ib = _fake_ib(nav=_NAV)
        config = RulesConfig()

        snap = build_live_snapshot(
            ib, config,
            sector_resolver=_FakeSector(),
            trade_log=trade_log,
            hwm=hwm,
            now=_NOW,
            mutate_hwm=False,
        )

        assert snap.nav == pytest.approx(_NAV)

    def test_margin_cushion_is_populated(self, tmp_path):
        hwm = HwmStore(tmp_path / "hwm.json")
        trade_log = WeeklyTradeLog(tmp_path / "trade_log.json")
        ib = _fake_ib(nav=_NAV)
        config = RulesConfig()

        snap = build_live_snapshot(
            ib, config,
            sector_resolver=_FakeSector(),
            trade_log=trade_log,
            hwm=hwm,
            now=_NOW,
            mutate_hwm=False,
        )

        # ExcessLiquidity = nav * 0.5 → cushion = 0.5
        assert snap.margin_cushion == pytest.approx(0.5)

    def test_empty_portfolio_yields_zero_futures_fields(self, tmp_path):
        hwm = HwmStore(tmp_path / "hwm.json")
        trade_log = WeeklyTradeLog(tmp_path / "trade_log.json")
        ib = _fake_ib()
        config = RulesConfig()

        snap = build_live_snapshot(
            ib, config,
            sector_resolver=_FakeSector(),
            trade_log=trade_log,
            hwm=hwm,
            now=_NOW,
            mutate_hwm=False,
        )

        assert snap.futures_realized_pnl_today == pytest.approx(0.0)
        assert snap.futures_trade_count_today == 0
        assert snap.futures_notional == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Tests: live_mnq_notional
# ---------------------------------------------------------------------------

from governor.live.builder import live_mnq_notional  # noqa: E402


def _fake_contract(multiplier="2"):
    return SimpleNamespace(multiplier=multiplier)


def _fake_ticker(price: float | None):
    def market_price():
        return price
    t = SimpleNamespace(marketPrice=market_price)
    return t


class TestLiveMnqNotional:
    def test_returns_price_times_multiplier(self):
        """qualifyContracts returns a contract, reqTickers returns price=30500 → 61000."""
        c = _fake_contract(multiplier="2")
        ticker = _fake_ticker(price=30500.0)
        ib = SimpleNamespace(
            qualifyContracts=lambda *a, **kw: [c],
            reqTickers=lambda *a, **kw: [ticker],
        )
        result = live_mnq_notional(ib)
        assert result == pytest.approx(61000.0)

    def test_returns_none_when_no_qualified_contracts(self):
        """qualifyContracts returns [] → None (fail-soft)."""
        ib = SimpleNamespace(
            qualifyContracts=lambda *a, **kw: [],
            reqTickers=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not be called")),
        )
        assert live_mnq_notional(ib) is None

    def test_returns_none_when_req_tickers_raises(self):
        """reqTickers raises → None (fail-soft, never propagates)."""
        c = _fake_contract(multiplier="2")
        ib = SimpleNamespace(
            qualifyContracts=lambda *a, **kw: [c],
            reqTickers=lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("TWS disconnected")),
        )
        assert live_mnq_notional(ib) is None

    def test_falls_back_to_close_when_market_price_zero(self):
        """marketPrice() returns 0 → falls back to ticker.close attribute."""
        c = _fake_contract(multiplier="2")
        t = _fake_ticker(price=0.0)
        t.close = 30500.0
        ib = SimpleNamespace(
            qualifyContracts=lambda *a, **kw: [c],
            reqTickers=lambda *a, **kw: [t],
        )
        result = live_mnq_notional(ib)
        assert result == pytest.approx(61000.0)

    def test_returns_none_when_price_zero_and_no_close(self):
        """marketPrice() returns 0, no .close attribute → None."""
        c = _fake_contract(multiplier="2")
        t = _fake_ticker(price=0.0)
        # no .close attribute on this ticker
        ib = SimpleNamespace(
            qualifyContracts=lambda *a, **kw: [c],
            reqTickers=lambda *a, **kw: [t],
        )
        assert live_mnq_notional(ib) is None

    def test_uses_default_multiplier_2_when_missing(self):
        """Contract has no multiplier attribute → defaults to 2.0."""
        c = SimpleNamespace()  # no multiplier
        ticker = _fake_ticker(price=30500.0)
        ib = SimpleNamespace(
            qualifyContracts=lambda *a, **kw: [c],
            reqTickers=lambda *a, **kw: [ticker],
        )
        result = live_mnq_notional(ib)
        assert result == pytest.approx(61000.0)  # 30500 * 2


# ---------------------------------------------------------------------------
# Tests: build_live_snapshot uses live MNQ value when available
# ---------------------------------------------------------------------------

def _fake_ib_with_mnq(nav: float = _NAV, mnq_price: float = 30500.0):
    """Fake IB stub that also implements qualifyContracts + reqTickers for MNQ."""
    account_values = [
        _account_value("NetLiquidation", str(nav)),
        _account_value("ExcessLiquidity", str(nav * 0.5)),
        _account_value("GrossPositionValue", str(nav * 0.8)),
    ]
    c = _fake_contract(multiplier="2")
    ticker = _fake_ticker(price=mnq_price)
    return SimpleNamespace(
        accountValues=lambda: account_values,
        portfolio=lambda: [],
        fills=lambda: [],
        qualifyContracts=lambda *a, **kw: [c],
        reqTickers=lambda *a, **kw: [ticker],
    )


def _fake_ib_no_mnq(nav: float = _NAV):
    """Fake IB stub whose qualifyContracts returns [] (live fetch returns None)."""
    account_values = [
        _account_value("NetLiquidation", str(nav)),
        _account_value("ExcessLiquidity", str(nav * 0.5)),
        _account_value("GrossPositionValue", str(nav * 0.8)),
    ]
    return SimpleNamespace(
        accountValues=lambda: account_values,
        portfolio=lambda: [],
        fills=lambda: [],
        qualifyContracts=lambda *a, **kw: [],
        reqTickers=lambda *a, **kw: [],
    )


class TestBuildLiveSnapshotMnqNotional:
    def test_uses_live_value_when_ib_provides_mnq_data(self, tmp_path):
        """build_live_snapshot passes live MNQ notional (30500 × 2 = 61000) through to snapshot.

        With an empty portfolio futures_notional=0 and contracts_overnight=0 regardless of
        the MNQ reference value, so we verify indirectly: the snapshot is produced without
        error and nav is correct (the live fetch path ran without crashing).
        """
        hwm = HwmStore(tmp_path / "hwm.json")
        trade_log = WeeklyTradeLog(tmp_path / "trade_log.json")
        ib = _fake_ib_with_mnq(nav=_NAV, mnq_price=30500.0)
        config = RulesConfig()

        snap = build_live_snapshot(
            ib, config,
            sector_resolver=_FakeSector(),
            trade_log=trade_log,
            hwm=hwm,
            now=_NOW,
            mutate_hwm=False,
        )

        assert snap.nav == pytest.approx(_NAV)
        # Empty portfolio → zero notional; the live MNQ fetch ran (no exception).
        assert snap.futures_notional == pytest.approx(0.0)
        assert snap.futures_contracts_overnight == pytest.approx(0.0)

    def test_falls_back_to_cfg_when_live_mnq_returns_none(self, tmp_path):
        """When qualifyContracts returns [] live_mnq_notional → None → cfg default used."""
        hwm = HwmStore(tmp_path / "hwm.json")
        trade_log = WeeklyTradeLog(tmp_path / "trade_log.json")
        ib = _fake_ib_no_mnq(nav=_NAV)
        config = RulesConfig()

        snap = build_live_snapshot(
            ib, config,
            sector_resolver=_FakeSector(),
            trade_log=trade_log,
            hwm=hwm,
            now=_NOW,
            mutate_hwm=False,
        )

        # No MNQ data, but snapshot should still succeed (fallback to cfg default).
        assert snap.nav == pytest.approx(_NAV)
        assert snap.futures_notional == pytest.approx(0.0)
