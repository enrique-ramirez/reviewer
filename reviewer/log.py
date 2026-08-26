"""Logging.

Two rules this module exists to enforce:

1. Normal logs carry repo, PR number, decision, and counts. Diff bodies, comment
   bodies, and prompts stay out, because these repositories are not ours and
   their contents should not accumulate in a logfile.
2. Anything genuinely sensitive that does reach a log line is redacted on the
   way out, so a stray f-string cannot leak a token.

Full prompts and responses are available under ``--debug``, written to
per-review files in the state directory rather than to the log.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

_TOKEN_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]{20,}"),
]

LOGGER_NAME = "pr-reviewer"


def redact(text: str) -> str:
    """Replace anything that looks like a credential with a marker."""
    for pattern in _TOKEN_PATTERNS:
        text = pattern.sub("<redacted>", text)
    return text


class _RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


# ANSI, applied only when writing to a terminal. A logfile stays plain text so
# grep and `less` are not full of escape codes.
_ANSI = {
    "reset": "\033[0m",
    "dim": "\033[2m",
    "bold": "\033[1m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "cyan": "\033[36m",
}

_LEVEL_COLOR = {
    "DEBUG": "dim",
    "INFO": "cyan",
    "WARNING": "yellow",
    "ERROR": "red",
    "CRITICAL": "red",
}


def _colors_enabled(stream: Any) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("PR_REVIEWER_COLOR") == "always":
        return True
    return bool(getattr(stream, "isatty", lambda: False)())


class _ColorFormatter(_RedactingFormatter):
    """Colour by level, and pick out the parts the eye needs.

    The log is read while something else is happening in another window, so what
    matters is spotting the lines that changed state — a review going out, a
    skip, an error — without reading every word.
    """

    def __init__(self, enabled: bool) -> None:
        super().__init__("%(asctime)s  %(message)s", datefmt="%H:%M:%S")
        self.enabled = enabled

    def format(self, record: logging.LogRecord) -> str:
        message = redact(record.getMessage())
        stamp = self.formatTime(record, self.datefmt)

        if not self.enabled:
            return f"{stamp}  {record.levelname:<7}  {message}"

        c = _ANSI
        level_colour = c[_LEVEL_COLOR.get(record.levelname, "reset")]

        if record.levelno >= logging.ERROR:
            marker = f"{c['red']}{c['bold']}  ✗ {c['reset']}"
        elif record.levelno >= logging.WARNING:
            marker = f"{c['yellow']}  ! {c['reset']}"
        else:
            marker = "    "

        # Highlight the repo#number so a line's subject is findable at a glance.
        message = re.sub(
            r"(\S+/\S+#\d+)",
            lambda m: f"{c['bold']}{m.group(1)}{c['reset']}",
            message,
            count=1,
        )
        for word, colour in (
            ("submitted", "green"),
            ("skipped", "dim"),
            ("reviewing", "blue"),
        ):
            message = message.replace(word, f"{c[colour]}{word}{c['reset']}", 1)

        return f"{c['dim']}{stamp}{c['reset']}{marker}{level_colour if record.levelno >= logging.WARNING else ''}{message}{c['reset']}"


def setup(level: str = "INFO", logfile: Path | None = None) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()
    logger.propagate = False

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(_ColorFormatter(_colors_enabled(sys.stdout)))
    logger.addHandler(stream)

    if logfile is not None:
        logfile.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(logfile, encoding="utf-8")
        file_handler.setFormatter(
            _RedactingFormatter(
                "%(asctime)s  %(levelname)-7s  %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(file_handler)

    return logger


def get() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


class DebugSink:
    """Writes full prompts and responses to disk when ``--debug`` is on.

    Off by default. When on, everything lands under
    ``<state_dir>/debug/<repo>/<pr>/`` so it is easy to find and easy to delete,
    and never inside the checkout.
    """

    def __init__(self, root: Path | None) -> None:
        self.root = root
        if root is not None:
            root.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return self.root is not None

    def write(self, repo: str, pr_number: int, name: str, content: Any) -> None:
        if self.root is None:
            return
        target = self.root / repo.replace("/", "__") / str(pr_number)
        target.mkdir(parents=True, exist_ok=True)
        path = target / name
        if isinstance(content, (dict, list)):
            payload = json.dumps(content, indent=2, ensure_ascii=False)
        else:
            payload = str(content)
        path.write_text(redact(payload), encoding="utf-8")
        os.chmod(path, 0o600)
        get().debug("debug artifact written: %s", path)
