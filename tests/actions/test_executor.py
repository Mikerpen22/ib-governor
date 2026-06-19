# tests/actions/test_executor.py
import datetime as dt
from types import SimpleNamespace

from governor.actions.executor import ActionExecutor
from governor.actions.lockout import LockoutStore

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 6, 17, 11, 0, tzinfo=UTC)


class FakeIB:
    def __init__(self):
        self.global_cancels = 0
        self.placed = []
    def reqGlobalCancel(self):
        self.global_cancels += 1
    def placeOrder(self, contract, order):
        self.placed.append((contract, order))
    def positions(self):
        return [SimpleNamespace(contract=SimpleNamespace(secType="FUT", localSymbol="MNQU6"),
                                position=6.0, avgCost=0.0)]


def _executor(ib, dry_run, tmp_path):
    return ActionExecutor(ib=ib, dry_run=dry_run,
                          lockout_store=LockoutStore(tmp_path / "l.json"))


def test_dry_run_blocks_global_cancel(tmp_path):
    ib = FakeIB()
    ex = _executor(ib, dry_run=True, tmp_path=tmp_path)
    executed = ex.cancel_all_orders()
    assert executed is False and ib.global_cancels == 0  # logged, not executed


def test_armed_executes_global_cancel(tmp_path):
    ib = FakeIB()
    ex = _executor(ib, dry_run=False, tmp_path=tmp_path)
    executed = ex.cancel_all_orders()
    assert executed is True and ib.global_cancels == 1


def test_lockout_sets_state_even_in_dry_run_but_no_account_call(tmp_path):
    ib = FakeIB()
    ex = _executor(ib, dry_run=True, tmp_path=tmp_path)
    ex.lockout(kind="futures_48h", until=T0 + dt.timedelta(hours=48), reason="house money", now=T0)
    # state is bookkeeping (not an account action) so it's set even in dry-run
    assert ex.lockout_store.active(T0 + dt.timedelta(hours=1)) is not None
    # but the cancel that accompanies it was NOT executed in dry-run
    assert ib.global_cancels == 0


def test_armed_lockout_cancels(tmp_path):
    ib = FakeIB()
    ex = _executor(ib, dry_run=False, tmp_path=tmp_path)
    ex.lockout(kind="futures_48h", until=T0 + dt.timedelta(hours=48), reason="r", now=T0)
    assert ib.global_cancels == 1
    assert ex.lockout_store.active(T0 + dt.timedelta(hours=1)) is not None


def test_trim_places_reducing_order_when_armed(tmp_path):
    ib = FakeIB()
    ex = _executor(ib, dry_run=False, tmp_path=tmp_path)
    # hold 6, target 2 -> sell 4
    ex.trim_futures(target_contracts=2)
    assert len(ib.placed) == 1
    contract, order = ib.placed[0]
    assert order.action == "SELL" and order.totalQuantity == 4.0


def test_trim_blocked_in_dry_run(tmp_path):
    ib = FakeIB()
    ex = _executor(ib, dry_run=True, tmp_path=tmp_path)
    ex.trim_futures(target_contracts=2)
    assert ib.placed == []


# ── place_order ────────────────────────────────────────────────────────────────

class FakeContract:
    def __init__(self, symbol="AAPL", local_symbol="AAPL"):
        self.symbol = symbol
        self.localSymbol = local_symbol
        self.secType = "STK"


class FakeOrder:
    def __init__(self, action="BUY", qty=10, order_type="MKT"):
        self.action = action
        self.totalQuantity = qty
        self.orderType = order_type


def test_place_order_dry_run_returns_false_no_call(tmp_path):
    """place_order under dry_run=True returns False and does NOT call ib.placeOrder."""
    ib = FakeIB()
    ex = _executor(ib, dry_run=True, tmp_path=tmp_path)
    contract = FakeContract()
    order = FakeOrder()
    result = ex.place_order(contract, order)
    assert result is False
    assert ib.placed == []


def test_place_order_armed_returns_true_calls_once(tmp_path):
    """place_order when armed returns True and calls ib.placeOrder exactly once."""
    ib = FakeIB()
    ex = _executor(ib, dry_run=False, tmp_path=tmp_path)
    contract = FakeContract(symbol="NVDA")
    order = FakeOrder(action="SELL", qty=5)
    result = ex.place_order(contract, order)
    assert result is True
    assert len(ib.placed) == 1
    placed_contract, placed_order = ib.placed[0]
    assert placed_contract is contract
    assert placed_order is order


# ── place_orders (bracket) ─────────────────────────────────────────────────────

def test_place_orders_dry_run_returns_false_no_call(tmp_path):
    """place_orders under dry_run returns False and does NOT call ib.placeOrder."""
    ib = FakeIB()
    ex = _executor(ib, dry_run=True, tmp_path=tmp_path)
    contract = FakeContract()
    orders = [FakeOrder(action="BUY", qty=10), FakeOrder(action="SELL", qty=10)]
    result = ex.place_orders(contract, orders)
    assert result is False
    assert ib.placed == []


def test_place_orders_armed_returns_true_calls_each_order(tmp_path):
    """place_orders when armed returns True and calls ib.placeOrder for every order."""
    ib = FakeIB()
    ex = _executor(ib, dry_run=False, tmp_path=tmp_path)
    contract = FakeContract(symbol="AAPL")
    order1 = FakeOrder(action="BUY", qty=10)
    order2 = FakeOrder(action="SELL", qty=10)
    order3 = FakeOrder(action="SELL", qty=10)
    result = ex.place_orders(contract, [order1, order2, order3])
    assert result is True
    assert len(ib.placed) == 3
    placed_orders = [o for _, o in ib.placed]
    assert order1 in placed_orders
    assert order2 in placed_orders
    assert order3 in placed_orders
