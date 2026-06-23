"""Tests for governor.comms.agent_runner — the headless `claude -p` bridge that
turns a natural-language Telegram message into a staged, confirm-gated order.

No real `claude` binary and no network: the subprocess runner and the
binary-presence check are injected.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from governor.comms.agent_runner import (
    build_ask_argv,
    build_claude_argv,
    run_agent,
    run_ask_agent,
)


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


def test_argv_allows_bash_denies_writes_and_isolates_mcp():
    """Bare Bash auto-runs headless (scoped rules don't); the real guarantee is the
    forced-dry-run sandbox env, with deny + strict-MCP as defense-in-depth."""
    argv = build_claude_argv("buy 100 ORCL", _cfg())

    allowed = _values_after(argv, "--allowed-tools")
    assert "Bash" in allowed and "Read" in allowed   # bare Bash form runs headless

    disallowed = " ".join(_values_after(argv, "--disallowed-tools"))
    assert "governor.gate submit" in disallowed       # bonus deny (dry-run is the real block)
    assert "place_order" in disallowed

    assert "--strict-mcp-config" in argv              # hard flag: ibkr-tws place_order MCP can't load


def test_agent_env_forces_dry_run_sandbox():
    from governor.comms.agent_runner import _agent_env
    env = _agent_env()
    assert env["GOVERNOR_AGENT_SANDBOX"] == "1"        # gate forced dry-run for the agent
    assert "PATH" in env                                # inherits daemon env (claude/python resolution)


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
    assert "off" in reply.lower() or "unavailable" in reply.lower()


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


# ── the read-only ASK agent ─────────────────────────────────────────────────

def test_ask_argv_adds_web_tools_and_keeps_the_write_denies():
    argv = build_ask_argv("how does NVDA look?", _cfg())
    allowed = _values_after(argv, "--allowed-tools")
    assert "WebSearch" in allowed and "WebFetch" in allowed     # news on top of Bash/Read
    assert "Bash" in allowed and "Read" in allowed
    disallowed = " ".join(_values_after(argv, "--disallowed-tools"))
    assert "governor.gate submit" in disallowed and "place_order" in disallowed
    assert "--strict-mcp-config" in argv                        # MCP place_order still can't load


def test_ask_prompt_forbids_placing_or_staging():
    argv = build_ask_argv("x", _cfg())
    prompt = argv[argv.index("--append-system-prompt") + 1].lower()
    assert "read-only" in prompt
    assert "never place or stage" in prompt or "no trading authority" in prompt


async def test_run_ask_agent_relays_html_stdout():
    runner = _FakeRunner(rc=0, stdout="  <b>NVDA</b> looks extended  ")
    reply = await run_ask_agent("how does NVDA look?", _cfg(),
                                runner=runner, which=lambda b: "/usr/bin/claude")
    assert reply == "<b>NVDA</b> looks extended"                # HTML preserved (not escaped here)


async def test_run_ask_agent_disabled_is_graceful_without_running():
    runner = _FakeRunner(stdout="should not run")
    reply = await run_ask_agent("what's my leverage", _cfg(enabled=False),
                                runner=runner, which=lambda b: "/usr/bin/claude")
    assert runner.calls == []
    assert "off" in reply.lower() or "unavailable" in reply.lower()


async def test_run_ask_agent_exception_is_graceful_not_raised():
    runner = _FakeRunner(raises=TimeoutError("timed out"))
    reply = await run_ask_agent("news on oracle", _cfg(),
                                runner=runner, which=lambda b: "/usr/bin/claude")
    assert isinstance(reply, str) and reply                     # never raises
