import pytest
from pydantic import ValidationError
from governor.gate.intent import OrderIntent, OrderType, Action, SecType, build_order


def test_market_order_needs_no_prices():
    i = OrderIntent(action=Action.BUY, symbol="ORCL", quantity=50,
                    sec_type=SecType.STK, order_type=OrderType.MARKET)
    o = build_order(i)
    assert o.action == "BUY" and o.totalQuantity == 50 and o.orderType == "MKT"


def test_limit_requires_limit_price():
    with pytest.raises(ValidationError) as exc_info:
        OrderIntent(action=Action.BUY, symbol="ORCL", quantity=50,
                    sec_type=SecType.STK, order_type=OrderType.LIMIT)
    assert "limit_price" in str(exc_info.value)


def test_stop_limit_builds_both_prices():
    i = OrderIntent(action=Action.SELL, symbol="ORCL", quantity=10, sec_type=SecType.STK,
                    order_type=OrderType.STOP_LIMIT, limit_price=140.0, stop_price=141.0)
    o = build_order(i)
    assert o.orderType == "STP LMT" and o.lmtPrice == 140.0 and o.auxPrice == 141.0


def test_stop_requires_stop_price():
    with pytest.raises(ValidationError) as exc_info:
        OrderIntent(action=Action.BUY, symbol="ORCL", quantity=10,
                    sec_type=SecType.STK, order_type=OrderType.STOP)
    assert "stop_price" in str(exc_info.value)


def test_quantity_zero_raises():
    with pytest.raises(ValidationError) as exc_info:
        OrderIntent(action=Action.BUY, symbol="ORCL", quantity=0,
                    sec_type=SecType.STK, order_type=OrderType.MARKET)
    assert "quantity" in str(exc_info.value)


def test_quantity_negative_raises():
    with pytest.raises(ValidationError) as exc_info:
        OrderIntent(action=Action.SELL, symbol="ORCL", quantity=-5,
                    sec_type=SecType.STK, order_type=OrderType.MARKET)
    assert "quantity" in str(exc_info.value)


def test_limit_builds_lmt_order():
    i = OrderIntent(action=Action.BUY, symbol="ORCL", quantity=25,
                    sec_type=SecType.STK, order_type=OrderType.LIMIT, limit_price=99.50)
    o = build_order(i)
    assert o.orderType == "LMT" and o.lmtPrice == 99.50


def test_sell_market_builds_sell_action():
    i = OrderIntent(action=Action.SELL, symbol="ORCL", quantity=10,
                    sec_type=SecType.STK, order_type=OrderType.MARKET)
    o = build_order(i)
    assert o.action == "SELL" and o.orderType == "MKT"


def test_stop_order_builds_correctly():
    i = OrderIntent(action=Action.SELL, symbol="ORCL", quantity=15,
                    sec_type=SecType.STK, order_type=OrderType.STOP, stop_price=95.0)
    o = build_order(i)
    assert o.orderType == "STP" and o.auxPrice == 95.0


def test_futures_market_order_builds():
    i = OrderIntent(action=Action.BUY, symbol="MNQ", quantity=2,
                    sec_type=SecType.FUT, order_type=OrderType.MARKET)
    o = build_order(i)
    assert o.action == "BUY" and o.totalQuantity == 2 and o.orderType == "MKT"


def test_intent_accepts_stop_loss():
    i = OrderIntent(action=Action.BUY, symbol="ORCL", quantity=10,
                    sec_type=SecType.STK, order_type=OrderType.LIMIT,
                    limit_price=100.0, stop_loss=95.0)
    assert i.stop_loss == 95.0


def test_intent_accepts_take_profit():
    i = OrderIntent(action=Action.BUY, symbol="ORCL", quantity=10,
                    sec_type=SecType.STK, order_type=OrderType.LIMIT,
                    limit_price=100.0, take_profit=110.0)
    assert i.take_profit == 110.0


def test_intent_accepts_both_protective_prices():
    i = OrderIntent(action=Action.BUY, symbol="ORCL", quantity=10,
                    sec_type=SecType.STK, order_type=OrderType.LIMIT,
                    limit_price=100.0, stop_loss=95.0, take_profit=110.0)
    assert i.stop_loss == 95.0 and i.take_profit == 110.0


def test_intent_protective_prices_default_none():
    i = OrderIntent(action=Action.BUY, symbol="ORCL", quantity=10,
                    sec_type=SecType.STK, order_type=OrderType.MARKET)
    assert i.stop_loss is None
    assert i.take_profit is None
