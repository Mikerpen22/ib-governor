"""Structured, validated trade intent + mapping to ib_async order objects."""
from __future__ import annotations

from enum import Enum

from ib_async import LimitOrder, MarketOrder, Order, StopLimitOrder, StopOrder
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
        return self


def build_order(intent: OrderIntent) -> Order:
    """Map a validated OrderIntent to an ib_async Order.

    Note: intent.sec_type is not encoded in the returned Order — it is used by
    the contract builder (a separate concern) to construct the matching Contract.
    """
    action = intent.action.value
    quantity = intent.quantity
    if intent.order_type is OrderType.MARKET:
        return MarketOrder(action, quantity)
    if intent.order_type is OrderType.LIMIT:
        return LimitOrder(action, quantity, intent.limit_price)
    if intent.order_type is OrderType.STOP:
        return StopOrder(action, quantity, intent.stop_price)
    return StopLimitOrder(action, quantity, intent.limit_price, intent.stop_price)
