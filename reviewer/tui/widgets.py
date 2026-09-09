"""The pieces every view is assembled from."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from rich.text import Text
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.app import ComposeResult
from textual.widgets import Button, DataTable, Static

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
        if key not in self._rendered:
            return
        self._rendered[key] = signature_of(cells)
        self.update_cell(key, self._column_keys[index], cells[index])

    def invalidate(self) -> None:
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
    def __init__(self, **kwargs: Any) -> None:
        self._body = Static()
        super().__init__(self._body, **kwargs)

    def show(self, text: Text) -> None:
        self._body.update(text)


@dataclass(frozen=True, slots=True)
class Action:
    id: str
    label: str
    key: str

    @property
    def markup(self) -> str:
        index = self.label.lower().find(self.key.lower())
        if index < 0:
            return f"{self.label} ({self.key})"
        letter = self.label[index]
        return f"{self.label[:index]}[u]{letter}[/u]{self.label[index + 1:]}"


class ActionBar(Horizontal):
    SLOTS = ("primary", "second")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Which action each slot is carrying. The button cannot hold it:
        # ``Button.name`` is read-only. Its id names the slot rather than the
        # action, which changes from row to row.
        self._carrying: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        for slot in self.SLOTS:
            yield Button("", id=f"action-{slot}", compact=True, classes="action")
        yield Button("", id="action-open", compact=True, classes="action")

    def carried_by(self, button: Button) -> str:
        return self._carrying.get(button.id or "", "")

    def show(self, left: Sequence[Action], right: Action | None) -> None:
        padded: list[Action | None] = list(left[: len(self.SLOTS)])
        padded += [None] * (len(self.SLOTS) - len(padded))
        for slot, action in zip(self.SLOTS, padded):
            self._fill(f"action-{slot}", action)
        self._fill("action-open", right)

    def _fill(self, button_id: str, action: Action | None) -> None:
        button = self.query_one(f"#{button_id}", Button)
        button.display = action is not None
        self._carrying[button_id] = action.id if action else ""
        if action is not None:
            button.label = action.markup
            button.tooltip = f"{action.label}  ({action.key})"


class StatusBar(Static):
    pass


@dataclass(frozen=True, slots=True)
class Progress:
    phase: str = ""
    remaining: float | None = None
    total: float | None = None
    paused: bool = False

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
            paused=bool(status.get("paused")),
        )


def track_text(progress: Progress, frame: int) -> Text:
    eaten = int(progress.fraction * TRACK_DOTS)
    track = Text("  ")
    track.append(" " * eaten)
    track.append(theme.pac_frame(0 if progress.paused else frame), style="bold yellow")
    track.append(theme.DOT * max(0, TRACK_DOTS - eaten), style=theme.FAINT)
    track.append(theme.GHOST, style=theme.URGENT)
    if progress.paused:
        track.append(f"   paused at {progress.countdown}", style=theme.NEEDS_YOU)
    else:
        track.append(f"   next scan {progress.countdown}", style=theme.MUTED)
    return track


def phase_text(progress: Progress, running_for: float) -> Text:
    line = Text("  ")
    line.append(progress.phase, style=theme.KEY)
    if running_for >= 1:
        line.append(f"  ·  {elapsed(running_for)}", style="magenta")
    return line


class PacTimer(Static):
    #: Width the clock reserves at the right edge of the header. Docked
    #: siblings both anchor to that edge rather than stacking, so this is
    #: reserved by hand. The tests assert it, since overlapping the clock is
    #: not something a glance at the screen would necessarily catch.
    CLOCK_WIDTH = 10

    DEFAULT_CSS = """
    PacTimer {
        dock: right;
        width: auto;
        /* Full height so it centres itself when the header is clicked taller,
           the way the icon and the clock do. Left at one row it stayed pinned
           to the top while everything around it moved to the middle. */
        height: 100%;
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
