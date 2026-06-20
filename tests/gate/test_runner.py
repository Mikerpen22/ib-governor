"""Tests for governor.gate.runner — qualify + analyze + submit (I/O isolated).

TDD: all 9 cases written BEFORE the implementation.
Uses a FakeIB (no real TWS), FakeLockoutStore, and FakeExecutor.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from governor.actions.lockout import Lockout
from governor.config import RulesConfig
from governor.gate.analysis import Verdict
from governor.gate.intent import Action, OrderIntent, OrderType, SecType
from governor.model import StateSnapshot
from governor.state.json_store import StateFileError

import datetime as dt


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

def _make_req_id_counter(start=100):
    counter = [start]
    def getReqId():
        val = counter[0]
        counter[0] += 1
        return val
    return SimpleNamespace(getReqId=getReqId)


# A deterministic 3-expiry futures ladder used by the base FakeIB.
# Front (earliest) is 20260619, which matches the default test clock (_NOW is
# 2026-06-18, so 20260619 is the earliest live expiry).
_FAKE_FUT_EXPIRIES = ("20260918", "20260619", "20261218")
_FAKE_FUT_FRONT = "20260619"


class FakeIB:
    """ib_async substitute (no TWS) that MODELS the real qualification semantics
    of ib_async 2.1.0 — it does NOT blindly echo contracts back.

    qualifyContracts:
      • under-specified Future (no lastTradeDateOrContractMonth) -> [None]
        (the real lib appends None; it does not pick an expiry for you)
      • fully-specified Future (expiry set)                      -> [that contract]
      • Stock                                                    -> [single match]
      • anything else                                            -> identity
    reqContractDetails(Future): one ContractDetails per listed expiry, so
      runner._front_future can pick the front month.
    """

    def __init__(self):
        self.client = _make_req_id_counter()

    def reqContractDetails(self, contract):
        sym = getattr(contract, "symbol", None)
        exch = getattr(contract, "exchange", None) or "CME"
        out = []
        for i, exp in enumerate(_FAKE_FUT_EXPIRIES):
            c = SimpleNamespace(
                secType="FUT", symbol=sym, exchange=exch,
                lastTradeDateOrContractMonth=exp, currency="USD",
                multiplier="2", conId=1000 + i,
            )
            out.append(SimpleNamespace(contract=c))
        return out

    def qualifyContracts(self, *contracts):
        out = []
        for c in contracts:
            sec = getattr(c, "secType", None)
            if sec == "FUT":
                expiry = getattr(c, "lastTradeDateOrContractMonth", "") or ""
                out.append(c if expiry else None)
            elif sec == "STK":
                # Single, unambiguous match (the common case).
                out.append(c)
            else:
                out.append(c)
        return out

    def whatIfOrder(self, contract, order):
        # Real ib.whatIfOrder returns a LIST of OrderState (or [] when TWS blocks it).
        return [SimpleNamespace(
            initMarginAfter="5000",
            equityWithLoanAfter="250000",
            maintMarginAfter="4000",
        )]

    def reqTickers(self, *contracts):
        return [SimpleNamespace(
            marketPrice=lambda: 145.0,
            last=145.0,
            close=145.0,
        )]


class InsufficientBPFakeIB(FakeIB):
    """whatIf reports init margin > equity — buying power insufficient."""

    def whatIfOrder(self, contract, order):
        return [SimpleNamespace(
            initMarginAfter="400000",
            equityWithLoanAfter="250000",
            maintMarginAfter="300000",
        )]


class ReadOnlyWhatIfIB(FakeIB):
    """whatIfOrder returns [] — what TWS does when its 'Read-Only API' setting blocks
    the preview (observed live). The gate must degrade gracefully, not crash."""

    def whatIfOrder(self, contract, order):
        return []


class SentinelMarginIB(FakeIB):
    """whatIfOrder returns a real OrderState whose initMarginAfter is the IBKR UNSET
    sentinel (~1.79e308). The preview must NOT surface the sentinel as a dollar figure."""

    def whatIfOrder(self, contract, order):
        return [SimpleNamespace(
            initMarginAfter="1.7976931348623157e308",
            equityWithLoanAfter="250000",
            maintMarginAfter="4000",
        )]


class FakeLockoutStore:
    def __init__(self, lockout=None, raises=False):
        self._lockout = lockout
        self._raises = raises

    def active(self, now):
        if self._raises:
            raise StateFileError("fake corrupt lockout file")
        return self._lockout


class FakeExecutor:
    def __init__(self, return_value=True):
        self._return_value = return_value
        self.calls = []           # list of (contract, order) tuples
        self.bracket_calls = []   # list of (contract, orders) tuples

    def place_order(self, contract, order) -> bool:
        self.calls.append((contract, order))
        return self._return_value

    def place_orders(self, contract, orders) -> bool:
        self.bracket_calls.append((contract, orders))
        return self._return_value


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = dt.datetime(2026, 6, 18, 10, 0, 0, tzinfo=dt.timezone.utc)


def _snap(nav=250_000.0, **kwargs) -> StateSnapshot:
    base = dict(
        ts="2026-06-18T10:00:00+00:00",
        nav=nav,
        margin_cushion=0.60,
        gross_leverage=0.0,
        drawdown_pct=0.0,
    )
    base.update(kwargs)
    return StateSnapshot(**base)


def _stk_intent(symbol="AAPL", action=Action.BUY, qty=10.0,
                order_type=OrderType.MARKET, limit_price=None):
    return OrderIntent(
        action=action,
        symbol=symbol,
        quantity=qty,
        sec_type=SecType.STK,
        order_type=order_type,
        limit_price=limit_price,
    )


def _fut_intent(symbol="MNQ", action=Action.BUY, qty=1.0):
    return OrderIntent(
        action=action,
        symbol=symbol,
        quantity=qty,
        sec_type=SecType.FUT,
        order_type=OrderType.MARKET,
    )


# ---------------------------------------------------------------------------
# Import the runner (will fail until implemented — that's the TDD red step)
# ---------------------------------------------------------------------------

from governor.gate import runner  # noqa: E402


# ---------------------------------------------------------------------------
# Unit: _buying_power_ok boundary / degraded-whatIf behavior
# ---------------------------------------------------------------------------

class TestBuyingPowerOk:
    def test_unparseable_margin_is_treated_as_ok(self):
        # IB returns N/A strings (degraded Gateway) -> _to_float -> 0.0 -> OK
        whatif = SimpleNamespace(initMarginAfter="N/A", equityWithLoanAfter="N/A")
        assert runner._buying_power_ok(whatif) is True

    def test_missing_fields_are_treated_as_ok(self):
        whatif = SimpleNamespace(initMarginAfter="", equityWithLoanAfter="")
        assert runner._buying_power_ok(whatif) is True

    def test_init_equal_to_equity_is_ok(self):
        # Boundary: strict > means equal is NOT a block
        whatif = SimpleNamespace(initMarginAfter="250000", equityWithLoanAfter="250000")
        assert runner._buying_power_ok(whatif) is True

    def test_init_just_over_equity_blocks(self):
        whatif = SimpleNamespace(initMarginAfter="250001", equityWithLoanAfter="250000")
        assert runner._buying_power_ok(whatif) is False

    def test_zero_equity_with_positive_init_is_ok(self):
        # equity <= 0 fails the > 0 guard -> not a block (avoid false-block)
        whatif = SimpleNamespace(initMarginAfter="5000", equityWithLoanAfter="0")
        assert runner._buying_power_ok(whatif) is True

    # ── [M1] prefer the after-trade availableFunds/excessLiquidity binding constraint ──

    def test_negative_available_funds_after_blocks(self):
        """When availableFundsAfter is present and negative, the order is insufficient —
        this is the real binding constraint (init margin would eat all free funds)."""
        whatif = SimpleNamespace(
            initMarginAfter="60000",
            equityWithLoanAfter="250000",       # init < equity, so the OLD check passes
            availableFundsAfter="-5000",         # ...but no free funds left -> BLOCK
        )
        assert runner._buying_power_ok(whatif) is False

    def test_negative_excess_liquidity_after_blocks(self):
        whatif = SimpleNamespace(
            initMarginAfter="60000",
            equityWithLoanAfter="250000",
            excessLiquidityAfter="-1.0",
        )
        assert runner._buying_power_ok(whatif) is False

    def test_positive_available_funds_after_is_ok(self):
        whatif = SimpleNamespace(
            initMarginAfter="60000",
            equityWithLoanAfter="250000",
            availableFundsAfter="40000",
        )
        assert runner._buying_power_ok(whatif) is True

    def test_zero_available_funds_after_is_ok(self):
        """Boundary: exactly zero free funds is not (yet) insufficient."""
        whatif = SimpleNamespace(
            initMarginAfter="60000",
            equityWithLoanAfter="250000",
            availableFundsAfter="0",
        )
        assert runner._buying_power_ok(whatif) is True

    def test_sentinel_available_funds_after_does_not_falsely_pass(self):
        """A 1.79e308 UNSET sentinel on availableFundsAfter must NOT be read as 'huge free
        funds, definitely ok' — it's unset. Fall back to the init-vs-equity check, which
        here detects insufficiency (init > equity)."""
        whatif = SimpleNamespace(
            initMarginAfter="400000",
            equityWithLoanAfter="250000",       # init > equity -> insufficient via fallback
            availableFundsAfter="1.7976931348623157e308",  # UNSET sentinel
        )
        assert runner._buying_power_ok(whatif) is False

    def test_sentinel_init_margin_does_not_falsely_pass(self):
        """A sentinel initMarginAfter must not silently pass as 'ok' via the fallback path
        (guard maps it out; with no usable after-funds field the verdict is the fail-open
        default, NOT a spurious block)."""
        whatif = SimpleNamespace(
            initMarginAfter="1.7976931348623157e308",  # UNSET sentinel
            equityWithLoanAfter="250000",
        )
        assert runner._buying_power_ok(whatif) is True

    def test_none_state_still_fails_open(self):
        """KEEP the fail-open contract: a missing OrderState (preview unavailable) -> OK."""
        assert runner._buying_power_ok(None) is True


class TestWhatIfShapes:
    """Real ib.whatIfOrder returns [OrderState] or [] — not a bare OrderState.
    Regression guard for the live bug where [] crashed _buying_power_ok."""

    def test_order_state_from_list(self):
        s = SimpleNamespace(initMarginAfter="1")
        assert runner._order_state([s]) is s

    def test_order_state_from_empty_list_is_none(self):
        assert runner._order_state([]) is None

    def test_order_state_from_single_object(self):
        s = SimpleNamespace(initMarginAfter="1")
        assert runner._order_state(s) is s

    def test_buying_power_ok_none_state_is_ok(self):
        assert runner._buying_power_ok(None) is True

    def test_empty_whatif_degrades_gracefully(self):
        # Read-only TWS: whatIf [] -> margin unavailable, NOT a crash, NOT a false-block.
        verdict, preview = runner.analyze_intent(
            ReadOnlyWhatIfIB(), _stk_intent(qty=1.0), _snap(), RulesConfig(),
            FakeLockoutStore(), now=_NOW,
        )
        assert preview["buying_power_ok"] is True
        assert preview["whatif_available"] is False
        assert preview["init_margin"] is None
        assert verdict.level is Verdict.GO

    def test_sentinel_init_margin_shows_none_not_dollar_figure(self):
        """[MEDIUM] When initMarginAfter is the IBKR UNSET sentinel (1.79e308), the preview
        must show init_margin=None — never surface the sentinel as a dollar figure.
        The whatIf is still 'available' (a real OrderState came back), distinguishing this
        from the read-only [] case."""
        verdict, preview = runner.analyze_intent(
            SentinelMarginIB(), _stk_intent(qty=1.0), _snap(), RulesConfig(),
            FakeLockoutStore(), now=_NOW,
        )
        assert preview["whatif_available"] is True
        assert preview["init_margin"] is None


# ---------------------------------------------------------------------------
# Unit: qualify raises a descriptive error when IB returns no contract
# ---------------------------------------------------------------------------

class TestQualifyEmptyResult:
    def test_empty_qualification_raises_value_error(self):
        class EmptyQualifyIB(FakeIB):
            def qualifyContracts(self, *contracts):
                return []

        with pytest.raises(ValueError, match="ZZZZ"):
            runner.qualify(EmptyQualifyIB(), _stk_intent(symbol="ZZZZ"))


# ---------------------------------------------------------------------------
# Realistic contract-qualification fakes (model ib_async 2.1.0 behavior).
#
# The OLD FakeIB.qualifyContracts is an identity function — it returns every
# contract unchanged. That hides three production-critical behaviors:
#   (a) an under-specified Future(symbol, exchange) with multiple listed
#       expiries causes the real ib.qualifyContracts(returnAll=False) to append
#       None to the result list (it does NOT pick an expiry for you),
#   (b) reqContractDetails returns one ContractDetails per listed expiry,
#   (c) an ambiguous stock (e.g. listed in multiple currencies) qualifies to
#       MORE THAN ONE contract.
# These fakes reproduce all three so qualify() is tested against reality.
# ---------------------------------------------------------------------------


def _fake_contract(secType="FUT", symbol="MNQ", exchange="CME",
                   lastTradeDateOrContractMonth="", currency="USD",
                   primaryExchange="", multiplier="2", conId=0):
    """A duck-typed ib_async Contract stand-in for qualification tests."""
    return SimpleNamespace(
        secType=secType,
        symbol=symbol,
        exchange=exchange,
        lastTradeDateOrContractMonth=lastTradeDateOrContractMonth,
        currency=currency,
        primaryExchange=primaryExchange,
        multiplier=multiplier,
        conId=conId,
    )


class RealisticQualifyIB(FakeIB):
    """Models ib_async 2.1.0 qualification semantics.

    - reqContractDetails(Future(symbol, exchange)) returns one ContractDetails
      per listed expiry (their .contract.secType == 'FUT').
    - qualifyContracts(Future) with an UNDER-SPECIFIED future (no expiry) yields
      [None] (the real lib can't disambiguate → appends None).
    - qualifyContracts(Future) with a SPECIFIC expiry yields [that contract].
    - qualifyContracts(Stock) yields all currency matches (≥1).
    """

    # Default MNQ ladder: three listed expiries, front = 20260619.
    _DEFAULT_FUT_EXPIRIES = ("20260918", "20260619", "20261218")

    def __init__(self, *, fut_expiries=None, stock_matches=1):
        super().__init__()
        self._fut_expiries = (
            self._DEFAULT_FUT_EXPIRIES if fut_expiries is None else tuple(fut_expiries)
        )
        self._stock_matches = stock_matches

    def reqContractDetails(self, contract):
        sym = getattr(contract, "symbol", None)
        exch = getattr(contract, "exchange", None) or "CME"
        details = []
        for i, exp in enumerate(self._fut_expiries):
            c = _fake_contract(secType="FUT", symbol=sym, exchange=exch,
                               lastTradeDateOrContractMonth=exp, conId=1000 + i)
            details.append(SimpleNamespace(contract=c))
        # A stray non-FUT detail (e.g. a combo) must be filtered out by _front_future.
        stray = _fake_contract(secType="BAG", symbol=sym, exchange=exch,
                               lastTradeDateOrContractMonth="20260619", conId=9999)
        details.append(SimpleNamespace(contract=stray))
        return details

    def qualifyContracts(self, *contracts):
        out = []
        for c in contracts:
            sec = getattr(c, "secType", None)
            if sec == "FUT":
                expiry = getattr(c, "lastTradeDateOrContractMonth", "") or ""
                if expiry:
                    # Specific expiry → resolves to exactly that contract.
                    out.append(c)
                else:
                    # Under-specified future → real lib appends None.
                    out.append(None)
            elif sec == "STK":
                sym = getattr(c, "symbol", "")
                for n in range(self._stock_matches):
                    out.append(_fake_contract(
                        secType="STK", symbol=sym, exchange="SMART",
                        currency=getattr(c, "currency", "USD") or "USD",
                        primaryExchange="NASDAQ", multiplier="", conId=2000 + n,
                    ))
            else:
                out.append(c)
        return out


# ---------------------------------------------------------------------------
# [Finding 1 — CRITICAL] Futures front-month selection.
# ---------------------------------------------------------------------------

class TestFrontMonthSelection:
    def test_front_month_is_earliest_non_expired_expiry(self, monkeypatch):
        # Pin "today" so the front month is deterministic. Earliest >= today.
        monkeypatch.setattr(runner, "_today_et", lambda: "20260601")
        ib = RealisticQualifyIB(fut_expiries=("20260918", "20260619", "20261218"))
        contract = runner.qualify(ib, _fut_intent(symbol="MNQ"))
        assert contract is not None
        assert contract.secType == "FUT"
        # 20260619 is the earliest expiry that is still >= 20260601.
        assert contract.lastTradeDateOrContractMonth == "20260619"

    def test_front_month_skips_expired_expiries(self, monkeypatch):
        # If "today" is past the nominal front, the next live expiry is chosen.
        monkeypatch.setattr(runner, "_today_et", lambda: "20260701")
        ib = RealisticQualifyIB(fut_expiries=("20260918", "20260619", "20261218"))
        contract = runner.qualify(ib, _fut_intent(symbol="MNQ"))
        # 20260619 is now expired → 20260918 is the earliest live expiry.
        assert contract.lastTradeDateOrContractMonth == "20260918"

    def test_all_expired_falls_back_to_earliest_overall(self, monkeypatch):
        # Degenerate: everything is in the past. Pick the earliest of the pool
        # anyway (don't crash; better an expired pick the user sees than a None).
        monkeypatch.setattr(runner, "_today_et", lambda: "20990101")
        ib = RealisticQualifyIB(fut_expiries=("20260918", "20260619", "20261218"))
        contract = runner.qualify(ib, _fut_intent(symbol="MNQ"))
        assert contract.lastTradeDateOrContractMonth == "20260619"

    def test_yyyymm_expiry_is_normalized(self, monkeypatch):
        # lastTradeDateOrContractMonth may be 'YYYYMM' — must still compare/pick.
        monkeypatch.setattr(runner, "_today_et", lambda: "20260601")
        ib = RealisticQualifyIB(fut_expiries=("202609", "202606", "202612"))
        contract = runner.qualify(ib, _fut_intent(symbol="MNQ"))
        assert contract.lastTradeDateOrContractMonth == "202606"

    def test_no_fut_contracts_raises(self, monkeypatch):
        monkeypatch.setattr(runner, "_today_et", lambda: "20260601")

        class NoFutDetailsIB(RealisticQualifyIB):
            def reqContractDetails(self, contract):
                # Only non-FUT details (e.g. an index/combo) → no usable future.
                sym = getattr(contract, "symbol", None)
                stray = _fake_contract(secType="IND", symbol=sym,
                                       lastTradeDateOrContractMonth="20260619")
                return [SimpleNamespace(contract=stray)]

        with pytest.raises(ValueError, match="No FUT contracts"):
            runner.qualify(NoFutDetailsIB(), _fut_intent(symbol="MNQ"))

    def test_front_month_unqualifiable_raises(self, monkeypatch):
        monkeypatch.setattr(runner, "_today_et", lambda: "20260601")

        class FrontUnqualifiableIB(RealisticQualifyIB):
            def qualifyContracts(self, *contracts):
                # Even the specific front-month can't be qualified → [None].
                return [None for _ in contracts]

        with pytest.raises(ValueError, match="qualify front-month"):
            runner.qualify(FrontUnqualifiableIB(), _fut_intent(symbol="MNQ"))

    def test_front_future_helper_today_is_monkeypatchable(self, monkeypatch):
        # The module-level _today_et must be a real, monkeypatchable seam.
        monkeypatch.setattr(runner, "_today_et", lambda: "20260601")
        ib = RealisticQualifyIB()
        c = runner._front_future(ib, "MNQ", "CME")
        assert c.lastTradeDateOrContractMonth == "20260619"


# ---------------------------------------------------------------------------
# [both branches] The real [None] payload must raise a CLEAR ValueError,
# not flow downstream into whatIfOrder/reqTickers/placeOrder.
# ---------------------------------------------------------------------------

class TestNonePayloadGuard:
    def test_none_only_payload_raises_clear_error(self):
        """qualifyContracts -> [None] (real lib for an under-specified contract).
        The OLD guard `if not [None]` is False, so this used to slip through and
        return None. Now it must raise."""
        class NoneStockIB(FakeIB):
            def qualifyContracts(self, *contracts):
                return [None]

        with pytest.raises(ValueError, match="AAPL"):
            runner.qualify(NoneStockIB(), _stk_intent(symbol="AAPL"))

    def test_none_payload_never_returns_none(self):
        class NoneStockIB(FakeIB):
            def qualifyContracts(self, *contracts):
                return [None]

        with pytest.raises(ValueError):
            runner.qualify(NoneStockIB(), _stk_intent(symbol="AAPL"))


# ---------------------------------------------------------------------------
# [Finding 2 — HIGH] _FUT_EXCHANGE: comprehensive map + fail-loud on unknown.
# ---------------------------------------------------------------------------

class TestFuturesExchangeMap:
    def test_unknown_futures_root_raises(self):
        # No silent CME guess for an unmapped root.
        with pytest.raises(ValueError, match="Unknown futures root"):
            runner.qualify(RealisticQualifyIB(), _fut_intent(symbol="ZZZ"))

    @pytest.mark.parametrize("symbol,exchange", [
        ("ES", "CME"), ("MES", "CME"), ("NQ", "CME"), ("MNQ", "CME"),
        ("RTY", "CME"), ("M2K", "CME"), ("6E", "CME"), ("6J", "CME"),
        ("CL", "NYMEX"), ("MCL", "NYMEX"), ("NG", "NYMEX"),
        ("GC", "COMEX"), ("MGC", "COMEX"), ("SI", "COMEX"), ("HG", "COMEX"),
        ("ZB", "CBOT"), ("ZN", "CBOT"), ("YM", "CBOT"), ("MYM", "CBOT"),
        ("ZC", "CBOT"), ("ZS", "CBOT"),
    ])
    def test_known_roots_map_to_correct_exchange(self, symbol, exchange, monkeypatch):
        monkeypatch.setattr(runner, "_today_et", lambda: "20260601")
        captured = {}

        class CaptureExchangeIB(RealisticQualifyIB):
            def reqContractDetails(self, contract):
                captured["exchange"] = getattr(contract, "exchange", None)
                return super().reqContractDetails(contract)

        runner.qualify(CaptureExchangeIB(), _fut_intent(symbol=symbol))
        assert captured["exchange"] == exchange


# ---------------------------------------------------------------------------
# [Finding 3 — MEDIUM] Stock qualification: strip Nones, single-match assertion,
# currency + primary_exchange threading.
# ---------------------------------------------------------------------------

class TestStockQualification:
    def test_single_match_succeeds(self):
        ib = RealisticQualifyIB(stock_matches=1)
        contract = runner.qualify(ib, _stk_intent(symbol="AAPL"))
        assert contract is not None
        assert contract.secType == "STK"
        assert contract.symbol == "AAPL"

    def test_snap_single_match_still_works(self):
        """Regression: the common case (SNAP resolves to ONE contract) must NOT
        be broken by the new ambiguity guard."""
        ib = RealisticQualifyIB(stock_matches=1)
        contract = runner.qualify(ib, _stk_intent(symbol="SNAP"))
        assert contract is not None
        assert contract.symbol == "SNAP"

    def test_ambiguous_stock_raises(self):
        ib = RealisticQualifyIB(stock_matches=2)
        with pytest.raises(ValueError, match="ambiguous"):
            runner.qualify(ib, _stk_intent(symbol="ABC"))

    def test_ambiguous_stock_error_mentions_count_and_remedy(self):
        ib = RealisticQualifyIB(stock_matches=3)
        with pytest.raises(ValueError, match="currency"):
            runner.qualify(ib, _stk_intent(symbol="ABC"))

    def test_currency_threaded_into_stock_contract(self):
        captured = {}

        class CaptureStockIB(RealisticQualifyIB):
            def qualifyContracts(self, *contracts):
                for c in contracts:
                    if getattr(c, "secType", None) == "STK":
                        captured["currency"] = getattr(c, "currency", None)
                        captured["primaryExchange"] = getattr(c, "primaryExchange", None)
                return super().qualifyContracts(*contracts)

        intent = OrderIntent(
            action=Action.BUY, symbol="RY", quantity=10.0,
            sec_type=SecType.STK, order_type=OrderType.MARKET,
            currency="CAD", primary_exchange="TSE",
        )
        runner.qualify(CaptureStockIB(stock_matches=1), intent)
        assert captured["currency"] == "CAD"
        assert captured["primaryExchange"] == "TSE"

    def test_empty_qualification_for_stock_raises(self):
        class EmptyStockIB(FakeIB):
            def qualifyContracts(self, *contracts):
                return []

        with pytest.raises(ValueError, match="QQQQ"):
            runner.qualify(EmptyStockIB(), _stk_intent(symbol="QQQQ"))


# ---------------------------------------------------------------------------
# [C2] analyze_intent must use the SAME live MNQ divisor as the daemon
#      (live_mnq_notional(ib)), not the static config.live.mnq_notional_usd.
# ---------------------------------------------------------------------------

class _MnqFakeIB(FakeIB):
    """A FakeIB whose qualifyContracts/reqTickers also answer for a ContFuture('MNQ')
    so live_mnq_notional(ib) returns a *live* notional distinct from the config default."""

    def __init__(self, mnq_price=22_000.0, mnq_mult="2"):
        super().__init__()
        self._mnq_price = mnq_price
        self._mnq_mult = mnq_mult

    def qualifyContracts(self, *contracts):
        # Delegate to the realistic base semantics (under-specified FUT -> None,
        # ContFuture/CONTFUT -> identity), then stamp the MNQ multiplier on any
        # surviving MNQ contract so live_mnq_notional can compute price × mult.
        out = super().qualifyContracts(*contracts)
        for c in out:
            if c is not None and getattr(c, "symbol", None) == "MNQ":
                c.multiplier = self._mnq_mult
        return out

    def reqTickers(self, *contracts):
        c = contracts[0]
        if getattr(c, "symbol", None) == "MNQ":
            price = self._mnq_price
            return [SimpleNamespace(marketPrice=lambda: price, last=price, close=price)]
        return super().reqTickers(*contracts)


class TestLiveMnqDivisor:
    def test_analyze_passes_live_mnq_into_hypothetical(self, monkeypatch):
        """The mnq_notional_usd handed to hypothetical_snapshot must be the LIVE value
        (price 22_000 × mult 2 = 44_000), NOT config.live.mnq_notional_usd (42_000)."""
        captured = {}
        real_hypo = runner.hypothetical_snapshot

        def spy(current, intent, notional, *, mnq_notional_usd=0.0, sector=None):
            captured["mnq"] = mnq_notional_usd
            return real_hypo(current, intent, notional,
                             mnq_notional_usd=mnq_notional_usd, sector=sector)

        monkeypatch.setattr(runner, "hypothetical_snapshot", spy)

        ib = _MnqFakeIB(mnq_price=22_000.0, mnq_mult="2")  # live = 44_000
        config = RulesConfig()  # config.live.mnq_notional_usd default = 42_000
        runner.analyze_intent(ib, _fut_intent(), _snap(), config, FakeLockoutStore(), now=_NOW)

        assert captured["mnq"] == pytest.approx(44_000.0)
        assert captured["mnq"] != pytest.approx(config.live.mnq_notional_usd)

    def test_analyze_falls_back_to_config_when_live_unavailable(self, monkeypatch):
        """When live_mnq_notional returns None, the static config default is used."""
        captured = {}
        real_hypo = runner.hypothetical_snapshot

        def spy(current, intent, notional, *, mnq_notional_usd=0.0, sector=None):
            captured["mnq"] = mnq_notional_usd
            return real_hypo(current, intent, notional,
                             mnq_notional_usd=mnq_notional_usd, sector=sector)

        monkeypatch.setattr(runner, "hypothetical_snapshot", spy)
        monkeypatch.setattr(runner, "live_mnq_notional", lambda ib: None)

        ib = FakeIB()
        config = RulesConfig()
        runner.analyze_intent(ib, _fut_intent(), _snap(), config, FakeLockoutStore(), now=_NOW)

        assert captured["mnq"] == pytest.approx(config.live.mnq_notional_usd)


# ---------------------------------------------------------------------------
# Case 1: Clean STK buy (small size, no breach, bp ok) → GO
#         analyze_intent does NOT call place_order
# ---------------------------------------------------------------------------

class TestCase1CleanStkBuy:
    def test_verdict_is_go(self):
        ib = FakeIB()
        config = RulesConfig()
        current = _snap()
        store = FakeLockoutStore()
        executor = FakeExecutor()

        verdict, preview = runner.analyze_intent(
            ib, _stk_intent(qty=1.0), current, config, store, now=_NOW
        )
        assert verdict.level is Verdict.GO

    def test_analyze_places_no_order(self):
        ib = FakeIB()
        config = RulesConfig()
        current = _snap()
        store = FakeLockoutStore()
        executor = FakeExecutor()

        runner.analyze_intent(
            ib, _stk_intent(qty=1.0), current, config, store, now=_NOW
        )
        # executor not even given to analyze_intent — it should not be called
        assert executor.calls == []


# ---------------------------------------------------------------------------
# Case 2: Oversized STK buy (notional > 1.5% NAV) → CAUTION, reason mentions %
#         At $145/share × 200 qty = $29,000 notional.
#         1.5% of $250k NAV = $3,750. $29k > $3,750 → over band.
# ---------------------------------------------------------------------------

class TestCase2OversizedStkBuy:
    def test_verdict_is_caution(self):
        ib = FakeIB()
        config = RulesConfig()
        current = _snap()
        store = FakeLockoutStore()

        verdict, preview = runner.analyze_intent(
            ib, _stk_intent(qty=200.0), current, config, store, now=_NOW
        )
        assert verdict.level is Verdict.CAUTION

    def test_reason_mentions_pct_nav(self):
        ib = FakeIB()
        config = RulesConfig()
        current = _snap()
        store = FakeLockoutStore()

        verdict, preview = runner.analyze_intent(
            ib, _stk_intent(qty=200.0), current, config, store, now=_NOW
        )
        # At least one reason should mention NAV %
        assert any("NAV" in r for r in verdict.reasons)


# ---------------------------------------------------------------------------
# Case 3: STK buy that pushes a name over the single-name cap → CAUTION (WARN trip)
#         single_name_pct default = 0.15 (15%).
#         Buy 10 × $145 = $1,450 notional / $250k NAV ≈ 0.58% extra weight.
#         Current weight 14.7% + 0.58% ≈ 15.28% > 15% → trips single_name WARN.
# ---------------------------------------------------------------------------

class TestCase3SingleNameBreach:
    def test_single_name_trip_in_preview(self):
        ib = FakeIB()
        config = RulesConfig()
        # Name already at 14.7%; buy pushes it ~0.4% higher → over 15% cap
        current = _snap(name_weights={"AAPL": 0.147}, name_exposure_signed={"AAPL": 0.147})
        store = FakeLockoutStore()

        verdict, preview = runner.analyze_intent(
            ib, _stk_intent(symbol="AAPL", qty=10.0), current, config, store, now=_NOW
        )

        trip_rule_ids = [t["rule_id"] for t in preview["trips"]]
        assert "equities.single_name" in trip_rule_ids

    def test_verdict_not_go(self):
        ib = FakeIB()
        config = RulesConfig()
        current = _snap(name_weights={"AAPL": 0.147}, name_exposure_signed={"AAPL": 0.147})
        store = FakeLockoutStore()

        verdict, preview = runner.analyze_intent(
            ib, _stk_intent(symbol="AAPL", qty=10.0), current, config, store, now=_NOW
        )
        # WARN trip → CAUTION (or oversized also triggers; either way, not GO)
        assert verdict.level is not Verdict.GO


# ---------------------------------------------------------------------------
# Case 4: Insufficient buying power → BLOCK
# ---------------------------------------------------------------------------

class TestCase4InsufficientBuyingPower:
    def test_block_on_insufficient_bp(self):
        ib = InsufficientBPFakeIB()
        config = RulesConfig()
        current = _snap()
        store = FakeLockoutStore()

        verdict, preview = runner.analyze_intent(
            ib, _stk_intent(qty=1.0), current, config, store, now=_NOW
        )
        assert verdict.level is Verdict.BLOCK

    def test_preview_buying_power_false(self):
        ib = InsufficientBPFakeIB()
        config = RulesConfig()
        current = _snap()
        store = FakeLockoutStore()

        _, preview = runner.analyze_intent(
            ib, _stk_intent(qty=1.0), current, config, store, now=_NOW
        )
        assert preview["buying_power_ok"] is False


# ---------------------------------------------------------------------------
# Case 5a: platform_off_today lockout → BLOCK for STK intent
# Case 5b: futures_48h lockout → BLOCK for FUT but NOT for STK
# ---------------------------------------------------------------------------

class TestCase5Lockouts:
    def test_platform_off_blocks_stk(self):
        ib = FakeIB()
        config = RulesConfig()
        current = _snap()
        lockout = Lockout(kind="platform_off_today",
                          until=_NOW + dt.timedelta(hours=8),
                          reason="platform off")
        store = FakeLockoutStore(lockout=lockout)

        verdict, _ = runner.analyze_intent(
            ib, _stk_intent(qty=1.0), current, config, store, now=_NOW
        )
        assert verdict.level is Verdict.BLOCK

    def test_futures_48h_blocks_fut(self):
        ib = FakeIB()
        config = RulesConfig()
        current = _snap()
        lockout = Lockout(kind="futures_48h",
                          until=_NOW + dt.timedelta(hours=24),
                          reason="48h cooling off")
        store = FakeLockoutStore(lockout=lockout)

        verdict, _ = runner.analyze_intent(
            ib, _fut_intent(), current, config, store, now=_NOW
        )
        assert verdict.level is Verdict.BLOCK

    def test_futures_48h_does_not_block_stk(self):
        ib = FakeIB()
        config = RulesConfig()
        current = _snap()
        lockout = Lockout(kind="futures_48h",
                          until=_NOW + dt.timedelta(hours=24),
                          reason="48h cooling off")
        store = FakeLockoutStore(lockout=lockout)

        verdict, _ = runner.analyze_intent(
            ib, _stk_intent(qty=1.0), current, config, store, now=_NOW
        )
        # futures_48h does NOT block STK trades
        assert verdict.level is not Verdict.BLOCK


# ---------------------------------------------------------------------------
# Case 6: Corrupt lockout file → fail CLOSED (lockout_active=True → BLOCK)
# ---------------------------------------------------------------------------

class TestCase6CorruptLockoutFile:
    def test_corrupt_lockout_blocks(self):
        ib = FakeIB()
        config = RulesConfig()
        current = _snap()
        store = FakeLockoutStore(raises=True)

        verdict, preview = runner.analyze_intent(
            ib, _stk_intent(qty=1.0), current, config, store, now=_NOW
        )
        assert verdict.level is Verdict.BLOCK

    def test_corrupt_lockout_preview_shows_active(self):
        ib = FakeIB()
        config = RulesConfig()
        current = _snap()
        store = FakeLockoutStore(raises=True)

        _, preview = runner.analyze_intent(
            ib, _stk_intent(qty=1.0), current, config, store, now=_NOW
        )
        assert preview["lockout_active"] is True


# ---------------------------------------------------------------------------
# Case 7: MARKET order with no limit_price uses reqTickers price for notional
#         Fake ticker returns 145.0. qty=1, multiplier=1 → notional=$145.
# ---------------------------------------------------------------------------

class TestCase7MarketOrderUsesLivePrice:
    def test_order_notional_from_ticker(self):
        ib = FakeIB()
        config = RulesConfig()
        current = _snap()
        store = FakeLockoutStore()

        _, preview = runner.analyze_intent(
            ib, _stk_intent(qty=1.0, order_type=OrderType.MARKET), current, config, store, now=_NOW
        )
        # Notional = 1 qty × $145 price = $145
        assert preview["order_notional"] == pytest.approx(145.0)


# ---------------------------------------------------------------------------
# Case 8: submit_intent calls executor.place_order exactly once, returns result
# ---------------------------------------------------------------------------

class TestCase8SubmitIntent:
    def test_calls_executor_once(self):
        ib = FakeIB()
        executor = FakeExecutor(return_value=True)

        result = runner.submit_intent(ib, executor, _stk_intent(qty=1.0))

        assert len(executor.calls) == 1
        assert result is True

    def test_executor_false_propagated(self):
        ib = FakeIB()
        executor = FakeExecutor(return_value=False)

        result = runner.submit_intent(ib, executor, _stk_intent(qty=1.0))

        assert result is False


# ---------------------------------------------------------------------------
# Case 9: preview dict is JSON-serializable and contains verdict + reasons
# ---------------------------------------------------------------------------

class TestCase9PreviewJsonSerializable:
    def test_json_dumps_succeeds(self):
        ib = FakeIB()
        config = RulesConfig()
        current = _snap()
        store = FakeLockoutStore()

        _, preview = runner.analyze_intent(
            ib, _stk_intent(qty=1.0), current, config, store, now=_NOW
        )
        # Must not raise
        serialized = json.dumps(preview)
        parsed = json.loads(serialized)
        assert isinstance(parsed, dict)

    def test_preview_contains_required_keys(self):
        ib = FakeIB()
        config = RulesConfig()
        current = _snap()
        store = FakeLockoutStore()

        verdict, preview = runner.analyze_intent(
            ib, _stk_intent(qty=1.0), current, config, store, now=_NOW
        )
        required_keys = {
            "symbol", "action", "quantity", "order_type", "order_notional",
            "pct_nav", "buying_power_ok", "whatif_available", "init_margin", "name_weight_before",
            "name_weight_after", "trips", "lockout_active", "verdict", "reasons",
        }
        assert required_keys.issubset(set(preview.keys()))

    def test_preview_verdict_matches_returned_verdict(self):
        ib = FakeIB()
        config = RulesConfig()
        current = _snap()
        store = FakeLockoutStore()

        verdict, preview = runner.analyze_intent(
            ib, _stk_intent(qty=1.0), current, config, store, now=_NOW
        )
        assert preview["verdict"] == verdict.level.value

    def test_preview_reasons_matches_returned_verdict(self):
        ib = FakeIB()
        config = RulesConfig()
        current = _snap()
        store = FakeLockoutStore()

        verdict, preview = runner.analyze_intent(
            ib, _stk_intent(qty=1.0), current, config, store, now=_NOW
        )
        assert preview["reasons"] == list(verdict.reasons)


# ---------------------------------------------------------------------------
# build_bracket tests
# ---------------------------------------------------------------------------

def _bracket_intent(action=Action.BUY, stop_loss=None, take_profit=None):
    return OrderIntent(
        action=action,
        symbol="AAPL",
        quantity=10.0,
        sec_type=SecType.STK,
        order_type=OrderType.LIMIT,
        limit_price=150.0,
        stop_loss=stop_loss,
        take_profit=take_profit,
    )


class TestBuildBracket:
    def test_no_protective_returns_one_order_transmit_true(self):
        ib = FakeIB()
        from ib_async import Stock
        contract = Stock("AAPL", "SMART", "USD")
        intent = _bracket_intent()
        orders = runner.build_bracket(ib, intent, contract)
        assert len(orders) == 1
        assert orders[0].transmit is True

    def test_sl_only_returns_two_orders(self):
        ib = FakeIB()
        from ib_async import Stock
        contract = Stock("AAPL", "SMART", "USD")
        intent = _bracket_intent(stop_loss=140.0)
        orders = runner.build_bracket(ib, intent, contract)
        assert len(orders) == 2

    def test_sl_only_parent_transmit_false(self):
        ib = FakeIB()
        from ib_async import Stock
        contract = Stock("AAPL", "SMART", "USD")
        intent = _bracket_intent(stop_loss=140.0)
        orders = runner.build_bracket(ib, intent, contract)
        parent, sl = orders
        assert parent.transmit is False

    def test_sl_only_child_transmit_true(self):
        ib = FakeIB()
        from ib_async import Stock
        contract = Stock("AAPL", "SMART", "USD")
        intent = _bracket_intent(stop_loss=140.0)
        orders = runner.build_bracket(ib, intent, contract)
        parent, sl = orders
        assert sl.transmit is True

    def test_sl_only_child_is_opposite_side(self):
        ib = FakeIB()
        from ib_async import Stock
        contract = Stock("AAPL", "SMART", "USD")
        intent = _bracket_intent(action=Action.BUY, stop_loss=140.0)
        orders = runner.build_bracket(ib, intent, contract)
        parent, sl = orders
        assert sl.action == "SELL"

    def test_sl_only_child_parentId_links_to_parent(self):
        ib = FakeIB()
        from ib_async import Stock
        contract = Stock("AAPL", "SMART", "USD")
        intent = _bracket_intent(stop_loss=140.0)
        orders = runner.build_bracket(ib, intent, contract)
        parent, sl = orders
        assert sl.parentId == parent.orderId

    def test_sl_only_child_is_stop_order_at_stop_price(self):
        ib = FakeIB()
        from ib_async import Stock, StopOrder
        contract = Stock("AAPL", "SMART", "USD")
        intent = _bracket_intent(stop_loss=140.0)
        orders = runner.build_bracket(ib, intent, contract)
        parent, sl = orders
        assert sl.orderType == "STP"
        assert sl.auxPrice == 140.0

    def test_sl_and_tp_returns_three_orders(self):
        ib = FakeIB()
        from ib_async import Stock
        contract = Stock("AAPL", "SMART", "USD")
        intent = _bracket_intent(stop_loss=140.0, take_profit=165.0)
        orders = runner.build_bracket(ib, intent, contract)
        assert len(orders) == 3

    def test_sl_and_tp_all_parentIds_link_to_parent(self):
        ib = FakeIB()
        from ib_async import Stock
        contract = Stock("AAPL", "SMART", "USD")
        intent = _bracket_intent(stop_loss=140.0, take_profit=165.0)
        orders = runner.build_bracket(ib, intent, contract)
        parent, tp, sl = orders
        assert tp.parentId == parent.orderId
        assert sl.parentId == parent.orderId

    def test_sl_and_tp_only_last_transmits(self):
        ib = FakeIB()
        from ib_async import Stock
        contract = Stock("AAPL", "SMART", "USD")
        intent = _bracket_intent(stop_loss=140.0, take_profit=165.0)
        orders = runner.build_bracket(ib, intent, contract)
        parent, tp, sl = orders
        assert parent.transmit is False
        assert tp.transmit is False
        assert sl.transmit is True

    def test_sl_and_tp_oca_group_set_on_children(self):
        ib = FakeIB()
        from ib_async import Stock
        contract = Stock("AAPL", "SMART", "USD")
        intent = _bracket_intent(stop_loss=140.0, take_profit=165.0)
        orders = runner.build_bracket(ib, intent, contract)
        parent, tp, sl = orders
        assert tp.ocaGroup == sl.ocaGroup
        assert tp.ocaGroup.startswith("oca_")
        assert tp.ocaType == 1
        assert sl.ocaType == 1

    def test_sl_and_tp_tp_is_limit_order(self):
        ib = FakeIB()
        from ib_async import Stock
        contract = Stock("AAPL", "SMART", "USD")
        intent = _bracket_intent(stop_loss=140.0, take_profit=165.0)
        orders = runner.build_bracket(ib, intent, contract)
        parent, tp, sl = orders
        assert tp.orderType == "LMT"
        assert tp.lmtPrice == 165.0

    def test_sell_intent_children_are_buy_side(self):
        ib = FakeIB()
        from ib_async import Stock
        contract = Stock("AAPL", "SMART", "USD")
        intent = _bracket_intent(action=Action.SELL, stop_loss=160.0, take_profit=135.0)
        orders = runner.build_bracket(ib, intent, contract)
        for child in orders[1:]:
            assert child.action == "BUY"


# ---------------------------------------------------------------------------
# [HIGH] TIF on bracket: protective children must outlive the session (GTC),
# entry parent keeps the entry tif.
# ---------------------------------------------------------------------------

class TestBracketTif:
    def test_protective_children_default_to_gtc(self):
        ib = FakeIB()
        from ib_async import Stock
        contract = Stock("AAPL", "SMART", "USD")
        intent = _bracket_intent(stop_loss=140.0, take_profit=165.0)
        parent, tp, sl = runner.build_bracket(ib, intent, contract)
        assert tp.tif == "GTC"
        assert sl.tif == "GTC"

    def test_protective_tif_is_configurable(self):
        ib = FakeIB()
        from ib_async import Stock
        contract = Stock("AAPL", "SMART", "USD")
        intent = OrderIntent(
            action=Action.BUY, symbol="AAPL", quantity=10.0, sec_type=SecType.STK,
            order_type=OrderType.LIMIT, limit_price=150.0, stop_loss=140.0,
            protective_tif="DAY",
        )
        parent, sl = runner.build_bracket(ib, intent, contract)
        assert sl.tif == "DAY"

    def test_entry_parent_keeps_entry_tif(self):
        ib = FakeIB()
        from ib_async import Stock
        contract = Stock("AAPL", "SMART", "USD")
        intent = OrderIntent(
            action=Action.BUY, symbol="AAPL", quantity=10.0, sec_type=SecType.STK,
            order_type=OrderType.LIMIT, limit_price=150.0, stop_loss=140.0,
            tif="DAY", protective_tif="GTC",
        )
        parent, sl = runner.build_bracket(ib, intent, contract)
        assert parent.tif == "DAY"
        assert sl.tif == "GTC"


# ---------------------------------------------------------------------------
# [feature] Adaptive rides on the bracket PARENT only — never on the
# StopOrder/LimitOrder protective children (TWS rejects Adaptive on a STP child).
# ---------------------------------------------------------------------------

class TestBracketAdaptive:
    def test_adaptive_on_parent_not_on_children(self):
        ib = FakeIB()
        from ib_async import Stock
        contract = Stock("AAPL", "SMART", "USD")
        intent = OrderIntent(
            action=Action.BUY, symbol="AAPL", quantity=10.0, sec_type=SecType.STK,
            order_type=OrderType.LIMIT, limit_price=150.0,
            stop_loss=140.0, take_profit=165.0, adaptive=True,
        )
        parent, tp, sl = runner.build_bracket(ib, intent, contract)
        assert parent.algoStrategy == "Adaptive"
        assert tp.algoStrategy == ""
        assert tp.algoParams == []
        assert sl.algoStrategy == ""
        assert sl.algoParams == []


# ---------------------------------------------------------------------------
# submit_intent bracket routing tests
# ---------------------------------------------------------------------------

class TestSubmitIntentBracketRouting:
    def test_with_stop_loss_calls_place_orders(self):
        ib = FakeIB()
        executor = FakeExecutor(return_value=True)
        intent = _bracket_intent(stop_loss=140.0)
        result = runner.submit_intent(ib, executor, intent)
        assert len(executor.bracket_calls) == 1
        assert len(executor.calls) == 0
        assert result is True

    def test_with_take_profit_only_calls_place_orders(self):
        ib = FakeIB()
        executor = FakeExecutor(return_value=True)
        intent = _bracket_intent(take_profit=165.0)
        result = runner.submit_intent(ib, executor, intent)
        assert len(executor.bracket_calls) == 1
        assert len(executor.calls) == 0

    def test_with_both_calls_place_orders_with_three_orders(self):
        ib = FakeIB()
        executor = FakeExecutor(return_value=True)
        intent = _bracket_intent(stop_loss=140.0, take_profit=165.0)
        runner.submit_intent(ib, executor, intent)
        assert len(executor.bracket_calls) == 1
        _contract, orders = executor.bracket_calls[0]
        assert len(orders) == 3

    def test_without_protective_calls_place_order(self):
        ib = FakeIB()
        executor = FakeExecutor(return_value=True)
        intent = _bracket_intent()  # no stop_loss, no take_profit
        runner.submit_intent(ib, executor, intent)
        assert len(executor.calls) == 1
        assert len(executor.bracket_calls) == 0


# ---------------------------------------------------------------------------
# analyze_intent preview with bracket fields
# ---------------------------------------------------------------------------

class TestAnalyzeIntentBracketPreview:
    def test_preview_includes_stop_loss_when_set(self):
        ib = FakeIB()
        config = RulesConfig()
        current = _snap()
        store = FakeLockoutStore()
        intent = OrderIntent(
            action=Action.BUY, symbol="AAPL", quantity=10.0,
            sec_type=SecType.STK, order_type=OrderType.LIMIT,
            limit_price=150.0, stop_loss=140.0,
        )
        _, preview = runner.analyze_intent(ib, intent, current, config, store, now=_NOW)
        assert preview["stop_loss"] == 140.0

    def test_preview_includes_take_profit_when_set(self):
        ib = FakeIB()
        config = RulesConfig()
        current = _snap()
        store = FakeLockoutStore()
        intent = OrderIntent(
            action=Action.BUY, symbol="AAPL", quantity=10.0,
            sec_type=SecType.STK, order_type=OrderType.LIMIT,
            limit_price=150.0, take_profit=165.0,
        )
        _, preview = runner.analyze_intent(ib, intent, current, config, store, now=_NOW)
        assert preview["take_profit"] == 165.0

    def test_preview_includes_risk_usd_when_stop_loss_set(self):
        ib = FakeIB()
        config = RulesConfig()
        current = _snap()
        store = FakeLockoutStore()
        # limit_price = 150.0 → reference price = 150.0; stop_loss = 140.0
        # risk = abs(150.0 - 140.0) * 10 * 1 = 100.0
        intent = OrderIntent(
            action=Action.BUY, symbol="AAPL", quantity=10.0,
            sec_type=SecType.STK, order_type=OrderType.LIMIT,
            limit_price=150.0, stop_loss=140.0,
        )
        _, preview = runner.analyze_intent(ib, intent, current, config, store, now=_NOW)
        assert preview["risk_usd"] == pytest.approx(100.0)

    def test_preview_no_protective_fields_absent(self):
        ib = FakeIB()
        config = RulesConfig()
        current = _snap()
        store = FakeLockoutStore()
        intent = _stk_intent(qty=1.0)
        _, preview = runner.analyze_intent(ib, intent, current, config, store, now=_NOW)
        assert "stop_loss" not in preview
        assert "take_profit" not in preview
        assert "risk_usd" not in preview
