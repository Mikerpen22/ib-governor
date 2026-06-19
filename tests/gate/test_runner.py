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


class FakeIB:
    """Minimal ib_async substitute — no TWS required."""

    def __init__(self):
        self.client = _make_req_id_counter()

    def qualifyContracts(self, *contracts):
        # Each contract is returned as-is (already has the fields it needs)
        return list(contracts)

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
        current = _snap(name_weights={"AAPL": 0.147})
        store = FakeLockoutStore()

        verdict, preview = runner.analyze_intent(
            ib, _stk_intent(symbol="AAPL", qty=10.0), current, config, store, now=_NOW
        )

        trip_rule_ids = [t["rule_id"] for t in preview["trips"]]
        assert "equities.single_name" in trip_rule_ids

    def test_verdict_not_go(self):
        ib = FakeIB()
        config = RulesConfig()
        current = _snap(name_weights={"AAPL": 0.147})
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
