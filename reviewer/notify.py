"""Desktop notifications.

macOS ``osascript`` by default, because it needs no install. Point
``notifications.command`` at ``terminal-notifier`` or any script taking
``--title`` and ``--message`` to use something else; set ``enabled: false`` to
go quiet.

A notification that fails is logged and forgotten — the reviewer should never
stop working because a toast did not appear.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any

from . import log


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def send(cfg: dict[str, Any], title: str, message: str, *, subtitle: str = "") -> None:
    if not cfg.get("enabled", True):
        return

    command = cfg.get("command") or "osascript"

    if shutil.which(command) is None:
        log.get().debug("notification command %r not found, skipping", command)
        return

    try:
        if command == "osascript":
            script = f'display notification "{_escape(message)}" with title "{_escape(title)}"'
            if subtitle:
                script += f' subtitle "{_escape(subtitle)}"'
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
        else:
            args = [command, "--title", title, "--message", message]
            if subtitle:
                args += ["--subtitle", subtitle]
            subprocess.run(args, capture_output=True, timeout=10)
    except Exception as exc:  # noqa: BLE001 - notifications are never load-bearing
        log.get().debug("notification failed: %s", exc)


def should_notify(events: list[str], event: str) -> bool:
    return event in events
