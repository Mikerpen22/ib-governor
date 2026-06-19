# tests/live/conftest.py
"""Lightweight duck-typed fakes for the IBKR objects build_snapshot() reads.
The builder only touches the attributes below, so we don't import ib_async here."""
import datetime as dt
from types import SimpleNamespace

import pytest


def account_value(tag: str, value: str, currency: str = "USD"):
    return SimpleNamespace(tag=tag, value=value, currency=currency, account="U1")


def portfolio_item(sec_type, position, market_price, multiplier="1", local_symbol="X"):
    contract = SimpleNamespace(secType=sec_type, multiplier=str(multiplier),
                               localSymbol=local_symbol)
    return SimpleNamespace(contract=contract, position=position, marketPrice=market_price)


def fill(sec_type, realized_pnl, order_id, local_symbol="MNQU6",
         time=None):
    contract = SimpleNamespace(secType=sec_type, localSymbol=local_symbol)
    commission = SimpleNamespace(realizedPNL=realized_pnl)
    execution = SimpleNamespace(orderId=order_id,
                                time=time or dt.datetime(2026, 6, 17, 11, 0))
    return SimpleNamespace(contract=contract, commissionReport=commission,
                           execution=execution)


@pytest.fixture
def mk_account_value():
    return account_value


@pytest.fixture
def mk_portfolio_item():
    return portfolio_item


@pytest.fixture
def mk_fill():
    return fill
