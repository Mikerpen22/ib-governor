"""Unit tests for BrakeConnection — no real socket.

These assert the delayed-data fallback (reqMarketDataType(4)) is enabled
immediately after the connection is established, in BOTH the sync and async
paths. Type 4 = delayed-frozen: lets sizing/notional work without a live
market-data subscription; a live subscription, when present, still wins.
"""
from __future__ import annotations

import pytest

from governor.config import LiveConfig
from governor.live.connection import BrakeConnection


class _FakeIB:
    """Records the order of connect / reqMarketDataType calls."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self._connected = False

    def connect(self, host, port, clientId, readonly, account):  # noqa: N803 (ib_async kwarg name)
        self.calls.append(("connect", host, port, clientId, readonly, account))
        self._connected = True

    async def connectAsync(self, host, port, clientId, readonly, account):  # noqa: N802,N803
        self.calls.append(("connectAsync", host, port, clientId, readonly, account))
        self._connected = True

    def reqMarketDataType(self, t):  # noqa: N802 (ib_async method name)
        self.calls.append(("reqMarketDataType", t))

    def isConnected(self) -> bool:  # noqa: N802
        return self._connected

    def disconnect(self) -> None:
        self._connected = False


def _conn_with_fake() -> tuple[BrakeConnection, _FakeIB]:
    conn = BrakeConnection(LiveConfig())
    fake = _FakeIB()
    conn.ib = fake  # type: ignore[assignment]
    return conn, fake


def test_connect_requests_delayed_market_data_type_4():
    conn, fake = _conn_with_fake()
    conn.connect()
    assert ("reqMarketDataType", 4) in fake.calls


def test_connect_requests_delayed_data_after_connecting():
    """reqMarketDataType(4) must come AFTER connect() (you can't set the data type
    on a socket that isn't open yet)."""
    conn, fake = _conn_with_fake()
    conn.connect()
    names = [c[0] for c in fake.calls]
    assert names.index("connect") < names.index("reqMarketDataType")


@pytest.mark.asyncio
async def test_connect_async_requests_delayed_market_data_type_4():
    conn, fake = _conn_with_fake()
    await conn.connect_async()
    assert ("reqMarketDataType", 4) in fake.calls


@pytest.mark.asyncio
async def test_connect_async_requests_delayed_data_after_connecting():
    conn, fake = _conn_with_fake()
    await conn.connect_async()
    names = [c[0] for c in fake.calls]
    assert names.index("connectAsync") < names.index("reqMarketDataType")
