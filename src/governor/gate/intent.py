"""Structured, validated trade intent + mapping to ib_async order objects."""
from __future__ import annotations

from enum import Enum

from ib_async import LimitOrder, MarketOrder, Order, StopLimitOrder, StopOrder, TagValue
from pydantic import BaseModel, Field, model_validator


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class SecType(str, Enum):
    STK = "STK"
    FUT = "FUT"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class AdaptivePriority(str, Enum):
    URGENT = "Urgent"
    NORMAL = "Normal"
    PATIENT = "Patient"


class OrderIntent(BaseModel):
    action: Action
    symbol: str = Field(min_length=1, max_length=20)
    quantity: float
    sec_type: SecType
    order_type: OrderType
    limit_price: float | None = None
    stop_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    # Disambiguation hints for STK qualification. currency defaults to USD;
    # primary_exchange (e.g. "NASDAQ", "NYSE", "TSE") narrows a symbol that is
    # listed on multiple venues / in multiple currencies. See runner.qualify.
    currency: str = "USD"
    primary_exchange: str | None = None
    # Time-in-force. ib_async orders default tif="" (= DAY), so an intraday
    # protective stop placed as a DAY order is cancelled by TWS at the session
    # close — leaving the filled entry unprotected overnight. tif is the ENTRY's
    # TIF; protective_tif is applied to the bracket's children (default GTC so
    # they outlive the session). protective_tif is configurable because some STK
    # venues restrict GTC stops.
    tif: str = "DAY"
    protective_tif: str = "GTC"
    # IBKR Adaptive algo (IBALGO). Adaptive is layered ON a MKT/LMT base order —
    # it is NOT a new order type: order.orderType stays MKT/LMT and only
    # algoStrategy/algoParams are added. Valid for MARKET/LIMIT only.
    adaptive: bool = False
    adaptive_priority: AdaptivePriority = AdaptivePriority.NORMAL

    model_config = {"frozen": True}

    @model_validator(mode="after")
    def _prices_present(self) -> "OrderIntent":
        needs_limit = self.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT)
        needs_stop = self.order_type in (OrderType.STOP, OrderType.STOP_LIMIT)
        if needs_limit and self.limit_price is None:
            raise ValueError(f"{self.order_type.value} requires limit_price")
        if needs_stop and self.stop_price is None:
            raise ValueError(f"{self.order_type.value} requires stop_price")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.adaptive and self.order_type not in (OrderType.MARKET, OrderType.LIMIT):
            raise ValueError("adaptive is only valid for MARKET or LIMIT orders")
        return self


def _apply_adaptive(order: Order, intent: OrderIntent) -> Order:
    """Layer the IBKR Adaptive algo onto a MKT/LMT base order, in place.

    Adaptive is an algo, not an order type: order.orderType is left untouched
    (stays MKT/LMT) so the rest of the pipeline (whatIf, sizing, bracket
    transmit/parentId/OCA) is unaffected. Only applied to MARKET/LIMIT orders;
    NEVER to STOP/STOP_LIMIT (TWS rejects Adaptive on a stop).
    """
    if intent.adaptive:
        order.algoStrategy = "Adaptive"
        order.algoParams = [TagValue("adaptivePriority", intent.adaptive_priority.value)]
    return order


def build_order(intent: OrderIntent) -> Order:
    """Map a validated OrderIntent to an ib_async Order.

    Note: intent.sec_type is not encoded in the returned Order — it is used by
    the contract builder (a separate concern) to construct the matching Contract.
    """
    action = intent.action.value
    quantity = intent.quantity
    if intent.order_type is OrderType.MARKET:
        order = MarketOrder(action, quantity)
        order.tif = intent.tif
        return _apply_adaptive(order, intent)
    if intent.order_type is OrderType.LIMIT:
        order = LimitOrder(action, quantity, intent.limit_price)
        order.tif = intent.tif
        return _apply_adaptive(order, intent)
    if intent.order_type is OrderType.STOP:
        order = StopOrder(action, quantity, intent.stop_price)
        order.tif = intent.tif
        return order
    order = StopLimitOrder(action, quantity, intent.limit_price, intent.stop_price)
    order.tif = intent.tif
    return order
