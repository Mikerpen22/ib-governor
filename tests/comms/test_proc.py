"""Tests for governor.comms.proc.run_capture — the shared async subprocess seam."""
from __future__ import annotations

import os
import sys

import pytest

from governor.comms.proc import run_capture


async def test_captures_stdout_and_returncode():
    rc, out, err = await run_capture([sys.executable, "-c", "print('hi')"], timeout=10)
    assert rc == 0
    assert out.strip() == "hi"
    assert err == ""


async def test_nonzero_returncode_and_stderr():
    rc, out, err = await run_capture(
        [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"],
        timeout=10,
    )
    assert rc == 3
    assert "boom" in err


async def test_timeout_raises_and_does_not_hang():
    with pytest.raises(TimeoutError):
        await run_capture([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.2)


# ── env passthrough (the channel the sandbox safety flag rides on) ───────────
# The agent runner's structural can't-place guarantee is GOVERNOR_AGENT_SANDBOX=1
# reaching the child gate process. That delivery happens here, via run_capture's
# **kwargs -> create_subprocess_exec(env=...). A real subprocess proves the flag
# actually arrives in the child's os.environ — a mock of the runner never would.

async def test_env_kwarg_reaches_the_child_process():
    env = {**os.environ, "GOVERNOR_AGENT_SANDBOX": "1"}
    rc, out, err = await run_capture(
        [sys.executable, "-c",
         "import os; print(os.environ.get('GOVERNOR_AGENT_SANDBOX', 'MISSING'))"],
        timeout=10,
        env=env,
    )
    assert rc == 0
    assert out.strip() == "1"          # the safety flag really lands in the child


async def test_child_without_env_does_not_see_the_flag():
    # Sanity counter-test: when we DON'T pass the flag, the child must not see a
    # stale one — so the positive test above is meaningful, not vacuous.
    base = {k: v for k, v in os.environ.items() if k != "GOVERNOR_AGENT_SANDBOX"}
    rc, out, _ = await run_capture(
        [sys.executable, "-c",
         "import os; print(os.environ.get('GOVERNOR_AGENT_SANDBOX', 'MISSING'))"],
        timeout=10,
        env=base,
    )
    assert rc == 0
    assert out.strip() == "MISSING"
