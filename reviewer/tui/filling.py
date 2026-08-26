"""Background work, as the interface sees it.

Two jobs run on threads of their own and report through a dict: filling in
history, and writing a summary for a merge nobody watched land. This turns those
dicts into values, and into the lines the interface renders from them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from rich.text import Text

from . import prose, theme

WORKING = ("estimating", "running")
FINISHED = ("done", "error")


@dataclass(frozen=True, slots=True)
class BackfillStatus:
    phase: str = "idle"
    filed: int = 0
    scanned: int = 0
    total: int = 0
    requests: int = 0
    lines: tuple[str, ...] = ()
    message: str = ""

    @property
    def working(self) -> bool:
        return self.phase in WORKING

    @property
    def estimating(self) -> bool:
        return self.phase == "estimating"

    @property
    def awaiting_answer(self) -> bool:
        return self.phase == "confirm"

    @property
    def finished(self) -> bool:
        return self.phase in FINISHED

    @property
    def failed(self) -> bool:
        return self.phase == "error"

    @classmethod
    def from_status(cls, status: Mapping[str, Any]) -> "BackfillStatus":
        return cls(
            phase=str(status.get("phase") or "idle"),
            filed=int(status.get("filed") or 0),
            scanned=int(status.get("scanned") or 0),
            total=int(status.get("total") or 0),
            requests=int(status.get("requests") or 0),
            lines=tuple(str(line) for line in status.get("lines") or ()),
            message=str(status.get("message") or ""),
        )


def progress_note(status: BackfillStatus, frame: int) -> Text | None:
    """A line for the History status bar while a backfill is working."""
    if not status.working:
        return None
    spinner = theme.spinner_frame(frame)
    if status.estimating:
        return prose.span(f"  {spinner} working out how much there is…", theme.LIVE)
    # Counts what has been *checked*, not what was new. A repository already on
    # record files nothing, so a filed-based bar sits at 0 for the whole sweep
    # and is indistinguishable from a hang — which is exactly how it read.
    return prose.join(
        prose.span(f"  {spinner} filling history — {status.scanned:,}", theme.LIVE),
        prose.span(f" of about {status.total:,}", theme.MUTED) if status.total else None,
        prose.span(f" · {status.filed:,} new", theme.MUTED) if status.filed else None,
        prose.span("   b to stop", theme.FAINT),
    )


def in_flight_lines(status: BackfillStatus) -> tuple[Text, ...]:
    """What to admit to when the user asks to quit."""
    if status.estimating:
        return (prose.span("  sizing up a backfill", theme.LIVE),)
    if status.phase != "running":
        return ()
    counted = (
        f"   {status.scanned:,} of about {status.total:,}"
        if status.total
        else f"   {status.scanned:,} checked so far"
    )
    return (
        prose.join(
            prose.span("  filling history", theme.LIVE),
            prose.span(counted, theme.MUTED),
        ),
    )


@dataclass(frozen=True, slots=True)
class SummaryStatus:
    """Summaries being written on request, one merge at a time."""

    phase: str = "idle"
    current: str = ""
    pending: int = 0
    written: int = 0
    failed: int = 0
    message: str = ""

    @property
    def working(self) -> bool:
        return self.phase == "running"

    @property
    def finished(self) -> bool:
        return self.phase in FINISHED

    @property
    def failed_outright(self) -> bool:
        return self.phase == "error"

    @classmethod
    def from_status(cls, status: Mapping[str, Any]) -> "SummaryStatus":
        return cls(
            phase=str(status.get("phase") or "idle"),
            current=str(status.get("current") or ""),
            pending=int(status.get("pending") or 0),
            written=int(status.get("written") or 0),
            failed=int(status.get("failed") or 0),
            message=str(status.get("message") or ""),
        )


def summary_note(status: SummaryStatus, frame: int) -> Text | None:
    """A line for the History status bar while a summary is being written."""
    if not status.working or not status.current:
        return None
    queued = f" · {status.pending} queued" if status.pending else ""
    return prose.join(
        prose.span(
            f"  {theme.spinner_frame(frame)} summarising {status.current}", theme.LIVE
        ),
        prose.span(queued, theme.MUTED) if queued else None,
    )


def summary_in_flight(status: SummaryStatus) -> tuple[Text, ...]:
    """What to admit to when the user asks to quit."""
    if not status.working or not status.current:
        return ()
    queued = f"   {status.pending} more queued" if status.pending else ""
    return (
        prose.join(
            prose.span(f"  summarising {status.current}", theme.LIVE),
            prose.span(queued, theme.MUTED) if queued else None,
        ),
    )
