"""macOS desktop notifications via osascript. The arg builder is pure/testable;
`notify()` shells out (best-effort; never raises into the daemon)."""
from __future__ import annotations

import logging
import subprocess

log = logging.getLogger("governor.notify")


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def build_osascript_args(title: str, text: str) -> list[str]:
    safe_text = _escape(text)
    safe_title = _escape(title)
    return ["osascript", "-e", f'display notification "{safe_text}" with title "{safe_title}"']


def notify(title: str, text: str) -> None:
    try:
        subprocess.run(build_osascript_args(title, text), check=False,
                       capture_output=True, timeout=5)
    except Exception as exc:  # never let a notifier failure crash the brake
        log.error("macOS notify failed: %s", exc)
