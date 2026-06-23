"""REAL-binary tests for the headless `claude -p` bridge (agent_runner).

The unit tests in test_agent_runner.py inject a fake runner, so they prove our
*own* string construction but can never catch the bridge breaking against the
actually-installed `claude` — e.g. a renamed flag, a dropped `--print` mode, or a
binary that no longer authenticates. These tests close that gap against the real
CLI.

Two tiers:
  - flag-drift validation runs whenever `claude` is on PATH (fast, no model call,
    no network) — it asserts the installed binary documents every flag our argv
    emits, so a future claude release that renames a flag fails HERE instead of
    silently degrading the live feature to "couldn't finish that one".
  - the full end-to-end `-p` run is gated behind IBG_LIVE_CLAUDE=1 because it
    spends a model call (and connects to IBKR through `gate analyze`); opt in to
    verify the whole NL-order path against the real model + real gate.
"""
from __future__ import annotations

import os
import re
import shutil

import pytest

from governor.comms.agent_runner import (
    _DISABLED_MSG,
    _EMPTY_MSG,
    _FAILED_MSG,
    _UNAVAILABLE_MSG,
    build_ask_argv,
    build_claude_argv,
    run_agent,
)
from governor.comms.proc import run_capture
from governor.config import TelegramAgentConfig

# The graceful-degradation strings run_agent returns on timeout/failure/empty.
# A real end-to-end success must NOT be one of these — otherwise the test passes
# vacuously when the agent merely times out (which is exactly what a too-short
# timeout produces).
_GRACEFUL = {_DISABLED_MSG, _UNAVAILABLE_MSG, _FAILED_MSG, _EMPTY_MSG}

pytestmark = pytest.mark.integration

CLAUDE = shutil.which("claude")
requires_claude = pytest.mark.skipif(CLAUDE is None, reason="`claude` not on PATH")


def _flags(argv: list[str]) -> list[str]:
    """The option flags in an argv (tokens starting with '-'). Values — the
    prompt, tool names, the MCP JSON, deny patterns like 'Bash(...)' — don't."""
    return [tok for tok in argv if tok.startswith("-")]


@requires_claude
async def test_installed_claude_documents_every_flag_our_order_argv_uses():
    """Every flag the ORDER argv emits is understood by the installed `claude`.
    Self-maintaining: it reads the flags off the real argv, so a new flag added
    to build_claude_argv is automatically validated against the binary too."""
    rc, help_text, err = await run_capture([CLAUDE, "--help"], timeout=30)
    assert rc == 0, f"`claude --help` failed: {err[:300]}"

    argv = build_claude_argv("buy 100 ORCL", TelegramAgentConfig(claude_bin=CLAUDE))
    missing = [f for f in _flags(argv) if f not in help_text]
    assert not missing, (
        f"installed claude ({CLAUDE}) does not document these argv flags: {missing} "
        "— the headless order bridge would break. Update build_claude_argv."
    )


@requires_claude
async def test_installed_claude_documents_every_flag_our_ask_argv_uses():
    """Same drift guard for the read-only ASK argv (it adds web tools)."""
    rc, help_text, err = await run_capture([CLAUDE, "--help"], timeout=30)
    assert rc == 0, f"`claude --help` failed: {err[:300]}"

    argv = build_ask_argv("how does NVDA look?", TelegramAgentConfig(claude_bin=CLAUDE))
    missing = [f for f in _flags(argv) if f not in help_text]
    assert not missing, f"installed claude does not document ASK argv flags: {missing}"


@requires_claude
async def test_real_claude_runs_headless_print_mode_with_our_flags():
    """A real `claude -p` invocation with the exact confinement flags our bridge
    uses (strict MCP isolation, allow/deny lists, appended system prompt) parses,
    runs, and returns stdout. Cheap prompt, no tools needed — this proves the
    binary accepts our flag *combination* and prints, without a heavy gate round
    trip. Skips (not fails) if the binary is present but unauthenticated/offline."""
    argv = [
        CLAUDE, "-p", "Reply with exactly the single word: PONG",
        "--permission-mode", "default",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers": {}}',
        "--allowed-tools", "Bash", "Read",
        "--append-system-prompt", "Answer in one word.",
    ]
    try:
        rc, out, err = await run_capture(argv, timeout=90, env={**os.environ})
    except TimeoutError:
        pytest.skip("claude -p timed out (slow/offline) — flag parsing covered elsewhere")

    if rc != 0:
        # Skip ONLY on a recognized auth/offline failure — otherwise a genuine
        # flag-combination breakage (the regression this test exists to catch)
        # would hide behind a skip. An unrecognized non-zero rc is a real failure.
        low = (err + out).lower()
        offline = ("credit balance", "not logged in", "unauthenticated", "log in",
                   "econnrefused", "network", "fetch failed", "rate limit",
                   "overloaded", "timed out", "timeout", "503", "529")
        if any(s in low for s in offline):
            pytest.skip(f"claude unauthenticated/offline: {err.strip()[:200]}")
        pytest.fail(
            f"claude -p exited {rc} with an UNRECOGNIZED error — likely our flag "
            f"combination broke against this claude version: {err.strip()[:300]}"
        )
    assert "PONG" in out.upper(), f"expected PONG in real claude output, got: {out[:200]!r}"


@pytest.mark.skipif(
    os.getenv("IBG_LIVE_CLAUDE") != "1",
    reason="set IBG_LIVE_CLAUDE=1 to run the full NL-order agent against real claude + gate "
           "(spends a model call and connects to IBKR via `gate analyze`)",
)
async def test_real_order_agent_end_to_end_returns_chat_reply():
    """The whole headless order path against the real model + real gate, under the
    sandbox (so it can place NOTHING). Content varies by model, so we assert the
    contract: a REAL analysis reply — not blank, and crucially NOT one of the
    graceful timeout/failure strings (a too-short timeout returns those, and a
    'non-empty' assertion would pass on them vacuously). Measured latency is
    variable (~60s to >180s), so we give the agent generous headroom here; the
    PRODUCTION default (TelegramAgentConfig.timeout_seconds=240) covers the
    measured slow tail — tune it in rules.yaml if that ever bites."""
    cfg = TelegramAgentConfig(claude_bin=CLAUDE or "claude", timeout_seconds=300.0)
    reply = await run_agent("buy 1 SNAP market order", cfg)

    assert isinstance(reply, str) and reply.strip(), "agent must return a non-empty reply"
    assert reply not in _GRACEFUL, (
        f"agent returned a graceful-degradation message, not a real analysis "
        f"(likely a timeout): {reply!r}"
    )
    # Word-boundary match so we don't pass on incidental substrings ("ALGO", "GOOD").
    assert re.search(r"\b(CONFIRM|VERDICT|GO|CAUTION|BLOCK)\b", reply.upper()), (
        f"reply lacks any sign of a real gate analysis: {reply[:200]!r}"
    )
