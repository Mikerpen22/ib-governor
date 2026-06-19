"""Tests for governor.gate.cli — CLI subcommands analyze & submit.

All tests monkeypatch I/O seams so no TWS connection is required.
Uses pytest capsys for stdout, tmp_path for staged order files.

TDD: written BEFORE the implementation (red step).
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from governor.config import RulesConfig
from governor.gate.analysis import GateVerdict, Verdict
from governor.gate.intent import Action, OrderIntent, OrderType, SecType
from governor.gate.staged import StagedOrderStore
from governor.model import StateSnapshot


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_NOW = dt.datetime(2026, 6, 19, 14, 30, 0, tzinfo=dt.timezone.utc)
_FIXED_TOKEN = "ABCD1234"


def _snap(nav: float = 250_000.0, **kwargs) -> StateSnapshot:
    base = dict(
        ts="2026-06-19T14:30:00+00:00",
        nav=nav,
        margin_cushion=0.60,
        gross_leverage=0.0,
        drawdown_pct=0.0,
    )
    base.update(kwargs)
    return StateSnapshot(**base)


def _go_verdict() -> GateVerdict:
    return GateVerdict(Verdict.GO, ())


def _block_verdict() -> GateVerdict:
    return GateVerdict(Verdict.BLOCK, ("an active lockout blocks new trades in this asset class",))


def _go_preview(symbol: str = "ORCL") -> dict:
    return {
        "symbol": symbol,
        "action": "BUY",
        "quantity": 50.0,
        "order_type": "LIMIT",
        "order_notional": 7250.0,
        "pct_nav": 0.0196,
        "buying_power_ok": True,
        "init_margin": 5000.0,
        "name_weight_before": 0.0,
        "name_weight_after": 0.0196,
        "trips": [],
        "lockout_active": False,
        "verdict": "GO",
        "reasons": [],
    }


def _orcl_intent() -> OrderIntent:
    return OrderIntent(
        action=Action.BUY,
        symbol="ORCL",
        quantity=50.0,
        sec_type=SecType.STK,
        order_type=OrderType.LIMIT,
        limit_price=145.0,
    )


# ---------------------------------------------------------------------------
# Fixtures: monkeypatch connect + build_current_snapshot + analyze_intent + submit_intent
# ---------------------------------------------------------------------------

class _FakeConn:
    """Minimal fake for the connection seam."""
    ib = SimpleNamespace()

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass


def _patch_cli_seams(monkeypatch, tmp_path, *,
                     verdict: GateVerdict,
                     preview: dict,
                     submit_return: bool = True):
    """Monkeypatch every I/O seam in governor.gate.cli.

    - connect: returns a _FakeConn
    - build_current_snapshot: returns a tiny StateSnapshot
    - analyze_intent: returns (verdict, preview)
    - submit_intent: records call + returns submit_return
    - load_config: returns default RulesConfig with staged file at tmp_path
    - StagedOrderStore path via staged_path override
    - _get_now: returns the fixed test timestamp _NOW
    """
    import governor.gate.cli as cli

    # Pin "now" so TTL math is deterministic
    monkeypatch.setattr(cli, "_get_now", lambda: _NOW)

    # Replace connect seam
    monkeypatch.setattr(cli, "_make_connection", lambda config: _FakeConn())

    # Replace snapshot builder
    monkeypatch.setattr(cli, "build_current_snapshot",
                        lambda ib, config: _snap())

    # Replace sector resolver seam so no real config/sector_cache.json is written
    monkeypatch.setattr(cli, "_resolve_sector", lambda ib, symbol: None)

    # Replace analyze_intent
    captured_intent = {}

    def _fake_analyze(ib, intent, current, config, lockout_store, *, now, sector=None):
        captured_intent["intent"] = intent
        return verdict, preview

    monkeypatch.setattr(cli, "analyze_intent", _fake_analyze)

    # Replace submit_intent
    submit_calls = []

    def _fake_submit(ib, executor, intent):
        submit_calls.append(intent)
        return submit_return

    monkeypatch.setattr(cli, "submit_intent", _fake_submit)

    # Point StagedOrderStore at tmp_path
    staged_path = tmp_path / "staged_orders.json"
    monkeypatch.setattr(cli, "_staged_path", lambda config: staged_path)

    # Load config with default rules
    monkeypatch.setattr(cli, "load_config", lambda _path: RulesConfig())

    return captured_intent, submit_calls, staged_path


# ---------------------------------------------------------------------------
# Test: analyze buy ORCL with --type limit --limit 145 --json
# ---------------------------------------------------------------------------

class TestAnalyzeJson:
    def test_stdout_json_contains_verdict_and_token(self, monkeypatch, tmp_path, capsys):
        import governor.gate.cli as cli

        captured_intent, _, staged_path = _patch_cli_seams(
            monkeypatch, tmp_path,
            verdict=_go_verdict(),
            preview=_go_preview(),
        )

        cli.main(["analyze", "buy", "50", "ORCL",
                  "--sec-type", "stk",
                  "--type", "limit",
                  "--limit", "145",
                  "--json"])

        out = capsys.readouterr().out
        data = json.loads(out)

        assert data["verdict"] == "GO"
        assert "token" in data
        assert isinstance(data["token"], str) and len(data["token"]) > 0

    def test_analyze_constructs_correct_intent(self, monkeypatch, tmp_path, capsys):
        import governor.gate.cli as cli

        captured_intent, _, _ = _patch_cli_seams(
            monkeypatch, tmp_path,
            verdict=_go_verdict(),
            preview=_go_preview(),
        )

        cli.main(["analyze", "buy", "50", "ORCL",
                  "--sec-type", "stk",
                  "--type", "limit",
                  "--limit", "145",
                  "--json"])

        intent = captured_intent["intent"]
        assert intent.action is Action.BUY
        assert intent.symbol == "ORCL"
        assert intent.quantity == 50.0
        assert intent.sec_type is SecType.STK
        assert intent.order_type is OrderType.LIMIT
        assert intent.limit_price == 145.0

    def test_analyze_stages_a_token_that_can_be_consumed(self, monkeypatch, tmp_path, capsys):
        import governor.gate.cli as cli

        _, _, staged_path = _patch_cli_seams(
            monkeypatch, tmp_path,
            verdict=_go_verdict(),
            preview=_go_preview(),
        )

        cli.main(["analyze", "buy", "50", "ORCL",
                  "--sec-type", "stk",
                  "--type", "limit",
                  "--limit", "145",
                  "--json"])

        out = capsys.readouterr().out
        token = json.loads(out)["token"]

        # The token must be consumable from the staged file
        store = StagedOrderStore(staged_path, ttl_seconds=300.0)
        result = store.consume(token, _NOW)
        assert result is not None
        assert result["symbol"] == "ORCL"

    def test_go_verdict_exits_zero(self, monkeypatch, tmp_path):
        import governor.gate.cli as cli

        _patch_cli_seams(
            monkeypatch, tmp_path,
            verdict=_go_verdict(),
            preview=_go_preview(),
        )

        # Should not raise SystemExit or raise with code 0
        try:
            cli.main(["analyze", "buy", "50", "ORCL",
                      "--type", "limit", "--limit", "145", "--json"])
        except SystemExit as e:
            assert e.code == 0 or e.code is None


# ---------------------------------------------------------------------------
# Test: BLOCK verdict exit codes (with and without --override)
# ---------------------------------------------------------------------------

class TestAnalyzeBlockVerdict:
    def test_block_without_override_exits_2(self, monkeypatch, tmp_path):
        import governor.gate.cli as cli

        _patch_cli_seams(
            monkeypatch, tmp_path,
            verdict=_block_verdict(),
            preview={**_go_preview(), "verdict": "BLOCK",
                     "reasons": ["an active lockout blocks new trades in this asset class"]},
        )

        with pytest.raises(SystemExit) as exc_info:
            cli.main(["analyze", "buy", "50", "ORCL",
                      "--type", "limit", "--limit", "145"])
        assert exc_info.value.code == 2

    def test_block_with_override_exits_zero(self, monkeypatch, tmp_path):
        import governor.gate.cli as cli

        _patch_cli_seams(
            monkeypatch, tmp_path,
            verdict=_block_verdict(),
            preview={**_go_preview(), "verdict": "BLOCK",
                     "reasons": ["an active lockout blocks new trades in this asset class"]},
        )

        # --override: should NOT raise with code 2
        try:
            cli.main(["analyze", "buy", "50", "ORCL",
                      "--type", "limit", "--limit", "145", "--override"])
        except SystemExit as e:
            # If it exits, it must be 0
            assert e.code == 0 or e.code is None

    def test_block_still_prints_output(self, monkeypatch, tmp_path, capsys):
        import governor.gate.cli as cli

        _patch_cli_seams(
            monkeypatch, tmp_path,
            verdict=_block_verdict(),
            preview={**_go_preview(), "verdict": "BLOCK",
                     "reasons": ["an active lockout blocks new trades"]},
        )

        try:
            cli.main(["analyze", "buy", "50", "ORCL",
                      "--type", "limit", "--limit", "145", "--json"])
        except SystemExit:
            pass  # expected exit 2

        out = capsys.readouterr().out
        # Should have output even when BLOCK
        assert len(out.strip()) > 0

    def test_block_still_stages_token(self, monkeypatch, tmp_path, capsys):
        """Even a BLOCK should stage a token so --override submit is possible."""
        import governor.gate.cli as cli

        _, _, staged_path = _patch_cli_seams(
            monkeypatch, tmp_path,
            verdict=_block_verdict(),
            preview={**_go_preview(), "verdict": "BLOCK",
                     "reasons": ["lockout"]},
        )

        try:
            cli.main(["analyze", "buy", "50", "ORCL",
                      "--type", "limit", "--limit", "145", "--json"])
        except SystemExit:
            pass

        out = capsys.readouterr().out
        token = json.loads(out)["token"]
        store = StagedOrderStore(staged_path, ttl_seconds=300.0)
        assert store.consume(token, _NOW) is not None


# ---------------------------------------------------------------------------
# Test: submit --token <T>
# ---------------------------------------------------------------------------

class TestSubmitToken:
    def _stage_intent(self, tmp_path) -> tuple[str, Path]:
        """Pre-stage an ORCL intent into a tmp file; return (token, path)."""
        staged_path = tmp_path / "staged_orders.json"
        store = StagedOrderStore(staged_path, ttl_seconds=300,
                                 token_factory=lambda: _FIXED_TOKEN)
        token = store.stage(_orcl_intent().model_dump(), _NOW)
        assert token == _FIXED_TOKEN
        return token, staged_path

    def test_submit_calls_submit_intent_once(self, monkeypatch, tmp_path, capsys):
        import governor.gate.cli as cli

        token, staged_path = self._stage_intent(tmp_path)

        _, submit_calls, _ = _patch_cli_seams(
            monkeypatch, tmp_path,
            verdict=_go_verdict(),   # unused for submit
            preview=_go_preview(),   # unused for submit
            submit_return=True,
        )

        # Point staged path at our pre-staged file
        monkeypatch.setattr(cli, "_staged_path", lambda config: staged_path)

        cli.main(["submit", "--token", token])

        assert len(submit_calls) == 1

    def test_submit_reconstructs_correct_intent(self, monkeypatch, tmp_path, capsys):
        import governor.gate.cli as cli

        token, staged_path = self._stage_intent(tmp_path)
        _, submit_calls, _ = _patch_cli_seams(
            monkeypatch, tmp_path,
            verdict=_go_verdict(),
            preview=_go_preview(),
            submit_return=True,
        )
        monkeypatch.setattr(cli, "_staged_path", lambda config: staged_path)

        cli.main(["submit", "--token", token])

        submitted_intent = submit_calls[0]
        assert submitted_intent.action is Action.BUY
        assert submitted_intent.symbol == "ORCL"
        assert submitted_intent.quantity == 50.0
        assert submitted_intent.order_type is OrderType.LIMIT
        assert submitted_intent.limit_price == 145.0

    def test_submit_valid_token_exits_zero(self, monkeypatch, tmp_path):
        import governor.gate.cli as cli

        token, staged_path = self._stage_intent(tmp_path)
        _patch_cli_seams(monkeypatch, tmp_path,
                         verdict=_go_verdict(), preview=_go_preview())
        monkeypatch.setattr(cli, "_staged_path", lambda config: staged_path)

        try:
            cli.main(["submit", "--token", token])
        except SystemExit as e:
            assert e.code == 0 or e.code is None


# ---------------------------------------------------------------------------
# Test: submit --token BOGUS (not in store) → error, submit_intent NOT called
# ---------------------------------------------------------------------------

class TestSubmitBogusToken:
    def test_bogus_token_exits_nonzero(self, monkeypatch, tmp_path):
        import governor.gate.cli as cli

        _patch_cli_seams(monkeypatch, tmp_path,
                         verdict=_go_verdict(), preview=_go_preview())

        with pytest.raises(SystemExit) as exc_info:
            cli.main(["submit", "--token", "BOGUS999"])
        assert exc_info.value.code != 0

    def test_bogus_token_does_not_call_submit_intent(self, monkeypatch, tmp_path, capsys):
        import governor.gate.cli as cli

        _, submit_calls, _ = _patch_cli_seams(
            monkeypatch, tmp_path,
            verdict=_go_verdict(),
            preview=_go_preview(),
        )

        try:
            cli.main(["submit", "--token", "BOGUS999"])
        except SystemExit:
            pass

        assert submit_calls == []

    def test_bogus_token_prints_error(self, monkeypatch, tmp_path, capsys):
        import governor.gate.cli as cli

        _patch_cli_seams(monkeypatch, tmp_path,
                         verdict=_go_verdict(), preview=_go_preview())

        try:
            cli.main(["submit", "--token", "BOGUS999"])
        except SystemExit:
            pass

        out = capsys.readouterr()
        combined = (out.out + out.err).lower()
        # Error message must identify the token as invalid/expired or echo it back
        assert (
            "invalid" in combined
            or "expired" in combined
            or "bogus999" in combined
        )


# ---------------------------------------------------------------------------
# Test: submit dry-run output branch (submit_intent returns False)
# ---------------------------------------------------------------------------

class TestSubmitDryRunOutput:
    """Cover the output branch where submit_intent returns False (dry-run / not sent)."""

    def _stage_intent(self, tmp_path) -> tuple[str, Path]:
        staged_path = tmp_path / "staged_orders.json"
        store = StagedOrderStore(staged_path, ttl_seconds=300,
                                 token_factory=lambda: _FIXED_TOKEN)
        token = store.stage(_orcl_intent().model_dump(), _NOW)
        assert token == _FIXED_TOKEN
        return token, staged_path

    def test_submit_dry_run_json_shows_placed_false_and_dry_run_true(
        self, monkeypatch, tmp_path, capsys
    ):
        import governor.gate.cli as cli

        token, staged_path = self._stage_intent(tmp_path)

        _patch_cli_seams(
            monkeypatch, tmp_path,
            verdict=_go_verdict(),
            preview=_go_preview(),
            submit_return=False,  # simulate dry-run / not placed
        )
        monkeypatch.setattr(cli, "_staged_path", lambda config: staged_path)

        cli.main(["submit", "--token", token, "--json"])

        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["placed"] is False
        assert data["dry_run"] is True

    def test_submit_dry_run_human_output_contains_dry_run_label(
        self, monkeypatch, tmp_path, capsys
    ):
        import governor.gate.cli as cli

        token, staged_path = self._stage_intent(tmp_path)

        _patch_cli_seams(
            monkeypatch, tmp_path,
            verdict=_go_verdict(),
            preview=_go_preview(),
            submit_return=False,
        )
        monkeypatch.setattr(cli, "_staged_path", lambda config: staged_path)

        cli.main(["submit", "--token", token])

        out = capsys.readouterr().out.upper()
        assert "DRY-RUN" in out or "NOT SENT" in out or "DRY" in out


# ---------------------------------------------------------------------------
# Test: armed-but-readonly misconfiguration warning on submit
# ---------------------------------------------------------------------------

class TestSubmitArmedButReadonlyWarning:
    """When dry_run=False and readonly=True the submit handler must warn on stderr."""

    def _stage_intent(self, tmp_path) -> tuple[str, Path]:
        staged_path = tmp_path / "staged_orders.json"
        store = StagedOrderStore(staged_path, ttl_seconds=300,
                                 token_factory=lambda: _FIXED_TOKEN)
        token = store.stage(_orcl_intent().model_dump(), _NOW)
        assert token == _FIXED_TOKEN
        return token, staged_path

    def test_armed_readonly_warns_on_stderr(self, monkeypatch, tmp_path, capsys):
        """submit with dry_run=False + readonly=True should print a 'readonly' warning."""
        import governor.gate.cli as cli

        token, staged_path = self._stage_intent(tmp_path)

        _, submit_calls, _ = _patch_cli_seams(
            monkeypatch, tmp_path,
            verdict=_go_verdict(),
            preview=_go_preview(),
            submit_return=True,
        )
        monkeypatch.setattr(cli, "_staged_path", lambda config: staged_path)

        # Override load_config to return a config where dry_run=False, readonly=True
        from governor.config import RulesConfig, LiveConfig
        armed_readonly_config = RulesConfig(
            live=LiveConfig(dry_run=False, readonly=True)
        )
        monkeypatch.setattr(cli, "load_config", lambda _path: armed_readonly_config)

        try:
            cli.main(["submit", "--token", token])
        except SystemExit:
            pass

        err = capsys.readouterr().err
        assert "readonly" in err.lower()


# ---------------------------------------------------------------------------
# Test: OrderIntent validation error (--type limit but no --limit) → clean error + nonzero exit
# ---------------------------------------------------------------------------

class TestValidationError:
    def test_limit_order_without_price_exits_nonzero(self, monkeypatch, tmp_path):
        import governor.gate.cli as cli

        _patch_cli_seams(monkeypatch, tmp_path,
                         verdict=_go_verdict(), preview=_go_preview())

        with pytest.raises(SystemExit) as exc_info:
            cli.main(["analyze", "buy", "50", "ORCL",
                      "--type", "limit"])   # missing --limit
        assert exc_info.value.code != 0

    def test_limit_order_without_price_prints_error(self, monkeypatch, tmp_path, capsys):
        import governor.gate.cli as cli

        _patch_cli_seams(monkeypatch, tmp_path,
                         verdict=_go_verdict(), preview=_go_preview())

        try:
            cli.main(["analyze", "buy", "50", "ORCL", "--type", "limit"])
        except SystemExit:
            pass

        out = capsys.readouterr()
        combined = out.out + out.err
        assert len(combined.strip()) > 0

    def test_limit_order_without_price_does_not_connect(self, monkeypatch, tmp_path):
        """Validation happens before any TWS connection attempt."""
        import governor.gate.cli as cli

        connect_calls = []
        _patch_cli_seams(monkeypatch, tmp_path,
                         verdict=_go_verdict(), preview=_go_preview())

        original_make = cli._make_connection

        def _tracking_connect(config):
            connect_calls.append(1)
            return original_make(config)

        monkeypatch.setattr(cli, "_make_connection", _tracking_connect)

        try:
            cli.main(["analyze", "buy", "50", "ORCL", "--type", "limit"])
        except SystemExit:
            pass

        assert connect_calls == [], "Should not connect before validation"


# ---------------------------------------------------------------------------
# Test: human-readable output (no --json) contains the key fields
# ---------------------------------------------------------------------------

class TestHumanReadableOutput:
    def test_human_output_contains_symbol_and_verdict(self, monkeypatch, tmp_path, capsys):
        import governor.gate.cli as cli

        _patch_cli_seams(monkeypatch, tmp_path,
                         verdict=_go_verdict(), preview=_go_preview())

        cli.main(["analyze", "buy", "50", "ORCL",
                  "--type", "limit", "--limit", "145"])

        out = capsys.readouterr().out
        assert "ORCL" in out
        assert "GO" in out

    def test_human_output_contains_submit_hint(self, monkeypatch, tmp_path, capsys):
        import governor.gate.cli as cli

        _patch_cli_seams(monkeypatch, tmp_path,
                         verdict=_go_verdict(), preview=_go_preview())

        cli.main(["analyze", "buy", "50", "ORCL",
                  "--type", "limit", "--limit", "145"])

        out = capsys.readouterr().out
        # Should hint at how to submit
        assert "submit" in out.lower() or "--token" in out


# ---------------------------------------------------------------------------
# Test: default values for --sec-type and --type
# ---------------------------------------------------------------------------

class TestArgDefaults:
    def test_default_sec_type_is_stk(self, monkeypatch, tmp_path, capsys):
        import governor.gate.cli as cli

        captured, _, _ = _patch_cli_seams(monkeypatch, tmp_path,
                                          verdict=_go_verdict(), preview=_go_preview())

        # No --sec-type given; --type market is default too
        cli.main(["analyze", "buy", "10", "AAPL"])
        intent = captured["intent"]
        assert intent.sec_type is SecType.STK

    def test_default_order_type_is_market(self, monkeypatch, tmp_path, capsys):
        import governor.gate.cli as cli

        captured, _, _ = _patch_cli_seams(monkeypatch, tmp_path,
                                          verdict=_go_verdict(), preview=_go_preview())

        cli.main(["analyze", "buy", "10", "AAPL"])
        intent = captured["intent"]
        assert intent.order_type is OrderType.MARKET
