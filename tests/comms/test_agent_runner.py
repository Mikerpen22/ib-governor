"""Tests for governor.comms.agent_runner — the headless `claude -p` bridge that
turns a natural-language Telegram message into a staged, confirm-gated order.

No real `claude` binary and no network: the subprocess runner and the
binary-presence check are injected.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from governor.comms.agent_runner import build_claude_argv, run_agent


def _cfg(enabled=True, claude_bin="claude", timeout=120.0):
    return SimpleNamespace(enabled=enabled, claude_bin=claude_bin, timeout_seconds=timeout)


class _FakeRunner:
    """Records the argv it was handed and returns a canned (rc, stdout, stderr)."""

    def __init__(self, rc=0, stdout="", stderr="", raises=None):
        self.rc, self.stdout, self.stderr, self.raises = rc, stdout, stderr, raises
        self.calls: list[list[str]] = []

    async def __call__(self, argv, timeout):
        self.calls.append(argv)
        if self.raises is not None:
            raise self.raises
        return self.rc, self.stdout, self.stderr


# ── argv construction (pure) ────────────────────────────────────────────────

def test_argv_invokes_claude_print_mode_with_text():
    argv = build_claude_argv("buy me 100 oracle", _cfg(claude_bin="/opt/claude"))
    assert argv[0] == "/opt/claude"
    assert "-p" in argv
    assert "buy me 100 oracle" in argv


def _values_after(argv, flag):
    """Return the run of values after *flag* up to the next --option (argparse nargs)."""
    i = argv.index(flag) + 1
    vals = []
    while i < len(argv) and not argv[i].startswith("--"):
        vals.append(argv[i])
        i += 1
    return vals


def test_argv_confines_agent_to_analyze_and_denies_write_paths():
    """C1: --allowed-tools is additive to global settings, so confinement must be
    enforced with deny rules + strict MCP, not just an allow-list."""
    argv = build_claude_argv("buy 100 ORCL", _cfg())

    allowed = " ".join(_values_after(argv, "--allowed-tools"))
    assert "governor.gate analyze" in allowed      # read-only analysis permitted
    assert "submit" not in allowed                  # the allow-list never grants submit

    disallowed = " ".join(_values_after(argv, "--disallowed-tools"))
    assert "governor.gate submit" in disallowed     # deny wins over the global Bash(python *) allow
    assert "place_order" in disallowed              # MCP write tool denied too

    # The inherited ibkr-tws MCP (with place_order) must not load at all.
    assert "--strict-mcp-config" in argv


def test_argv_appends_a_system_prompt():
    argv = build_claude_argv("buy 100 ORCL", _cfg())
    assert "--append-system-prompt" in argv
    prompt = argv[argv.index("--append-system-prompt") + 1]
    assert prompt.strip()                               # non-empty guidance


# ── run_agent graceful gates ────────────────────────────────────────────────

async def test_disabled_returns_graceful_message_without_running():
    runner = _FakeRunner(stdout="should not run")
    reply = await run_agent("buy 100 ORCL", _cfg(enabled=False),
                            runner=runner, which=lambda b: "/usr/bin/claude")
    assert runner.calls == []
    assert "offline" in reply.lower() or "disabled" in reply.lower()


async def test_missing_binary_returns_graceful_message_without_running():
    runner = _FakeRunner(stdout="should not run")
    reply = await run_agent("buy 100 ORCL", _cfg(),
                            runner=runner, which=lambda b: None)
    assert runner.calls == []
    assert "unavailable" in reply.lower() or "offline" in reply.lower()


# ── run_agent happy + failure paths ─────────────────────────────────────────

async def test_success_relays_stdout():
    runner = _FakeRunner(rc=0, stdout="  GO — BUY 100 ORCL. Reply CONFIRM ABC123  ")
    reply = await run_agent("buy 100 ORCL", _cfg(),
                            runner=runner, which=lambda b: "/usr/bin/claude")
    assert reply == "GO — BUY 100 ORCL. Reply CONFIRM ABC123"
    assert len(runner.calls) == 1


async def test_nonzero_exit_is_graceful_not_raised():
    runner = _FakeRunner(rc=1, stdout="", stderr="boom")
    reply = await run_agent("buy 100 ORCL", _cfg(),
                            runner=runner, which=lambda b: "/usr/bin/claude")
    assert "couldn't" in reply.lower() or "error" in reply.lower() or "failed" in reply.lower()


async def test_runner_exception_is_graceful_not_raised():
    runner = _FakeRunner(raises=TimeoutError("timed out"))
    reply = await run_agent("buy 100 ORCL", _cfg(),
                            runner=runner, which=lambda b: "/usr/bin/claude")
    assert isinstance(reply, str) and reply                     # never raises


async def test_empty_stdout_falls_back_to_a_message():
    runner = _FakeRunner(rc=0, stdout="   ")
    reply = await run_agent("buy 100 ORCL", _cfg(),
                            runner=runner, which=lambda b: "/usr/bin/claude")
    assert reply.strip()                                        # some message, not blank
