"""Carrying log records from the reviewer thread to the event loop.

Textual widgets may only be touched from its own loop, so records queue here and
the app drains them on a timer. Bounded, so a run left open overnight cannot
grow without limit.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass

from ..log import redact
from .theme import MUTED, NEEDS_YOU, URGENT

BUFFER = 500

LEVEL_STYLES = ((logging.ERROR, URGENT), (logging.WARNING, NEEDS_YOU))


def style_for(levelno: int) -> str:
    for threshold, style in LEVEL_STYLES:
        if levelno >= threshold:
            return style
    return MUTED


@dataclass(frozen=True, slots=True)
class Line:
    text: str
    style: str


def format_record(record: logging.LogRecord) -> Line:
    stamp = time.strftime("%H:%M:%S", time.localtime(record.created))
    return Line(f"{stamp}  {redact(record.getMessage())}", style_for(record.levelno))


class LogRelay(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self._queue: deque[Line] = deque(maxlen=BUFFER)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        line = format_record(record)
        with self._lock:
            self._queue.append(line)

    def drain(self) -> tuple[Line, ...]:
        with self._lock:
            lines = tuple(self._queue)
            self._queue.clear()
        return lines
