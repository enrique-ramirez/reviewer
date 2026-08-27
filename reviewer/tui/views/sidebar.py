"""The repository list: what you are scoped to, and how each one is doing.

It exists only when there is a choice to make. Watching one repository it would
be a column of screen width spent naming the only repository there is.

Collapsing it does not hide it. A sidebar that vanishes takes the fact that
there *are* other repositories with it, so what is left is a rail of Pac-Man
ghosts — one per repository, coloured the way its row would have been. You can
still see there are three of them and that one has gone yellow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static

from .. import prose, theme
from ..formatting import clip
from ..models import PullRequest, owner_of, short_name
from ..session import ALL_REPOSITORIES, Session
from ..status import wants_you

NAME_WIDTH = 30
INDENT = "   "


@dataclass(frozen=True, slots=True)
class RepoStats:
    """One sidebar row's worth of "how is this doing"."""

    label: str
    subtitle: str
    open_count: int = 0
    reviewed: int = 0
    waiting: int = 0
    busy: bool = False
    is_everything: bool = False

    @property
    def glyph(self) -> str:
        return theme.ALL_REPOS if self.is_everything else theme.ONE_REPO

    @property
    def rail_glyph(self) -> str:
        return theme.PAC_OPEN if self.is_everything else theme.GHOST


def _tally(label: str, subtitle: str, rows: Sequence[PullRequest], **kwargs: bool) -> RepoStats:
    return RepoStats(
        label=label,
        subtitle=subtitle,
        open_count=len(rows),
        reviewed=sum(1 for pr in rows if pr.reviewed_by_us),
        waiting=sum(1 for pr in rows if wants_you(pr)),
        busy=any(pr.activity for pr in rows),
        **kwargs,
    )


def summarise(
    pull_requests: Sequence[PullRequest], session: Session
) -> tuple[RepoStats, ...]:
    """One entry per sidebar row, in the order ``Session.entries`` gives them.

    Counted over every watched repository rather than the scoped ones, which is
    the whole point: the sidebar is how you see what is waiting somewhere you
    are not looking.
    """
    by_repo: dict[str, list[PullRequest]] = {repo: [] for repo in session.repos}
    for pull_request in pull_requests:
        by_repo.setdefault(pull_request.repo, []).append(pull_request)

    count = len(session.repos)
    everything = _tally(
        ALL_REPOSITORIES,
        "1 repository" if count == 1 else f"{count} repositories",
        pull_requests,
        is_everything=True,
    )
    return (
        everything,
        *(_tally(short_name(r), owner_of(r), by_repo.get(r, ())) for r in session.repos),
    )


def _counts(stats: RepoStats) -> Text:
    """Open, how much of it we have looked at, how much wants a human."""
    line = prose.span(f"{stats.open_count} open", theme.MUTED)
    if stats.reviewed:
        line.append(f"  {stats.reviewed} reviewed", style=theme.MUTED)
    if stats.waiting:
        line.append(f"  {stats.waiting} need you", style=theme.NEEDS_YOU)
    return line


def entry_text(stats: RepoStats, *, chosen: bool, frame: int) -> Text:
    marker, marker_style = (
        (theme.spinner_frame(frame), theme.LIVE)
        if stats.busy
        else (stats.glyph, theme.KEY if chosen else theme.MUTED)
    )
    return prose.join(
        prose.span(theme.CURSOR if chosen else " ", theme.KEY if chosen else ""),
        prose.span(marker, marker_style),
        prose.line(f" {clip(stats.label, NAME_WIDTH)}", "bold" if chosen else ""),
        prose.line(f"{INDENT}{clip(stats.subtitle, NAME_WIDTH)}", theme.MUTED),
        prose.span(INDENT),
        _counts(stats),
    )


def rail_text(stats: RepoStats, *, chosen: bool, frame: int) -> Text:
    """One cell wide: still there, still says whether anything wants you."""
    if stats.busy:
        glyph, style = theme.spinner_frame(frame), theme.LIVE
    elif stats.waiting:
        glyph, style = stats.rail_glyph, theme.NEEDS_YOU
    else:
        glyph, style = stats.rail_glyph, theme.KEY if chosen else theme.MUTED
    return prose.join(
        prose.span(theme.CURSOR if chosen else " ", theme.KEY if chosen else ""),
        prose.span(glyph, style),
    )


class SidebarHeader(Static):
    """The title, and the thing you click to fold the sidebar away."""

    class Toggled(Message):
        pass

    def show(self, *, collapsed: bool) -> None:
        """One line either way: the arrow, then the title if there is room.

        The word "hide" went: the arrow already says what clicking does, and
        spending a second line on saying it again cost a repository row.
        """
        if collapsed:
            self.update(prose.span(theme.EXPAND, theme.FAINT))
            return
        self.update(
            prose.join(
                prose.span(f"{theme.COLLAPSE} ", theme.FAINT),
                prose.span("REPOSITORIES", theme.MUTED),
            )
        )

    def on_click(self, event: object) -> None:
        self.post_message(self.Toggled())


class RepoRow(Static):
    """One repository. Clicking it scopes every tab to that repository."""

    class Picked(Message):
        def __init__(self, index: int) -> None:
            super().__init__()
            self.index = index

    def __init__(self, index: int) -> None:
        super().__init__()
        self.index = index

    def show(
        self, stats: RepoStats, *, chosen: bool, collapsed: bool, frame: int
    ) -> None:
        self.set_class(chosen, "chosen")
        render = rail_text if collapsed else entry_text
        self.update(render(stats, chosen=chosen, frame=frame))

    def on_click(self, event: object) -> None:
        self.post_message(self.Picked(self.index))


class RepoSidebar(Vertical):
    """A focusable panel of clickable rows, keyboard and mouse alike."""

    can_focus = True

    BINDINGS = [
        Binding("up,k", "step(-1)", "Previous repo", show=False),
        Binding("down,j", "step(1)", "Next repo", show=False),
    ]

    def __init__(self, count: int) -> None:
        super().__init__()
        self.collapsed = False
        self._count = count
        self._chosen = 0

    def compose(self) -> ComposeResult:
        yield SidebarHeader()
        for index in range(self._count):
            yield RepoRow(index)
        yield Static(id="repobar_hint")

    @property
    def rows(self) -> list[RepoRow]:
        return list(self.query(RepoRow))

    def show(self, stats: Sequence[RepoStats], *, chosen: int, frame: int) -> None:
        self._chosen = chosen
        self.query_one(SidebarHeader).show(collapsed=self.collapsed)
        for row, item in zip(self.rows, stats):
            row.show(
                item,
                chosen=row.index == chosen,
                collapsed=self.collapsed,
                frame=frame,
            )
        self.query_one("#repobar_hint", Static).update(self._hint())

    def toggle(self) -> None:
        self.collapsed = not self.collapsed
        self.set_class(self.collapsed, "collapsed")

    def action_step(self, delta: int) -> None:
        self.post_message(RepoRow.Picked(self._chosen + delta))

    def _hint(self) -> Text:
        if self.collapsed:
            return Text()
        return prose.span(
            " ↑↓ pick · esc done" if self.has_focus else " e to switch", theme.FAINT
        )

    def on_click(self, event: object) -> None:
        self.focus()

    def on_focus(self) -> None:
        self.query_one("#repobar_hint", Static).update(self._hint())

    def on_blur(self) -> None:
        self.query_one("#repobar_hint", Static).update(self._hint())
