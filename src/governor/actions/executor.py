"""The ONLY module that calls an ib_async WRITE method (placeOrder,
reqGlobalCancel). Every account-affecting call goes through `_guarded`, which
short-circuits (logging intent) when dry_run is set."""
from __future__ import annotations

import datetime as dt
import logging

from ib_async import MarketOrder

from .lockout import Lockout, LockoutStore
from ..live.snapshot import contract_symbol, is_sec_type

log = logging.getLogger("governor.actions")


class ActionExecutor:
    def __init__(self, ib, dry_run: bool, lockout_store: LockoutStore) -> None:
        self.ib = ib
        self.dry_run = dry_run
        self.lockout_store = lockout_store

    def _guarded(self, description: str, do_it) -> bool:
        """Run an account-affecting action unless dry_run. Returns True iff executed."""
        if self.dry_run:
            log.warning("DRY-RUN — would execute: %s", description)
            return False
        do_it()
        log.warning("EXECUTED: %s", description)
        return True

    def cancel_all_orders(self) -> bool:
        return self._guarded("cancel ALL open orders (reqGlobalCancel, account-wide)",
                             lambda: self.ib.reqGlobalCancel())

    def lockout(self, kind: str, until: dt.datetime, reason: str, now: dt.datetime) -> None:
        # 1) cancel everything (account action — gated)
        self.cancel_all_orders()
        # 2) persist the lockout flag (bookkeeping, not an account action — always set)
        self.lockout_store.set(Lockout(kind=kind, until=until, reason=reason))
        log.warning("LOCKOUT set: %s until %s (%s)", kind, until.isoformat(), reason)

    def place_order(self, contract, order) -> bool:
        """Submit a NEW order through the single guarded write chokepoint.

        Returns True iff executed (False under dry_run).
        All writes to ib stay inside _guarded — this is the sole public
        entry point for placing a user-initiated order.
        """
        sym = contract_symbol(contract) or "?"
        return self._guarded(
            f"place order: {order.action} {order.totalQuantity} {sym} ({order.orderType})",
            lambda: self.ib.placeOrder(contract, order),
        )

    def place_orders(self, contract, orders) -> bool:
        """Place a multi-order bracket through the single guarded write chokepoint (dry_run-gated)."""
        sym = contract_symbol(contract) or "?"
        head = orders[0]
        return self._guarded(
            f"place bracket: {len(orders)} orders for {sym} ({head.action} {head.totalQuantity})",
            lambda: [self.ib.placeOrder(contract, o) for o in orders],
        )

    def trim_futures(self, target_contracts: float) -> bool:
        """Reduce the net futures position toward `target_contracts` (per underlying)
        with a market order. Conservative: trims each FUT position whose abs size
        exceeds the target down to the target."""
        executed_any = False
        for pos in self.ib.positions():
            if not is_sec_type(pos, "FUT"):
                continue
            size = abs(pos.position)
            if size <= target_contracts:
                continue
            reduce_qty = size - target_contracts
            action = "SELL" if pos.position > 0 else "BUY"
            order = MarketOrder(action, reduce_qty)
            did = self._guarded(
                f"trim {pos.contract.localSymbol}: {action} {reduce_qty} "
                f"(from {size} to {target_contracts})",
                lambda c=pos.contract, o=order: self.ib.placeOrder(c, o),
            )
            executed_any = executed_any or did
        return executed_any
