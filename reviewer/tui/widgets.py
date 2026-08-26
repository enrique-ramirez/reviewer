"""The pieces every view is assembled from."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from rich.text import Text
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Static

from . import theme
from .formatting import elapsed

Cells = Sequence[Text]
Signature = tuple[tuple[str, str], ...]
Rendered = tuple[str, Cells]

TRACK_DOTS = 22


@dataclass(frozen=True, slots=True)
class Column:
    label: str
    width: int | None = None


def signature_of(cells: Cells) -> Signature:
    return tuple((cell.plain, str(cell.style)) for cell in cells)


class SyncedTable(DataTable):
    """A table whose rows are updated in place, so the cursor holds still.

    Rebuilding every second would reset the selection under the reader, so the
    table is only rebuilt when the set of rows or their order changes. Otherwise
    the cells that actually differ are written one at a time — which is what lets
    a status turning into "reviewing" appear without the board flickering.
    """

    # DataTable binds these to scrolling within itself, which swallows them
    # before the app sees them. A page here is exactly one screenful, so there
    # is nothing to scroll and "page down" can only sensibly mean the next page.
    BINDINGS = [
        Binding("pagedown", "app.page_forward", "Next page", show=False),
        Binding("pageup", "app.page_back", "Prev page", show=False),
    ]

    def __init__(self, columns: Sequence[Column], **kwargs: Any) -> None:
        super().__init__(cursor_type="row", zebra_stripes=True, **kwargs)
        self._columns = tuple(columns)
        self._column_keys: list[Any] = []
        self._rendered: dict[str, Signature] = {}

    def on_mount(self) -> None:
        self._ensure_columns()

    def _ensure_columns(self) -> None:
        if not self._column_keys:
            self._column_keys = [
                self.add_column(column.label, width=column.width)
                for column in self._columns
            ]

    def sync(self, rows: Sequence[Rendered]) -> None:
        self._ensure_columns()
        if [key for key, _ in rows] != list(self._rendered):
            self._rebuild(rows)
            return
        for key, cells in rows:
            signature = signature_of(cells)
            if self._rendered[key] == signature:
                continue
            self._rendered[key] = signature
            for column, cell in zip(self._column_keys, cells):
                self.update_cell(key, column, cell)

    def refresh_cell(self, key: str, cells: Cells, index: int) -> None:
        """Redraw one column of one row, cheaply, between syncs.

        The whole row's signature is recorded alongside, or the next sync would
        see the animated cell as stale and rewrite it a frame behind.
        """
        if key not in self._rendered:
            return
        self._rendered[key] = signature_of(cells)
        self.update_cell(key, self._column_keys[index], cells[index])

    def invalidate(self) -> None:
        """Force a rebuild on the next sync."""
        self._rendered.clear()

    def _rebuild(self, rows: Sequence[Rendered]) -> None:
        cursor = self.cursor_row
        self.clear()
        self._rendered.clear()
        for key, cells in rows:
            self.add_row(*cells, key=key)
            self._rendered[key] = signature_of(cells)
        if rows:
            self.move_cursor(row=min(max(cursor, 0), len(rows) - 1))


class DetailPane(VerticalScroll):
    """The scrolling account of whichever row the cursor is on."""

    def __init__(self, **kwargs: Any) -> None:
        self._body = Static()
        super().__init__(self._body, **kwargs)

    def show(self, text: Text) -> None:
        self._body.update(text)


class StatusBar(Static):
    """The one-line footer under a view, describing what it is showing."""


@dataclass(frozen=True, slots=True)
class Progress:
    """How far through the wait between scans the reviewer is."""

    phase: str = ""
    remaining: float | None = None
    total: float | None = None

    @property
    def counting_down(self) -> bool:
        return bool(self.remaining and self.total)

    @property
    def fraction(self) -> float:
        if not self.counting_down:
            return 0.0
        assert self.total and self.remaining is not None
        return min(1.0, max(0.0, (self.total - self.remaining) / self.total))

    @property
    def countdown(self) -> str:
        minutes, seconds = divmod(int(self.remaining or 0), 60)
        return f"{minutes}m{seconds:02d}s"

    @classmethod
    def from_status(cls, status: Mapping[str, Any]) -> "Progress":
        return cls(
            phase=str(status.get("phase") or ""),
            remaining=status.get("remaining"),
            total=status.get("total"),
        )


def track_text(progress: Progress, frame: int) -> Text:
    """The wait until the next scan, as Pac-Man closing on a ghost."""
    eaten = int(progress.fraction * TRACK_DOTS)
    track = Text("  ")
    track.append(" " * eaten)
    track.append(theme.pac_frame(frame), style="bold yellow")
    track.append(theme.DOT * max(0, TRACK_DOTS - eaten), style=theme.FAINT)
    track.append(theme.GHOST, style=theme.URGENT)
    track.append(f"   next scan {progress.countdown}", style=theme.MUTED)
    return track


def phase_text(progress: Progress, running_for: float) -> Text:
    line = Text("  ")
    line.append(progress.phase, style=theme.KEY)
    if running_for >= 1:
        line.append(f"  ·  {elapsed(running_for)}", style="magenta")
    return line


class PacTimer(Static):
    """Purely decorative, and deliberately cheap: one line, twice a second.

    While a scan is actually running it steps aside and shows the phase, because
    that is the information that matters then.

    It lives in the header, beside the clock. What it says is true of the run
    rather than of any one tab, and it was previously wedged between the tabs
    and the log where it read as belonging to whichever pane was above it.
    """

    #: Width the clock reserves at the right edge of the header. Docked
    #: siblings both anchor to that edge rather than stacking, so this is
    #: reserved by hand — asserted in the tests, since overlapping the clock is
    #: not something a glance at the screen would necessarily catch.
    CLOCK_WIDTH = 10

    DEFAULT_CSS = """
    PacTimer {
        dock: right;
        width: auto;
        /* The clock's ten columns, plus a rule and a column of air either
           side of it: the two were legible on their own and read as one
           string together. */
        margin-right: 13;
        border-right: vkey $panel-lighten-2;
        content-align: right middle;
        padding-right: 1;
    }
    """

    REFRESH_SECONDS = 0.5

    def __init__(
        self,
        progress: Callable[[], Progress],
        running_for: Callable[[], float],
    ) -> None:
        super().__init__()
        self._progress = progress
        self._running_for = running_for
        self._frame = 0

    def on_mount(self) -> None:
        self.redraw()
        self.set_interval(self.REFRESH_SECONDS, self.redraw)

    def redraw(self) -> None:
        self._frame += 1
        progress = self._progress()
        if progress.counting_down:
            self.update(track_text(progress, self._frame))
        else:
            self.update(phase_text(progress, self._running_for()))
