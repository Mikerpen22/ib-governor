import pytest

from governor.config import LiveConfig, RulesConfig


def test_live_defaults():
    lc = LiveConfig()
    assert lc.host == "127.0.0.1"
    assert lc.client_id == 4          # distinct from Desktop=2, Claude Code=3, ibkr-cli=1
    assert lc.readonly is True
    assert lc.dry_run is True         # start safe
    assert lc.briefing_times_et == ["10:30", "12:30", "15:55"]
    assert lc.session_close_et == "16:00"


def test_rulesconfig_has_live_by_default():
    assert isinstance(RulesConfig().live, LiveConfig)


def test_live_rejects_bad_time():
    with pytest.raises(ValueError):
        LiveConfig(session_close_et="25:00")


def test_live_rejects_bad_briefing_time():
    with pytest.raises(ValueError):
        LiveConfig(briefing_times_et=["10:30", "noon"])


def test_live_unattended_defaults():
    lc = LiveConfig()
    assert lc.gateway_restart_et == "23:59"
    assert lc.restart_quiet_window_min == 10.0
    assert lc.reconnect_alert_after_seconds == 90.0
    assert lc.weekly_relogin_reset_et == "01:00"
    assert lc.weekly_relogin_probe_et == "09:00"


def test_live_rejects_bad_restart_time():
    with pytest.raises(ValueError):
        LiveConfig(gateway_restart_et="24:00")


def test_live_rejects_bad_probe_time():
    with pytest.raises(ValueError):
        LiveConfig(weekly_relogin_probe_et="9am")
