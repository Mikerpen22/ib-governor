import pytest
from ib_async import TagValue
from pydantic import ValidationError
from governor.gate.intent import (
    AdaptivePriority,
    OrderIntent,
    OrderType,
    Action,
    SecType,
    build_order,
)


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


# ── [Finding 3] currency / primary_exchange disambiguation fields ──

def test_intent_currency_defaults_to_usd():
    i = OrderIntent(action=Action.BUY, symbol="ORCL", quantity=10,
                    sec_type=SecType.STK, order_type=OrderType.MARKET)
    assert i.currency == "USD"


def test_intent_primary_exchange_defaults_to_none():
    i = OrderIntent(action=Action.BUY, symbol="ORCL", quantity=10,
                    sec_type=SecType.STK, order_type=OrderType.MARKET)
    assert i.primary_exchange is None


def test_intent_accepts_currency_and_primary_exchange():
    i = OrderIntent(action=Action.BUY, symbol="RY", quantity=10,
                    sec_type=SecType.STK, order_type=OrderType.MARKET,
                    currency="CAD", primary_exchange="TSE")
    assert i.currency == "CAD"
    assert i.primary_exchange == "TSE"


# ── [HIGH] TIF — protective stops must outlive the session ──

def test_intent_tif_defaults_to_day():
    i = OrderIntent(action=Action.BUY, symbol="ORCL", quantity=10,
                    sec_type=SecType.STK, order_type=OrderType.MARKET)
    assert i.tif == "DAY"


def test_intent_protective_tif_defaults_to_gtc():
    i = OrderIntent(action=Action.BUY, symbol="ORCL", quantity=10,
                    sec_type=SecType.STK, order_type=OrderType.MARKET)
    assert i.protective_tif == "GTC"


def test_build_order_sets_tif_on_market():
    i = OrderIntent(action=Action.BUY, symbol="ORCL", quantity=10,
                    sec_type=SecType.STK, order_type=OrderType.MARKET, tif="GTC")
    assert build_order(i).tif == "GTC"


def test_build_order_sets_default_day_tif():
    i = OrderIntent(action=Action.BUY, symbol="ORCL", quantity=10,
                    sec_type=SecType.STK, order_type=OrderType.LIMIT, limit_price=99.0)
    assert build_order(i).tif == "DAY"


def test_build_order_sets_tif_on_stop():
    i = OrderIntent(action=Action.SELL, symbol="ORCL", quantity=10, sec_type=SecType.STK,
                    order_type=OrderType.STOP, stop_price=95.0, tif="GTC")
    assert build_order(i).tif == "GTC"


# ── [feature] IBKR Adaptive algo (layered on MKT/LMT base orders) ──

def test_intent_adaptive_defaults_off():
    i = OrderIntent(action=Action.BUY, symbol="ORCL", quantity=10,
                    sec_type=SecType.STK, order_type=OrderType.MARKET)
    assert i.adaptive is False
    assert i.adaptive_priority is AdaptivePriority.NORMAL


def test_adaptive_market_order_keeps_mkt_type_and_adds_algo():
    i = OrderIntent(action=Action.BUY, symbol="ORCL", quantity=10,
                    sec_type=SecType.STK, order_type=OrderType.MARKET, adaptive=True)
    o = build_order(i)
    assert o.orderType == "MKT"
    assert o.algoStrategy == "Adaptive"
    assert o.algoParams == [TagValue("adaptivePriority", "Normal")]


def test_adaptive_limit_order_keeps_lmt_type_and_adds_algo():
    i = OrderIntent(action=Action.BUY, symbol="ORCL", quantity=10, sec_type=SecType.STK,
                    order_type=OrderType.LIMIT, limit_price=99.0, adaptive=True,
                    adaptive_priority=AdaptivePriority.URGENT)
    o = build_order(i)
    assert o.orderType == "LMT"
    assert o.algoStrategy == "Adaptive"
    assert o.algoParams == [TagValue("adaptivePriority", "Urgent")]


def test_non_adaptive_order_has_no_algo():
    i = OrderIntent(action=Action.BUY, symbol="ORCL", quantity=10,
                    sec_type=SecType.STK, order_type=OrderType.MARKET)
    o = build_order(i)
    assert o.algoStrategy == ""
    assert o.algoParams == []


def test_adaptive_on_stop_intent_raises():
    with pytest.raises(ValidationError) as exc_info:
        OrderIntent(action=Action.SELL, symbol="ORCL", quantity=10, sec_type=SecType.STK,
                    order_type=OrderType.STOP, stop_price=95.0, adaptive=True)
    assert "adaptive is only valid for MARKET or LIMIT" in str(exc_info.value)


def test_adaptive_on_stop_limit_intent_raises():
    with pytest.raises(ValidationError) as exc_info:
        OrderIntent(action=Action.SELL, symbol="ORCL", quantity=10, sec_type=SecType.STK,
                    order_type=OrderType.STOP_LIMIT, limit_price=140.0, stop_price=141.0,
                    adaptive=True)
    assert "adaptive is only valid for MARKET or LIMIT" in str(exc_info.value)
