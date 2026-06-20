"""Tests for governor.comms.proc.run_capture — the shared async subprocess seam."""
from __future__ import annotations

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
