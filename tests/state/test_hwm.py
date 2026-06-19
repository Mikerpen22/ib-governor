from governor.state.hwm import HwmStore


def test_hwm_rises_and_holds(tmp_path):
    s = HwmStore(tmp_path / "hwm.json")
    peak, dd = s.update(100.0)
    assert peak == 100.0 and dd == 0.0
    peak, dd = s.update(120.0)     # new high
    assert peak == 120.0 and dd == 0.0
    peak, dd = s.update(90.0)      # holds the peak
    assert peak == 120.0
    assert dd == (120.0 - 90.0) / 120.0
    assert s.drawdown_pct(90.0) == (120.0 - 90.0) / 120.0


def test_hwm_persists(tmp_path):
    p = tmp_path / "hwm.json"
    HwmStore(p).update(150.0)
    assert HwmStore(p).peak() == 150.0


def test_drawdown_zero_when_at_peak(tmp_path):
    s = HwmStore(tmp_path / "hwm.json")
    s.update(100.0)
    assert s.drawdown_pct(100.0) == 0.0
    assert s.drawdown_pct(110.0) == 0.0   # above prior peak -> no drawdown


def test_update_returns_drawdown_inline(tmp_path):
    """update() returns (peak, drawdown_pct) without a second disk read."""
    s = HwmStore(tmp_path / "hwm.json")
    s.update(200.0)
    peak, dd = s.update(180.0)
    assert peak == 200.0
    assert dd == (200.0 - 180.0) / 200.0


def test_corrupt_file_self_heals_loudly(tmp_path, caplog):
    """A corrupt HWM file must not blind the daemon (HWM only feeds WARN drawdown
    rules). It logs loudly and resets the peak from the next NAV."""
    import logging

    p = tmp_path / "hwm.json"
    p.write_text("{garbage")
    s = HwmStore(p)
    with caplog.at_level(logging.WARNING):
        peak, dd = s.update(150.0)
    assert peak == 150.0 and dd == 0.0                       # self-healed from current NAV
    assert any("unreadable" in r.getMessage().lower() for r in caplog.records)
    assert HwmStore(p).peak() == 150.0                       # file rewritten cleanly
