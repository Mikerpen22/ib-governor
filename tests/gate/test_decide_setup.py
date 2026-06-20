# tests/gate/test_decide_setup.py
"""Task 9: decide() setup-CAUTION clause.

Tests that a poor SetupAssessment escalates the verdict to CAUTION, good setup
stays GO, a hard BLOCK is never downgraded, and poor setup alone never BLOCKs.
"""
from governor.gate.analysis import GateFacts, decide, Verdict
from governor.technicals.types import SetupAssessment
from governor.model import Severity, Trip, AssetClass, ActionType


def _poor(reasons=("setup: not a confirmed Stage 2 (4/7)",)):
    return SetupAssessment(available=True, asset_class="equity", poor=True, caution_reasons=reasons)


def _ok():
    return SetupAssessment(available=True, asset_class="equity", poor=False, caution_reasons=())


def test_poor_setup_escalates_clean_trade_to_caution():
    v = decide(GateFacts(setup=_poor()))
    assert v.level is Verdict.CAUTION
    assert any("Stage 2" in r for r in v.reasons)


def test_good_setup_stays_go():
    assert decide(GateFacts(setup=_ok())).level is Verdict.GO


def test_setup_never_downgrades_a_block():
    hard = Trip(
        rule_id="daily_loss_stop",
        asset_class=AssetClass.FUTURE,
        severity=Severity.HARD,
        message="loss stop",
        action=ActionType.PLATFORM_OFF_TODAY,
    )
    v = decide(GateFacts(post_trade_trips=(hard,), setup=_ok()))
    assert v.level is Verdict.BLOCK


def test_poor_setup_alone_never_blocks():
    assert decide(GateFacts(setup=_poor())).level is Verdict.CAUTION  # never BLOCK
