"""What has landed.

Summary is this session only — the counterweight to automating the reviewing,
so that changes still get read. History is the same record without the time
limit, across every watched repository, with filters for answering questions
about it.

Both show the same account of one merged pull request, so both are here.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable

from rich.text import Text
from textual.containers import Horizontal
from textual.widgets import Input, Label, Select

from .. import formatting, prose, theme
from ..data import MergePage
from ..models import Merge
from ..session import WINDOWS, Session
from ..widgets import Cells, Column
from .base import RecordView

AUTHOR_WIDTH = 16
REPO_WIDTH = 18

DESCRIPTION_NOTES = {
    "title": "the author's own title — backfilled history is not summarised, "
    "which is what keeps it free. press g to write one for this pull request",
    "review": "taken from our own review — the summary could not be written",
}


def _description(merge: Merge, placeholder: str, weak_style: str) -> tuple[str, str]:
    """The change in one line, and how confidently to present it.

    A backfilled row carries the author's title rather than a summary we wrote,
    and is shown plainly rather than dressed up as one.
    """
    if not merge.description:
        return placeholder, theme.MUTED
    return merge.description, "" if merge.described_by_model else weak_style


def _contribution(merge: Merge) -> Text:
    if not merge.reviewed_by_us:
        # Most of a backfilled history looks like this, and "0 comments over 0
        # rounds" would imply we looked and said nothing.
        return prose.field("from us", "not reviewed by this tool", theme.MUTED)
    detail = f"{merge.our_comments} comment(s) over {merge.our_reviews} round(s)"
    if merge.our_blockers:
        detail += f", {merge.our_blockers} blocking"
    # No colour: it merged, so however hard we pushed back at the time, nothing
    # here asks anything of anyone now.
    return prose.join(
        prose.field("from us", detail),
        prose.field("ended on", merge.last_event) if merge.last_event else None,
        _cost(merge),
    )


def _cost(merge: Merge) -> Text | None:
    """What reviewing it cost, totalled over every round.

    Every part is conditional: providers report different things, and a zero
    printed where one simply said nothing would read as a measurement.
    """
    cost = merge.cost
    if cost is None:
        return None
    parts: list[str] = []
    if cost.seconds:
        parts.append(formatting.elapsed(cost.seconds))
    if cost.tokens:
        parts.append(f"{formatting.tokens(cost.tokens)} tokens")
    if cost.cost_usd:
        parts.append(formatting.money(cost.cost_usd))
    if not parts:
        return None
    return prose.field("cost", " · ".join(parts) + (f"   {cost.label}" if cost.label else ""))


def _summary_paragraph(merge: Merge) -> Text:
    if not merge.description:
        return prose.join(
            prose.line("Summary not written yet. Press g to write one.", theme.MUTED),
            prose.blank(),
        )
    note = DESCRIPTION_NOTES.get(merge.description_source)
    return prose.join(
        prose.line(merge.description, "white"),
        prose.line(f"\n({note})", theme.MUTED) if note else None,
        prose.blank(),
    )


def _timings(merge: Merge) -> Text:
    open_for = merge.open_for
    merged_at = merge.merged_at
    return prose.join(
        prose.field("open for", formatting.duration(open_for))
        if open_for is not None
        else None,
        prose.field("merged", formatting.timestamp(merged_at)) if merged_at else None,
    )


def detail_text(merge: Merge) -> Text:
    """The full account of one merged pull request, for either tab."""
    ours = "  (yours)" if merge.is_ours else ""
    return prose.join(
        prose.headline(merge.number, merge.title, prose.span(merge.repo, theme.MUTED)),
        _summary_paragraph(merge),
        prose.field("author", f"@{merge.author}{ours}"),
        prose.field("merged by", f"@{merge.merged_by}") if merge.merged_by else None,
        prose.field("into", merge.base_ref or "?"),
        prose.field(
            "size",
            f"{formatting.churn(merge.additions, merge.deletions)} "
            f"in {merge.changed_files} files",
        ),
        _timings(merge),
        _contribution(merge),
        prose.field("labels", ", ".join(merge.labels)) if merge.labels else None,
        prose.span("\npress o to open it on GitHub", theme.MUTED),
    )


def _author_cell(merge: Merge) -> Text:
    return Text(
        formatting.clip(merge.author, AUTHOR_WIDTH),
        style=theme.KEY if merge.is_ours else theme.MUTED,
    )


def _churn_cell(merge: Merge) -> Text:
    return Text(formatting.churn(merge.additions, merge.deletions), style=theme.MUTED)


# --------------------------------------------------------------- this run

SUMMARY_COLUMNS = (
    Column("PR", 7),
    Column("Merged", 10),
    Column("Size", 12),
    Column("Author", 16),
    Column("Us", 5),
    Column("What changed"),
)


def summary_cells(merge: Merge, now: float) -> Cells:
    description, style = _description(merge, "summary pending…", "italic")
    return (
        Text(f"#{merge.number}", style="bold"),
        Text(formatting.ago(now - merge.landed_at), style=theme.MUTED),
        _churn_cell(merge),
        _author_cell(merge),
        Text(str(merge.our_comments or "–"), style=theme.MUTED),
        Text(formatting.one_line(description), style=style),
    )


class SummaryView(RecordView):
    """This run, unfiltered and unpaged: a session rarely sees more than a few."""

    COLUMNS = SUMMARY_COLUMNS

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._now = 0.0
        self._total = 0

    def show(self, page: MergePage, *, now: float) -> None:
        self._now, self._total = now, page.total
        super().show(page.merges)

    def row_cells(self, record: Merge) -> Cells:
        return summary_cells(record, self._now)

    def detail_text(self, record: Merge) -> Text:
        return detail_text(record)

    def empty_text(self) -> Text:
        return prose.span(
            "Nothing has merged since this run started.\n\n"
            "Pull requests we reviewed move here once they land. Everything on "
            "record is on the History tab.",
            theme.MUTED,
        )

    def status_text(self) -> Text:
        return prose.join(
            prose.span("  this run", theme.KEY),
            prose.span(f"   {self._total} merged", theme.MUTED),
            prose.span("   everything on record is in History", theme.FAINT),
        )


# ----------------------------------------------------------------- history

HISTORY_COLUMNS = (
    Column("Repo", 18),
    Column("PR", 7),
    Column("Merged", 12),
    Column("Size", 12),
    Column("Author", 16),
    Column("What changed"),
)

AUTHOR_FILTER_ID = "author_filter"
DATE_FILTER_ID = "date_filter"

#: The label beside the date picker, with its shortcut letter underlined — the
#: same convention the tab bar uses.
DATE_LABEL = "da[u]t[/u]es"


def history_cells(merge: Merge) -> Cells:
    description, style = _description(merge, formatting.MISSING, theme.MUTED)
    return (
        Text(formatting.clip(merge.short_repo, REPO_WIDTH), style=theme.MUTED),
        Text(f"#{merge.number}", style="bold"),
        Text(formatting.date_only(merge.landed_at), style=theme.MUTED),
        _churn_cell(merge),
        _author_cell(merge),
        Text(formatting.one_line(description), style=style),
    )


@dataclass(frozen=True, slots=True)
class HistoryContext:
    """Everything the History tab needs beyond the rows themselves."""

    session: Session
    page: MergePage
    can_backfill: bool = False
    note: Text | None = None


def history_status(context: HistoryContext) -> Text:
    if context.note is not None:
        return context.note
    session, page = context.session, context.page
    # Which repositories are in scope is not repeated here: the sidebar shows
    # it, highlighted, and saying it twice made the busiest line on the screen
    # carry the least new information.
    return prose.join(
        prose.span("  "),
        prose.span(f"{page.total:,} merged", theme.KEY),
        prose.span(f"   author ~ {session.author}", theme.NEEDS_YOU)
        if session.author
        else None,
    )


def history_pager(context: HistoryContext) -> Text:
    page = context.page
    if context.note is not None or page.pages <= 1:
        return Text()
    return prose.span(f"page {page.number + 1}/{page.pages}", theme.MUTED)


def history_empty(context: HistoryContext) -> Text:
    """What to say when there is nothing to show, which depends on why."""
    if context.session.narrowed:
        return prose.span(
            "Nothing matches those filters.\n\nEscape clears them.", theme.MUTED
        )
    if context.page.total:
        return prose.span("Nothing on this page.", theme.MUTED)

    start = "  press b" if context.can_backfill else "  ./run.sh --backfill"
    return prose.join(
        prose.line("No history yet.\n", theme.MUTED),
        prose.line(
            "Pull requests merged from now on land here by themselves. To fill "
            "in what merged before this tool existed:\n",
            theme.MUTED,
        ),
        prose.line(f"{start}\n", theme.KEY),
        prose.span(
            "It asks how far back to go, says how much it will fetch before "
            "fetching any of it, and makes no model calls.",
            theme.MUTED,
        ),
    )


class HistoryView(RecordView):
    """Everything on record, filtered and paged."""

    COLUMNS = HISTORY_COLUMNS

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._shown = HistoryContext(
            session=Session(repos=(), started_at=0.0), page=MergePage((), 0)
        )

    def filters(self) -> Iterable[Any]:
        return (
            Horizontal(
                Label(DATE_LABEL, id="date_label"),
                Select(
                    [(label, index) for index, (label, _) in enumerate(WINDOWS)],
                    value=0,
                    allow_blank=False,
                    compact=True,
                    id=DATE_FILTER_ID,
                ),
                id="history_filters",
            ),
        )

    def overlays(self) -> Iterable[Any]:
        return (Input(placeholder="author contains…", id=AUTHOR_FILTER_ID),)

    @property
    def author_box(self) -> Input:
        return self.query_one(f"#{AUTHOR_FILTER_ID}", Input)

    @property
    def dates(self) -> Select:
        """The date-range picker. Shows the current range as well as setting it."""
        return self.query_one(f"#{DATE_FILTER_ID}", Select)

    @property
    def page(self) -> MergePage:
        """Which slice of the history is on screen, and how many there are."""
        return self._shown.page

    def show(self, context: HistoryContext) -> None:
        self._shown = context
        # Keep the picker showing what is actually being filtered — Escape
        # clears the filters from elsewhere, and the control has to follow.
        picker = self.dates
        if picker.value != context.session.window:
            picker.value = context.session.window
        super().show(context.page.merges)

    def update_note(self, note: Text | None) -> None:
        """Repaint only the status bar, for the backfill spinner."""
        self._shown = replace(self._shown, note=note)
        self.redraw_status()

    def row_cells(self, record: Merge) -> Cells:
        return history_cells(record)

    def detail_text(self, record: Merge) -> Text:
        return detail_text(record)

    def empty_text(self) -> Text:
        return history_empty(self._shown)

    def status_text(self) -> Text:
        return history_status(self._shown)

    def pager_text(self) -> Text:
        return history_pager(self._shown)
