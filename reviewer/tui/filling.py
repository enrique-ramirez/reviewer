"""The backfill, as the interface sees it.

The work runs on its own thread and reports through a dict; this turns that dict
into a value and into the two lines the interface renders from it.
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
    return prose.join(
        prose.span(f"  {spinner} filling history — {status.filed:,}", theme.LIVE),
        prose.span(f" of about {status.total:,}", theme.MUTED) if status.total else None,
        prose.span("   b to stop", theme.FAINT),
    )


def in_flight_lines(status: BackfillStatus) -> tuple[Text, ...]:
    """What to admit to when the user asks to quit."""
    if status.estimating:
        return (prose.span("  sizing up a backfill", theme.LIVE),)
    if status.phase != "running":
        return ()
    counted = (
        f"   {status.filed:,} of about {status.total:,}"
        if status.total
        else f"   {status.filed:,} so far"
    )
    return (
        prose.join(
            prose.span("  filling history", theme.LIVE),
            prose.span(counted, theme.MUTED),
        ),
    )
