"""Tests for GateFacts / GateVerdict / decide().

TDD Step 1 — these tests are written BEFORE the implementation exists.
All 8 cases from the spec, plus a precedence check.
"""
import pytest

from governor.gate.analysis import GateFacts, GateVerdict, Verdict, decide, SizingCheck
from governor.model import ActionType, AssetClass, Severity, Trip


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trip(severity: Severity, rule_id: str = "some.rule", message: str = "msg") -> Trip:
    return Trip(
        rule_id=rule_id,
        asset_class=AssetClass.FUTURE,
        severity=severity,
        message=message,
        action=ActionType.ALERT_ONLY,
    )


# ---------------------------------------------------------------------------
# Case 1: lockout_active → BLOCK, reason mentions "lockout"
# ---------------------------------------------------------------------------

def test_lockout_active_blocks():
    facts = GateFacts(lockout_active=True)
    verdict = decide(facts)
    assert verdict.level is Verdict.BLOCK
    assert any("lockout" in r for r in verdict.reasons)


# ---------------------------------------------------------------------------
# Case 2: HARD trip → BLOCK
# ---------------------------------------------------------------------------

def test_hard_trip_blocks():
    facts = GateFacts(
        post_trade_trips=(
            _trip(Severity.HARD, rule_id="futures.daily_loss_stop", message="daily loss exceeded"),
        )
    )
    verdict = decide(facts)
    assert verdict.level is Verdict.BLOCK
    assert len(verdict.reasons) == 1
    assert "futures.daily_loss_stop" in verdict.reasons[0]


# ---------------------------------------------------------------------------
# Case 3: buying_power_ok=False → BLOCK, reason mentions buying power
# ---------------------------------------------------------------------------

def test_insufficient_buying_power_blocks():
    facts = GateFacts(buying_power_ok=False)
    verdict = decide(facts)
    assert verdict.level is Verdict.BLOCK
    assert any("buying power" in r for r in verdict.reasons)


# ---------------------------------------------------------------------------
# Case 4: WARN trip only → CAUTION
# ---------------------------------------------------------------------------

def test_warn_trip_only_caution():
    facts = GateFacts(
        post_trade_trips=(_trip(Severity.WARN, rule_id="equity.concentration", message="too big"),)
    )
    verdict = decide(facts)
    assert verdict.level is Verdict.CAUTION
    assert "equity.concentration" in verdict.reasons[0]


# ---------------------------------------------------------------------------
# Case 5: sizing over_band only → CAUTION, reason mentions pct
# ---------------------------------------------------------------------------

def test_sizing_over_band_caution():
    facts = GateFacts(sizing=SizingCheck(pct_nav=0.02, over_band=True))
    verdict = decide(facts)
    assert verdict.level is Verdict.CAUTION
    # Reason should express the percentage
    assert any("2.0%" in r for r in verdict.reasons)


# ---------------------------------------------------------------------------
# Case 6: clean (all defaults, sizing ok) → GO, reasons empty
# ---------------------------------------------------------------------------

def test_clean_go():
    facts = GateFacts(sizing=SizingCheck(pct_nav=0.01, over_band=False))
    verdict = decide(facts)
    assert verdict.level is Verdict.GO
    assert verdict.reasons == ()


def test_all_defaults_go():
    """GateFacts() with zero trips and no explicit sizing → GO."""
    verdict = decide(GateFacts())
    assert verdict.level is Verdict.GO
    assert verdict.reasons == ()


# ---------------------------------------------------------------------------
# Case 7: HARD + WARN + sizing over_band → BLOCK (not CAUTION); HARD reason present
# ---------------------------------------------------------------------------

def test_precedence_hard_wins():
    hard_trip = _trip(Severity.HARD, rule_id="futures.daily_loss_stop", message="hard stop")
    warn_trip = _trip(Severity.WARN, rule_id="equity.concentration", message="warn msg")
    facts = GateFacts(
        post_trade_trips=(hard_trip, warn_trip),
        sizing=SizingCheck(pct_nav=0.05, over_band=True),
    )
    verdict = decide(facts)
    assert verdict.level is Verdict.BLOCK
    # The HARD reason must be present
    assert any("futures.daily_loss_stop" in r for r in verdict.reasons)


# ---------------------------------------------------------------------------
# Case 8: INFO trip alone → GO (INFO not surfaced)
# ---------------------------------------------------------------------------

def test_info_trip_does_not_surface():
    facts = GateFacts(
        post_trade_trips=(_trip(Severity.INFO, rule_id="info.only", message="just info"),)
    )
    verdict = decide(facts)
    assert verdict.level is Verdict.GO
    assert verdict.reasons == ()


# ---------------------------------------------------------------------------
# GateVerdict is frozen (immutability contract)
# ---------------------------------------------------------------------------

def test_gate_verdict_is_frozen():
    verdict = decide(GateFacts())
    with pytest.raises((AttributeError, TypeError)):
        verdict.level = Verdict.BLOCK  # type: ignore[misc]
