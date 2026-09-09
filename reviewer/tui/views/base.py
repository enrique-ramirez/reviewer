"""A table beside a detail pane, with a status line underneath.

All three tabs are this shape. Subclasses supply the columns and the pure
functions that turn one record into cells and into prose; everything about
keeping the table, the detail and the status bar in step lives here once.
"""

from __future__ import annotations

from typing import Any, ClassVar, Iterable, Sequence

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable

from ..widgets import (
    Action,
    ActionBar,
    Cells,
    Column,
    DetailPane,
    StatusBar,
    SyncedTable,
)

OPEN_ON_GITHUB = Action(id="open", label="Open on GitHub", key="o")
READ_REVIEW = Action(id="conversation", label="Read the review", key="c")


class RecordView(Vertical):
    COLUMNS: ClassVar[tuple[Column, ...]] = ()

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._records: tuple[Any, ...] = ()

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(classes="listing"):
                yield SyncedTable(self.COLUMNS)
                with Horizontal(classes="underbar"):
                    yield StatusBar(classes="counts")
                    yield StatusBar(classes="pager")
                    yield from self.filters()
                yield from self.overlays()
            with Vertical(classes="details"):
                yield DetailPane()
                yield ActionBar(classes="actions")

    def filters(self) -> Iterable[Any]:
        return ()

    def overlays(self) -> Iterable[Any]:
        return ()

    @property
    def table(self) -> SyncedTable:
        return self.query_one(SyncedTable)

    @property
    def detail(self) -> DetailPane:
        return self.query_one(DetailPane)

    @property
    def action_bar(self) -> ActionBar:
        return self.query_one(ActionBar)

    @property
    def status_bar(self) -> StatusBar:
        return self.query_one("StatusBar.counts", StatusBar)

    @property
    def pager_bar(self) -> StatusBar:
        return self.query_one("StatusBar.pager", StatusBar)

    @property
    def records(self) -> tuple[Any, ...]:
        return self._records

    @property
    def current(self) -> Any | None:
        index = self.table.cursor_row
        return self._records[index] if 0 <= index < len(self._records) else None

    def show(self, records: Sequence[Any]) -> None:
        self._records = tuple(records)
        self.table.sync([(self.row_key(r), self.row_cells(r)) for r in self._records])
        self.redraw_detail()
        self.redraw_status()

    def redraw_detail(self) -> None:
        record = self.current
        width = self.detail.content_size.width
        self.detail.show(
            self.empty_text()
            if record is None
            else self.detail_text(record, width=width)
        )
        left, right = self.actions(record)
        self.action_bar.show(left, right)

    def redraw_status(self) -> None:
        self.status_bar.update(
            self.status_text(width=self.status_bar.content_size.width)
        )
        self.pager_bar.update(self.pager_text())

    @on(DataTable.RowHighlighted)
    def _follow_cursor(self) -> None:
        self.redraw_detail()

    def row_key(self, record: Any) -> str:
        return str(record.key)

    def row_cells(self, record: Any) -> Cells:
        raise NotImplementedError

    def detail_text(self, record: Any, *, width: int = 0) -> Text:
        raise NotImplementedError

    def actions(
        self, record: Any
    ) -> tuple[tuple[Action, ...], Action | None]:
        if record is None or not getattr(record, "url", ""):
            return ((), None)
        return ((), OPEN_ON_GITHUB)

    def empty_text(self) -> Text:
        raise NotImplementedError

    def status_text(self, *, width: int = 0) -> Text:
        """What the bar under the list says. ``width`` is the space it has.

        Zero means "not laid out yet, do not try to fit anything".
        """
        raise NotImplementedError

    def pager_text(self) -> Text:
        return Text()
