import pytest
from governor.technicals.types import (
    Bar, Stage2Result, VcpResult, EquitySetup, FuturesSetup, SetupAssessment,
)

def test_bar_is_frozen():
    b = Bar(date="2026-06-19", open=1.0, high=2.0, low=0.5, close=1.5, volume=100.0)
    assert b.close == 1.5
    with pytest.raises(Exception):
        b.close = 9.0  # frozen

def test_setup_assessment_unavailable_default():
    s = SetupAssessment(available=False, asset_class="equity", poor=False,
                        caution_reasons=(), equity=None, futures=None)
    assert s.available is False and s.poor is False and s.caution_reasons == ()
