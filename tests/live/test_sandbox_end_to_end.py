"""End-to-end proof of the agent's STRUCTURAL can't-place guarantee, exercised
through REAL `python -m governor.gate` subprocesses (not monkeypatched seams).

The safety story for the Telegram NL-order feature is: the headless agent runs the
gate with GOVERNOR_AGENT_SANDBOX=1, which `_maybe_agent_sandbox` turns into a
forced `live.dry_run=True`, so even a `gate submit` the agent somehow ran places
NOTHING — independent of (unreliable) Claude Code headless permission matching.

Unit tests cover each link in isolation (the pure forcing function; the executor
short-circuit; the proc env passthrough). This file welds the chain end-to-end at
the real process boundary: a real subprocess, real config load, real argparse,
the real `_maybe_agent_sandbox` call in `main()`, against the operator's REAL
config — which is the only place the wiring can actually be verified.
"""
from __future__ import annotations

import json
import os
import socket
import sys
from datetime import datetime, timezone

import pytest

from governor.comms.proc import run_capture
from governor.config import load_config

pytestmark = pytest.mark.integration

_CONFIG = "config/rules.yaml"


def _tws_open(cfg) -> bool:
    try:
        with socket.create_connection((cfg.live.host, cfg.live.port), timeout=2):
            return True
    except OSError:
        return False


async def _gate(args: list[str], *, sandbox: bool, staged_path) -> tuple[int, str, str]:
    """Run `python -m governor.gate <args>` as a real subprocess. Always points the
    subprocess at an ISOLATED staged file (GOVERNOR_STAGED_PATH) so a test never
    reads or writes the live daemon's production config/staged_orders.json."""
    env = {**os.environ, "GOVERNOR_STAGED_PATH": str(staged_path)}
    if sandbox:
        env["GOVERNOR_AGENT_SANDBOX"] = "1"
    else:
        env.pop("GOVERNOR_AGENT_SANDBOX", None)
    return await run_capture(
        [sys.executable, "-m", "governor.gate", *args], timeout=90, env=env
    )


async def test_real_submit_subprocess_bogus_token_emits_structured_error(tmp_path):
    """The real CLI entry point (no monkeypatching) loads config, parses args, runs
    `_maybe_agent_sandbox`, and honors the structured-error contract the daemon
    switches on. Bogus token returns before any TWS connect, so this needs no IBKR
    and runs on any config — proving the real plumbing the unit tests stub out.
    Isolated staged file so it doesn't even read the live daemon's."""
    rc, out, err = await _gate(
        ["submit", "--token", "NOPENOTATOKEN", "--json"],
        sandbox=True, staged_path=tmp_path / "staged.json",
    )
    assert rc == 1, f"expected exit 1 for a bogus token, got {rc}: {err[-300:]}"
    data = json.loads(out.strip().splitlines()[-1])
    assert data == {"ok": False, "reason": "EXPIRED", "message": data["message"]}


async def test_sandbox_forces_dry_run_on_armed_config_end_to_end(tmp_path):
    """THE keystone: against the operator's ARMED config (dry_run=False), a real
    `gate submit` under GOVERNOR_AGENT_SANDBOX=1 must report dry_run=True and place
    NOTHING. The contrast — config armed on disk, submit forced dry-run — is the
    proof the sandbox did the forcing. Stages a GO token (avoids verdict variance)
    into an ISOLATED staged file (GOVERNOR_STAGED_PATH -> tmp_path), so the live
    daemon's production config/staged_orders.json is never touched."""
    cfg = load_config(_CONFIG)
    if not _tws_open(cfg):
        pytest.skip(f"TWS not reachable at {cfg.live.host}:{cfg.live.port}")
    if cfg.live.dry_run:
        pytest.skip(
            "config is not armed (live.dry_run already True) — a dry-run submit is "
            "the default here, so it cannot prove the SANDBOX forced it. Meaningful "
            "only against an armed config."
        )

    from governor.gate.intent import Action, OrderIntent, OrderType, SecType
    from governor.gate.staged import StagedOrderStore

    staged = tmp_path / "staged.json"   # isolated from the live daemon's file
    intent = OrderIntent(
        action=Action.BUY, symbol="SNAP", quantity=1.0,
        sec_type=SecType.STK, order_type=OrderType.MARKET,
    )
    store = StagedOrderStore(staged, ttl_seconds=cfg.live.confirm_ttl_seconds)
    token = store.stage(intent.model_dump(), datetime.now(timezone.utc), verdict="GO")

    try:
        rc, out, err = await _gate(
            ["submit", "--token", token, "--json"], sandbox=True, staged_path=staged
        )
    finally:
        # Best-effort cleanup; the tmp file is pytest-managed regardless, and a
        # failed cleanup must not mask the assertion outcome below.
        try:
            store.consume(token, datetime.now(timezone.utc))
        except Exception:  # noqa: BLE001
            pass

    assert rc == 0, f"sandboxed submit failed rc={rc}: {err[-500:]}"
    data = json.loads(out.strip().splitlines()[-1])
    assert data["dry_run"] is True, (
        "GOVERNOR_AGENT_SANDBOX MUST force dry_run even when the on-disk config is "
        f"armed (dry_run={cfg.live.dry_run}); got {data}"
    )
    assert data["placed"] is False, "a sandbox-forced dry-run must place NOTHING"
