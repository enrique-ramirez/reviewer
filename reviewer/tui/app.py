"""The dashboard: three tabs, one poll, and the keys that move between them.

**Dashboard** is the live board — what is open right now and what wants a human.
**Summary** is what landed while you were watching, so automating the reviewing
does not mean losing track of what is going into the repository. **History** is
the same record without the time limit. The next-scan line and the log pane sit
below all three, because they describe the run rather than any one view.

The reviewer runs on a background thread and writes to SQLite; this reads the
same database on a timer and calls neither GitHub nor the model. Nothing here
mutates a record: a poll reads rows, turns them into values, and hands them to
views that render them.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.widgets import (
    Footer,
    Header,
    Input,
    RichLog,
    TabbedContent,
    TabPane,
)

from .. import backfill, model
from ..state import Store
from . import browser, data, filling, screens
from .logs import LogRelay
from .models import PullRequest
from .screens import Ask
from .session import Session
from .views import board as board_view
from .views.base import RecordView
from .views.board import BoardView
from .views.merges import HistoryContext, HistoryView, SummaryView
from .views.sidebar import RepoRow, RepoSidebar, RepoStats, SidebarHeader, summarise
from .widgets import PacTimer, Progress

POLL_SECONDS = 1.0
SPIN_SECONDS = 0.12

DASHBOARD, SUMMARY, HISTORY = "dashboard", "summary", "history"


def tab_label(title: str) -> str:
    """A tab's name with its hotkey letter underlined.

    Textual markup rather than a ``rich.Text``: ``TabPane`` puts its title
    through ``Content.from_markup``, which takes a string.
    """
    return f"[u]{title[0]}[/u]{title[1:]}"


@dataclass(frozen=True, slots=True)
class Runtime:
    """Everything the interface needs from the process around it."""

    store: Store
    repos: tuple[str, ...]
    relay: LogRelay
    stop: threading.Event
    status: Callable[[], Mapping[str, Any]]
    started_at: float
    backfiller: backfill.Runner | None = None


class Dashboard(App[None]):
    CSS_PATH = "styles.tcss"
    TITLE = "pr-reviewer"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        # The tab bar carries these itself with the letter underlined, so
        # repeating them below would spend a third of a one-line footer on what
        # is already on screen. `t` rather than `d` for the date range — `d`
        # reads as Dashboard once the tabs have letters.
        Binding("d", f"show_tab('{DASHBOARD}')", "Dashboard", show=False),
        Binding("s", f"show_tab('{SUMMARY}')", "Summary", show=False),
        Binding("h", f"show_tab('{HISTORY}')", "History", show=False),
        Binding("enter,o", "open", "Open in browser"),
        Binding("a", "toggle_filter", "Only needs me"),
        Binding("slash", "author_filter", "Filter author"),
        Binding("t", "cycle_window", "Date range"),
        Binding("e", "focus_repos", "Repos"),
        Binding("E", "toggle_repos", "Fold repos"),
        Binding("b", "backfill", "Fill history"),
        Binding("l", "toggle_log", "Log"),
        Binding("r", "reload", "Refresh"),
        Binding("left_square_bracket", "page_back", "Prev page", show=False),
        Binding("right_square_bracket", "page_forward", "Next page", show=False),
        Binding("j,down", "cursor_down", "Down", show=False),
        Binding("k,up", "cursor_up", "Up", show=False),
    ]

    # Keys that only mean something on one tab. Anything unlisted works
    # everywhere. Summary has none of its own: it shows this run and nothing
    # else, which is the whole point of it.
    TAB_ACTIONS = {
        "toggle_filter": DASHBOARD,
        "author_filter": HISTORY,
        "cycle_window": HISTORY,
        "page_back": HISTORY,
        "page_forward": HISTORY,
        "backfill": HISTORY,
    }

    SIDEBAR_ACTIONS = frozenset({"focus_repos", "toggle_repos"})

    def __init__(self, runtime: Runtime) -> None:
        super().__init__()
        self.runtime = runtime
        self.session = Session(repos=runtime.repos, started_at=runtime.started_at)
        self.pull_requests: tuple[PullRequest, ...] = ()
        self.repo_stats: tuple[RepoStats, ...] = ()
        self._frame = 0

    # ------------------------------------------------------------- layout

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="shell"):
            if self.session.multi_repo:
                yield RepoSidebar(len(self.session.entries))
            with Vertical(id="body"):
                with TabbedContent(initial=DASHBOARD):
                    with TabPane(tab_label("Dashboard"), id=DASHBOARD):
                        yield BoardView()
                    with TabPane(tab_label("Summary"), id=SUMMARY):
                        yield SummaryView()
                    with TabPane(tab_label("History"), id=HISTORY):
                        yield HistoryView()
                # Outside the tabs: these describe the run, not any one view.
                yield PacTimer(self._progress, self._longest_running)
                # Wrapped rather than clipped: a scan line carries the PR title,
                # and losing the end of it is what makes the log worth reading.
                yield RichLog(id="log", markup=False, highlight=False, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(POLL_SECONDS, self.poll)
        self.set_interval(SPIN_SECONDS, self.spin)
        self.poll()
        # The board, not the sidebar, however the widgets happen to be ordered.
        self.board.table.focus()

    # ------------------------------------------------------------- lookups

    @property
    def board(self) -> BoardView:
        return self.query_one(BoardView)

    @property
    def summary(self) -> SummaryView:
        return self.query_one(SummaryView)

    @property
    def history(self) -> HistoryView:
        return self.query_one(HistoryView)

    @property
    def sidebar(self) -> RepoSidebar | None:
        """None when watching one repository, and while the app is shutting down."""
        try:
            return self.query_one(RepoSidebar)
        except NoMatches:
            return None

    @property
    def tab(self) -> str:
        try:
            return self.query_one(TabbedContent).active
        except NoMatches:
            # Bindings are checked once before compose finishes.
            return DASHBOARD

    def view_for(self, tab: str) -> RecordView:
        if tab == SUMMARY:
            return self.summary
        if tab == HISTORY:
            return self.history
        return self.board

    @property
    def view(self) -> RecordView:
        return self.view_for(self.tab)

    @property
    def typing(self) -> bool:
        """True while the author box has focus, so letter keys stay letters."""
        return isinstance(self.focused, Input)

    @property
    def asking(self) -> bool:
        return isinstance(self.screen, Ask)

    def backfill_status(self) -> filling.BackfillStatus:
        runner = self.runtime.backfiller
        if runner is None:
            return filling.BackfillStatus()
        return filling.BackfillStatus.from_status(runner.status())

    # ---------------------------------------------------------------- data

    def poll(self) -> None:
        # A timer can fire once more after the app has begun tearing its widgets
        # down, and redrawing a tree that is going away is never worth an error.
        if not self.is_running:
            return
        self._drain_log()
        if self.runtime.stop.is_set():
            self.exit()
            return
        self.reload()
        self._advance_backfill()

    def reload(self) -> None:
        now = time.time()
        self._reload_board(now)
        self._reload_summary(now)
        self._reload_history(now)
        self._reload_sidebar()

    def _reload_board(self, now: float) -> None:
        everything = data.open_pull_requests(self.runtime.store, self.session.repos)
        # Summarised before the scope filter, so the sidebar can say what is
        # waiting in the repositories you are not looking at.
        self.repo_stats = summarise(everything, self.session)
        self.pull_requests = board_view.in_view(everything, self.session)
        self.board.show(self.pull_requests, now=now, frame=self._frame)
        self.sub_title = board_view.subtitle(self.pull_requests, self.session)

    def _reload_summary(self, now: float) -> None:
        self.summary.show(
            data.merges_this_run(self.runtime.store, self.session), now=now
        )

    def _reload_history(self, now: float) -> None:
        page = data.merge_history(self.runtime.store, self.session, now)
        self.session = self.session.with_page(page.number)
        self.history.show(
            HistoryContext(
                session=self.session,
                page=page,
                can_backfill=self.runtime.backfiller is not None,
                note=filling.progress_note(self.backfill_status(), self._frame),
            )
        )

    def _reload_sidebar(self) -> None:
        sidebar = self.sidebar
        if sidebar is not None:
            sidebar.show(
                self.repo_stats, chosen=self.session.repo_index, frame=self._frame
            )

    def _drain_log(self) -> None:
        pane = self.query_one("#log", RichLog)
        for line in self.runtime.relay.drain():
            pane.write(Text(line.text, style=line.style))

    def spin(self) -> None:
        """Advance the spinners, doing no database work.

        Runs about eight times a second, so it redraws from the values already
        in hand and touches only the cells that animate. When nothing is under
        way — the usual case — it costs three checks and returns.
        """
        if not self.is_running:
            return
        status = self.backfill_status()
        # The sidebar spins on every tab, so a review running in a repository
        # you are not looking at is still visibly running.
        elsewhere = self.session.multi_repo and any(s.busy for s in self.repo_stats)
        on_board = self.tab == DASHBOARD and any(
            pr.activity for pr in self.pull_requests
        )
        on_history = self.tab == HISTORY and status.working
        if not (elsewhere or on_board or on_history):
            return

        self._frame += 1
        if elsewhere:
            self._reload_sidebar()
        if on_board:
            self.board.animate(time.time(), self._frame)
        if on_history:
            self.history.update_note(filling.progress_note(status, self._frame))

    def _progress(self) -> Progress:
        return Progress.from_status(self.runtime.status())

    def _longest_running(self) -> float:
        return board_view.longest_running(self.pull_requests, time.time())

    # ------------------------------------------------------------ bindings

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Offer only the keys that do something on the tab in view.

        ``False`` means "disabled and not shown", which is what we want: the
        footer is one line, and a key advertised where it silently does nothing
        is worse than one not offered. Returning ``None`` would leave it there,
        greyed out.
        """
        if action in self.SIDEBAR_ACTIONS:
            return self.session.multi_repo
        owner = self.TAB_ACTIONS.get(action)
        return owner is None or owner == self.tab

    @on(TabbedContent.TabActivated)
    def _tab_changed(self) -> None:
        # The footer caches what it drew; without this it keeps the old tab's
        # keys until something else forces a redraw.
        self.refresh_bindings()
        # Focus follows the tab, by key or by click. Textual re-activates
        # whichever pane holds the focused widget, so leaving focus behind on
        # the old tab's table drags the tab straight back with it.
        self.view.table.focus()

    # ------------------------------------------------------------- actions

    def action_reload(self) -> None:
        self.reload()

    def action_show_tab(self, tab: str) -> None:
        if not self.typing:
            self.query_one(TabbedContent).active = tab
            self.view_for(tab).table.focus()

    def action_toggle_filter(self) -> None:
        if self.typing or self.tab != DASHBOARD:
            return
        self.session = self.session.with_attention_only(not self.session.only_attention)
        self._reload_board(time.time())

    def action_open(self) -> None:
        record = self.view.current
        if record is not None and record.url and browser.open_url(record.url):
            self.notify(f"opened #{record.number} in your browser", timeout=2)

    def action_toggle_log(self) -> None:
        """Give the log's ten lines back to whichever table is showing.

        The relay keeps filling while it is hidden, so bringing it back shows
        what happened in the meantime rather than starting blank.
        """
        if not self.typing:
            self.query_one("#log", RichLog).toggle_class("hidden")

    def action_cursor_down(self) -> None:
        self._move_cursor(1)

    def action_cursor_up(self) -> None:
        self._move_cursor(-1)

    def _move_cursor(self, delta: int) -> None:
        # The sidebar owns the arrows while it has focus, through its own
        # bindings, so this only ever moves the table in view.
        if self.typing:
            return
        table = self.view.table
        table.action_cursor_down() if delta > 0 else table.action_cursor_up()

    def action_focus_repos(self) -> None:
        """Hand the arrow keys to the sidebar, and take them back again."""
        sidebar = self.sidebar
        if self.typing or sidebar is None:
            return
        if sidebar.has_focus:
            self.view.table.focus()
            return
        if sidebar.collapsed:
            self._set_sidebar_collapsed(False)
        sidebar.focus()

    def action_toggle_repos(self) -> None:
        """Fold the sidebar down to a rail, and back."""
        sidebar = self.sidebar
        if self.typing or sidebar is None:
            return
        self._set_sidebar_collapsed(not sidebar.collapsed)

    def _set_sidebar_collapsed(self, collapsed: bool) -> None:
        sidebar = self.sidebar
        if sidebar is None or sidebar.collapsed == collapsed:
            return
        sidebar.toggle()
        if collapsed and sidebar.has_focus:
            self.view.table.focus()
        self._reload_sidebar()

    @on(RepoRow.Picked)
    def _repo_picked(self, event: RepoRow.Picked) -> None:
        event.stop()
        self.session = self.session.with_repo(event.index)
        self.reload()

    @on(SidebarHeader.Toggled)
    def _sidebar_toggled(self, event: SidebarHeader.Toggled) -> None:
        event.stop()
        self._set_sidebar_collapsed(not self.sidebar.collapsed)

    # ------------------------------------------------------- history tab

    def action_cycle_window(self) -> None:
        if self.typing or self.tab != HISTORY:
            return
        self.session = self.session.with_next_window()
        self._reload_history(time.time())

    def action_author_filter(self) -> None:
        if self.tab != HISTORY:
            return
        box = self.history.author_box
        box.add_class("active")
        box.focus()

    @on(Input.Submitted, "#author_filter")
    def _apply_author(self, event: Input.Submitted) -> None:
        self.session = self.session.with_author(event.value)
        self._close_author_box()
        self._reload_history(time.time())

    def _close_author_box(self) -> None:
        box = self.history.author_box
        box.value = self.session.author
        box.remove_class("active")
        self.view.table.focus()

    def action_page_back(self) -> None:
        if self.typing or self.tab != HISTORY or self.session.page == 0:
            return
        self.session = self.session.with_page(self.session.page - 1)
        self._reload_history(time.time())

    def action_page_forward(self) -> None:
        if self.typing or self.tab != HISTORY:
            return
        self.session = self.session.with_page(self.session.page + 1)
        self._reload_history(time.time())

    def on_key(self, event: Any) -> None:
        """Escape: out of the author box, or out of the filters entirely.

        While typing it restores what was there before, so a half-typed name is
        never applied. Otherwise it clears the author and the date range in one
        go — filters are easy to set and were otherwise fiddly to undo.
        """
        if event.key != "escape":
            return
        sidebar = self.sidebar
        if self.typing:
            self._close_author_box()
        elif sidebar is not None and sidebar.has_focus:
            self.view.table.focus()
        elif self.tab == HISTORY and self.session.filtered:
            self.session = self.session.unfiltered()
            self.history.author_box.value = ""
            self._reload_history(time.time())
        else:
            return
        event.stop()

    # ------------------------------------------------------------ backfill

    def action_backfill(self) -> None:
        runner = self.runtime.backfiller
        if self.typing or self.tab != HISTORY or self.asking:
            return
        if runner is None:
            self.notify("backfill needs ./run.sh --backfill here", timeout=4)
            return
        if runner.busy:
            # The same key stops it. The sweep ends between pages and keeps
            # everything it has already committed.
            runner.cancel()
            self.notify("stopping the backfill…", timeout=3)
            return
        # The estimate needs a round trip to GitHub; poll() picks the answer up
        # and asks the second question when it arrives.
        self.push_screen(screens.range_question(), self._begin_backfill)

    def _begin_backfill(self, range_key: str | None) -> None:
        if range_key and self.runtime.backfiller is not None:
            self.runtime.backfiller.start(range_key)

    def _advance_backfill(self) -> None:
        """Move a running backfill along, from the poll timer.

        The work is on its own thread; this only notices when it has a question
        or an answer.
        """
        runner = self.runtime.backfiller
        if runner is None or self.asking:
            return
        status = self.backfill_status()

        if status.awaiting_answer:
            self.push_screen(
                screens.confirm_question(status),
                lambda reply: self._answer_backfill(reply, status),
            )
            return

        if status.finished:
            self.notify(
                status.message or "backfill finished",
                timeout=6,
                severity="error" if status.failed else "information",
            )
            runner.dismiss()
            self.history.table.invalidate()

    def _answer_backfill(
        self, reply: str | None, status: filling.BackfillStatus
    ) -> None:
        runner = self.runtime.backfiller
        if runner is None:
            return
        if reply == screens.YES and status.total:
            runner.confirm()
        else:
            runner.cancel()

    # ---------------------------------------------------------------- quit

    def action_quit(self) -> None:  # type: ignore[override]
        """Ask first. Quitting throws away minutes of work and a real bill."""
        if self.asking:
            return
        self.push_screen(
            screens.quit_question(self._in_flight(), model.live_count()),
            self._answer_quit,
        )

    def _in_flight(self) -> tuple[Text, ...]:
        return (
            *board_view.work_in_flight(self.pull_requests, time.time()),
            *filling.in_flight_lines(self.backfill_status()),
        )

    def _answer_quit(self, reply: str | None) -> None:
        if reply == screens.YES:
            self.runtime.stop.set()
            self.exit()


def run(runtime: Runtime) -> None:
    Dashboard(runtime).run()
    runtime.stop.set()
