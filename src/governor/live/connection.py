"""Owns the single persistent ib_async connection. The ONLY module that opens a socket."""
from __future__ import annotations

import logging

from ib_async import IB

from ..config import LiveConfig

log = logging.getLogger("governor.connection")


class BrakeConnection:
    def __init__(self, cfg: LiveConfig) -> None:
        self._cfg = cfg
        self.ib = IB()

    def connect(self) -> None:
        self.ib.connect(
            self._cfg.host,
            self._cfg.port,
            clientId=self._cfg.client_id,
            readonly=self._cfg.readonly,
            account=self._cfg.account or "",
        )
        # Delayed-frozen market data (type 4): lets sizing/notional work without a
        # live data subscription. A live subscription, when present, still wins.
        self.ib.reqMarketDataType(4)
        log.info(
            "connected TWS %s:%s clientId=%s readonly=%s",
            self._cfg.host, self._cfg.port, self._cfg.client_id, self._cfg.readonly,
        )

    async def connect_async(self) -> None:
        await self.ib.connectAsync(
            self._cfg.host, self._cfg.port,
            clientId=self._cfg.client_id, readonly=self._cfg.readonly,
            account=self._cfg.account or "",
        )
        # Delayed-frozen market data (type 4): lets sizing/notional work without a
        # live data subscription. A live subscription, when present, still wins.
        self.ib.reqMarketDataType(4)

    def is_connected(self) -> bool:
        return self.ib.isConnected()

    def disconnect(self) -> None:
        if self.ib.isConnected():
            self.ib.disconnect()
