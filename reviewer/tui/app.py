"""The dashboard: three tabs, one poll, and the keys that move between them.

**Dashboard** is the live board — what is open right now and what wants a human.
**Summary** is what landed while you were watching, so automating the reviewing
does not mean losing track of what is going into the repository. **History** is
the same record without the time limit. The next-scan line lives in the header
and the log pane below all three, because both describe the run rather than any
one view — and a run-wide line wedged between a tab and the log read as
belonging to whichever pane was above it.

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
from textual.widgets._header import HeaderClock, HeaderIcon, HeaderTitle
from textual.widgets import (
    Footer,
    Header,
    Button,
    Input,
    RichLog,
    Select,
    TabbedContent,
    TabPane,
)

from .. import backfill, model, summarize
from ..state import Store
from . import browser, data, filling, screens, theme
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


class RunHeader(Header):
    """Textual's header, with the run's own status line added to it.

    Composed after the clock on purpose: two widgets docked right stack inward
    from the edge, so the clock keeps the corner and this sits to its left.

    Clicking the header makes it three rows tall. The icon and the clock fill
    that height and centre themselves in it; the title and the countdown were
    one row each and stayed pinned to the top, so a taller header left them
    sitting above everything else. Both are given the height to centre in.
    """

    DEFAULT_CSS = """
    RunHeader HeaderTitle {
        height: 100%;
        content-align: center middle;
    }
    """

    def format_title(self) -> Text:
        """The title, with the ghost in Blinky's own red.

        The same red the ghost at the end of the countdown wears, so the two are
        recognisably the same character.

        Presentation only: ``app.title`` stays a plain string, so anything that
        reads it — the terminal's own tab, a test — gets the name rather than
        markup.
        """
        title = self.screen_title
        text = Text(no_wrap=True, overflow="ellipsis")
        if title.startswith(theme.GHOST):
            text.append(theme.GHOST, style=theme.URGENT)
            title = title[len(theme.GHOST) :]
        text.append(title)
        sub_title = self.screen_sub_title
        if sub_title:
            text.append(" — ")
            text.append(sub_title, style=theme.MUTED)
        return text

    def __init__(self, timer: PacTimer) -> None:
        super().__init__(show_clock=True)
        self._timer = timer

    def compose(self) -> ComposeResult:
        yield HeaderIcon().data_bind(Header.icon)
        yield HeaderTitle()
        yield HeaderClock().data_bind(Header.time_format)
        yield self._timer

POLL_SECONDS = 1.0
SPIN_SECONDS = 0.12

DASHBOARD, SUMMARY, HISTORY = "dashboard", "summary", "history"


NAME = "Blinky"
"""After Pac-Man's red ghost — the one already at the end of the countdown."""


def tab_label(title: str, count: int | None = None) -> str:
    """A tab's name with its hotkey letter underlined, and what it holds.

    Textual markup rather than a ``rich.Text``: ``TabPane`` puts its title
    through ``Content.from_markup``, which takes a string.

    The count rides on the tab rather than in the window title because it is
    true of one tab and not of the others — a subtitle saying "6 open" while
    you were reading History was answering a question nobody had asked.
    """
    underlined = f"[u]{title[0]}[/u]{title[1:]}"
    return underlined if count is None else f"{underlined} ({count})"


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
    summariser: summarize.Runner | None = None


class Dashboard(App[None]):
    CSS_PATH = "styles.tcss"
    TITLE = NAME

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
        Binding("slash", "author_filter", "Filter author", key_display="/"),
        Binding("t", "cycle_window", "Date range"),
        Binding("e", "focus_repos", "Repos"),
        Binding("E", "toggle_repos", "Fold repos"),
        Binding("b", "backfill", "Fill history"),
        Binding("g", "describe", "Summarise"),
        Binding("l", "toggle_log", "Log"),
        Binding("r", "reload", "Refresh"),
        # Shown, not hidden: the footer is the only place the keys are listed
        # now, and a paging key nobody can find is a history nobody can read.
        Binding("left_square_bracket,pageup", "page_back", "Prev page",
                key_display="["),
        Binding("right_square_bracket,pagedown", "page_forward", "Next page",
                key_display="]"),
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
        "describe": HISTORY,
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
        yield RunHeader(PacTimer(self._progress, self._longest_running))
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
        """True while a filter control has focus, so letter keys stay letters.

        Covers the date picker as well as the author box: an open dropdown that
        let `q` quit underneath it would be a trap.
        """
        return isinstance(self.focused, Input) or self.picking

    @property
    def picking(self) -> bool:
        """Whether the date picker has the keyboard.

        Expanding a Select moves focus into an overlay rather than keeping it on
        the control, so asking what is focused is not enough — the question is
        whether focus is anywhere inside it.
        """
        focused = self.focused
        if focused is None:
            return False
        if isinstance(focused, Select):
            return True
        return any(isinstance(node, Select) for node in focused.ancestors)

    @property
    def asking(self) -> bool:
        return isinstance(self.screen, Ask)

    def summary_status(self) -> filling.SummaryStatus:
        runner = self.runtime.summariser
        if runner is None:
            return filling.SummaryStatus()
        return filling.SummaryStatus.from_status(runner.status())

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
        self._advance_summaries()

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
        self._retitle()

    def _reload_summary(self, now: float) -> None:
        self.summary.show(
            data.merges_this_run(self.runtime.store, self.session), now=now
        )

    @on(TabbedContent.TabActivated)
    def _refit_history(self) -> None:
        """Re-fit the page once History is actually on screen.

        A hidden tab has no height, so the page size worked out while the
        Dashboard was showing is the fallback rather than the real one. Without
        this the first view of History is a short page that grows a second later.
        """
        if self.tab == HISTORY:
            self.call_after_refresh(self._reload_history, time.time())

    def on_resize(self, _event: Any) -> None:
        """Re-fit the history page when the window changes shape.

        How many rows fit is how big a page is, so a resize changes the answer.
        The poll would catch it within a second; doing it here means the first
        frame after startup is already the right size rather than a short page
        that grows.
        """
        try:
            self._reload_history(time.time())
        except NoMatches:
            # Resize arrives before compose has finished on the first frame.
            pass

    def _history_rows(self) -> int:
        """How many rows the history table has room for right now.

        Zero while the widget is still being laid out, which is why there is a
        default to fall back to rather than a page of nothing.
        """
        height = self.history.table.size.height
        return (height - 1) if height > 1 else data.PAGE_SIZE

    def _reload_history(self, now: float) -> None:
        page = data.merge_history(
            self.runtime.store, self.session, now, self._history_rows()
        )
        self.session = self.session.with_page(page.number)
        self.history.show(
            HistoryContext(
                session=self.session,
                page=page,
                can_backfill=self.runtime.backfiller is not None,
                # A backfill takes the line when both are going: it is the one
                # with an end in sight worth watching.
                note=filling.progress_note(self.backfill_status(), self._frame)
                or filling.summary_note(self.summary_status(), self._frame),
            )
        )

    def _retitle(self) -> None:
        """Name the run and what it is scoped to, and count the open board.

        Both follow the sidebar, so switching repository renames the window and
        recounts the tab in one go.
        """
        self.title = f"{theme.GHOST} {NAME}: {self.session.scope_label}"
        self.sub_title = board_view.subtitle(self.pull_requests, self.session)
        label = tab_label("Dashboard", len(self.pull_requests))
        try:
            tab = self.query_one(TabbedContent).get_tab(DASHBOARD)
        except NoMatches:
            return
        # Only on a change: the poll runs every second and relabelling a tab
        # rebuilds it.
        if str(tab.label) != label:
            tab.label = label

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

    @on(Button.Pressed, ".action")
    def _action_button(self, event: Button.Pressed) -> None:
        """A button does exactly what its key does, and nothing of its own.

        One implementation per action, reachable two ways, so the two can never
        drift apart.
        """
        event.stop()
        if event.button.id == "action-open":
            self.action_open()
        elif event.button.id == "action-primary":
            self.action_describe()
        # Clicking a button takes focus with it, which would leave every letter
        # key inert until the reader worked out why.
        self.view.table.focus()

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
        if self._roll_page(delta):
            return
        table = self.view.table
        table.action_cursor_down() if delta > 0 else table.action_cursor_up()

    def _roll_page(self, delta: int) -> bool:
        """Carry the cursor onto the next page when it runs off this one.

        History is paged rather than scrolled — 1,594 rows should not all be
        held in memory to look at twenty. But paging that stops the cursor dead
        at the last row reads as "this is all there is", which is the wrong
        thing to tell someone with sixty-three more pages. So the obvious motion
        keeps working and the page turns underneath it.
        """
        if self.tab != HISTORY:
            return False
        page = self.history.page
        table = self.view.table
        row, rows = table.cursor_row, len(self.view.records)

        if delta > 0 and row >= rows - 1 and page.number + 1 < page.pages:
            self.action_page_forward()
            self.view.table.move_cursor(row=0)
            return True
        if delta < 0 and row <= 0 and page.number > 0:
            self.action_page_back()
            # Onto the last row of the previous page, so going back and forward
            # across a boundary lands where it started.
            self.view.table.move_cursor(row=max(0, len(self.view.records) - 1))
            return True
        return False

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
        """Hand the date range to the picker.

        It used to cycle blindly through four ranges, so finding the one you
        wanted meant pressing the key until it came round and reading a label
        elsewhere to know where you had landed. The list is now on screen and
        the key is how you reach it.
        """
        if self.typing or self.tab != HISTORY:
            return
        picker = self.history.dates
        picker.focus()
        # Open it too: one key should get you to the choices, not to a control
        # that then needs a second key to say what it offers.
        picker.expanded = True

    @on(Select.Changed, "#date_filter")
    def _apply_window(self, event: Select.Changed) -> None:
        if event.value is Select.BLANK or event.value == self.session.window:
            return
        self.session = self.session.with_window(int(event.value))
        self._reload_history(time.time())
        # Back to the table: picking a range is a thing you finish, and leaving
        # focus in the picker would leave every letter key inert.
        self.view.table.focus()

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
        if self.picking:
            self.view.table.focus()
        elif self.typing:
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

    # --------------------------------------------------------- summarising

    def action_describe(self) -> None:
        """Write a summary for the merge under the cursor.

        The one place a model call is bought by a keystroke rather than by a
        pull request changing, so it is deliberately one row at a time and never
        repeats work that is already done.
        """
        runner = self.runtime.summariser
        if self.typing or self.tab != HISTORY or self.asking:
            return
        if runner is None:
            self.notify("summarising needs the watch loop running", timeout=4)
            return

        record = self.view.current
        if record is None:
            return
        if getattr(record, "described_by_model", False):
            # Already paid for. Rewriting one is a rarer thing to want than
            # pressing g by accident on a row that already reads fine.
            self.notify(f"#{record.number} already has a summary", timeout=3)
            return

        if runner.request(record.repo, record.number):
            self.notify(f"summarising #{record.number}…", timeout=3)
        else:
            self.notify(f"#{record.number} is already queued", timeout=3)

    def _advance_summaries(self) -> None:
        """Notice when the summary thread has finished, from the poll timer."""
        runner = self.runtime.summariser
        if runner is None or self.asking:
            return
        status = self.summary_status()
        if not status.finished:
            return
        self.notify(
            status.message or "summarising finished",
            timeout=5,
            severity="error" if status.failed_outright else "information",
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
            *filling.summary_in_flight(self.summary_status()),
        )

    def _answer_quit(self, reply: str | None) -> None:
        if reply == screens.YES:
            self.runtime.stop.set()
            self.exit()


def run(runtime: Runtime) -> None:
    Dashboard(runtime).run()
    runtime.stop.set()
