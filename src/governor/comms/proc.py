"""Shared async subprocess seam: spawn a command, capture output, enforce a hard
timeout, and reap the child on timeout so nothing is orphaned.

One helper for both the headless `claude -p` agent and the `gate submit`
chokepoint — so the spawn/capture/decode/timeout-cleanup logic lives in exactly
one place and the two callers can't drift.
"""
from __future__ import annotations

import asyncio


async def run_capture(argv: list[str], timeout: float, **kwargs) -> tuple[int, str, str]:
    """Run *argv* (no shell), capturing stdout/stderr; return (returncode, out, err).

    Raises asyncio.TimeoutError if the child outlives *timeout* — after killing
    and reaping it, so a slow child never leaks. Extra kwargs (e.g. env, cwd) are
    passed through to create_subprocess_exec.
    """
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **kwargs,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")
