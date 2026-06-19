# tests/comms/test_notify.py
from governor.comms.notify import build_osascript_args


def test_build_osascript_args_escapes_quotes():
    args = build_osascript_args(title="Brake", text='he said "stop"')
    assert args[0] == "osascript" and args[1] == "-e"
    assert "display notification" in args[2]
    assert '\\"stop\\"' in args[2]  # embedded quotes escaped


def test_build_osascript_args_escapes_backslash():
    args = build_osascript_args(title="t", text="path C:\\x")
    # A single backslash in the input must be doubled to \\ in the AppleScript string.
    # We verify the output contains \\\\ (which is the Python literal for two backslashes).
    assert "\\\\" in args[2]
