"""The dashboard: three tabs, one poll, and the keys that move between them.

Dashboard is the live board: what is open right now and what wants a human.
Summary is what landed while you were watching, so automating the reviewing does
not mean losing track of what is going into the repository. History is the same
record without the time limit.

The next-scan line lives in the header and the log pane below all three. Both
describe the run rather than any one view, and a run-wide line wedged between a
tab and the log read as belonging to whichever pane was above it.

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

from .. import backfill, conversation, model, summarize
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
    DEFAULT_CSS = """
    RunHeader HeaderTitle {
        height: 100%;
        content-align: center middle;
    }
    """

    def format_title(self) -> Text:
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


def tab_label(title: str, count: int | None = None) -> str:
    underlined = f"[u]{title[0]}[/u]{title[1:]}"
    return underlined if count is None else f"{underlined} ({count})"


@dataclass(frozen=True, slots=True)
class Runtime:
    store: Store
    repos: tuple[str, ...]
    relay: LogRelay
    stop: threading.Event
    status: Callable[[], Mapping[str, Any]]
    started_at: float
    wake: threading.Event | None = None
    pause: threading.Event | None = None
    backfiller: backfill.Runner | None = None
    summariser: summarize.Runner | None = None
    conversations: conversation.Runner | None = None


class Dashboard(App[None]):
    CSS_PATH = "styles.tcss"
    TITLE = NAME

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("d", f"show_tab('{DASHBOARD}')", "Dashboard", show=False),
        Binding("s", f"show_tab('{SUMMARY}')", "Summary", show=False),
        Binding("h", f"show_tab('{HISTORY}')", "History", show=False),
        Binding("enter,o", "open", "Open in browser"),
        Binding("slash", "author_filter", "Filter author", key_display="/"),
        Binding("t", "cycle_window", "Date range"),
        Binding("e", "focus_repos", "Repos"),
        Binding("E", "toggle_repos", "Fold repos"),
        Binding("b", "backfill", "Fill history"),
        Binding("g", "describe", "Summarise"),
        Binding("c", "conversation", "Read the review"),
        Binding("x", "stop_review", "Stop review", show=False),
        Binding("l", "toggle_log", "Log"),
        Binding("r", "reload", "Scan now"),
        Binding("p", "pause", "Pause"),
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
    #
    # Advertising is half of it. A disabled binding is not dispatched at all,
    # so this is also what keeps a key meant for a live pull request from
    # reaching a merged one. Stopping a review is the case in point: only the
    # Dashboard's rows have a review to stop.
    TAB_ACTIONS = {
        "author_filter": HISTORY,
        "cycle_window": HISTORY,
        "page_back": HISTORY,
        "page_forward": HISTORY,
        "backfill": HISTORY,
        "describe": HISTORY,
        "stop_review": DASHBOARD,
    }

    SIDEBAR_ACTIONS = frozenset({"focus_repos", "toggle_repos"})

    def __init__(self, runtime: Runtime) -> None:
        super().__init__()
        self.runtime = runtime
        self.session = Session(repos=runtime.repos, started_at=runtime.started_at)
        self.pull_requests: tuple[PullRequest, ...] = ()
        self.repo_stats: tuple[RepoStats, ...] = ()
        self._frame = 0

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
                yield RichLog(id="log", markup=False, highlight=False, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(POLL_SECONDS, self.poll)
        self.set_interval(SPIN_SECONDS, self.spin)
        self.poll()
        self.board.table.focus()

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
        try:
            return self.query_one(RepoSidebar)
        except NoMatches:
            return None

    @property
    def tab(self) -> str:
        try:
            return self.query_one(TabbedContent).active
        except NoMatches:
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
        return isinstance(self.focused, Input) or self.picking

    @property
    def picking(self) -> bool:
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

    def poll(self) -> None:
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
        try:
            self._reload_history(time.time())
        except NoMatches:
            pass

    def _history_rows(self) -> int:
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
        self.title = f"{theme.GHOST} {NAME}: {self.session.scope_label}"
        self.sub_title = board_view.subtitle(self.pull_requests)
        label = tab_label("Dashboard", len(self.pull_requests))
        try:
            tab = self.query_one(TabbedContent).get_tab(DASHBOARD)
        except NoMatches:
            return
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
        if not self.is_running:
            return
        status = self.backfill_status()
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
        self.refresh_bindings()
        # Focus follows the tab, by key or by click. Textual re-activates
        # whichever pane holds the focused widget, so leaving focus behind on
        # the old tab's table drags the tab straight back with it.
        self.view.table.focus()

    def action_reload(self) -> None:
        self.reload()

        wake = self.runtime.wake
        if wake is None:
            self.notify("refreshed from the database", timeout=2)
            return

        status = self.runtime.status()
        phase = str(status.get("phase") or "")
        if phase != "waiting" and not status.get("paused"):
            self.notify(f"already running: {phase}", timeout=3)
            return

        wake.set()
        self.notify("scanning now…", timeout=2)

    def action_pause(self) -> None:
        if self.typing:
            return

        pause = self.runtime.pause
        if pause is None:
            self.notify("nothing to pause: no scan loop is running", timeout=3)
            return

        if pause.is_set():
            pause.clear()
            self.notify("scanning again", timeout=2)
        else:
            pause.set()
            self.notify("paused, press p to start again", timeout=3)
        self.refresh_bindings()

    def action_show_tab(self, tab: str) -> None:
        if not self.typing:
            self.query_one(TabbedContent).active = tab
            self.view_for(tab).table.focus()

    @on(Button.Pressed, ".action")
    def _action_button(self, event: Button.Pressed) -> None:
        event.stop()
        actions = {
            "open": self.action_open,
            "describe": self.action_describe,
            "conversation": self.action_conversation,
        }
        run = actions.get(self.view.action_bar.carried_by(event.button))
        if run is not None:
            run()
        self.view.table.focus()

    def action_open(self) -> None:
        record = self.view.current
        if record is not None and record.url and browser.open_url(record.url):
            self.notify(f"opened #{record.number} in your browser", timeout=2)

    def action_stop_review(self) -> None:
        """Stop the model call on the row under the cursor.

        The escape hatch for a review that has genuinely stopped moving, and the
        reason nothing else in this tool kills one automatically. A review is
        minutes of work and real quota, and from the outside a slow call and a
        stuck one look identical. So the board says what it can see (how long,
        how much of it asleep, how long since the model last spoke) and leaves
        the judgement to whoever is reading it.

        Nothing else is touched: the tick moves on to the next pull request, and
        the next pass will pick this one up again from the top.
        """
        record = self.view.current
        if self.typing or self.asking or record is None:
            return
        if not isinstance(record, PullRequest):
            return
        if record.activity is None:
            self.notify(f"#{record.number} is not being reviewed", timeout=3)
            return
        if model.cancel(record.key):
            self.notify(f"stopping the review of #{record.number}…", timeout=3)
        else:
            self.notify(f"#{record.number} had already finished", timeout=3)

    def action_toggle_log(self) -> None:
        if not self.typing:
            self.query_one("#log", RichLog).toggle_class("hidden")

    def action_cursor_down(self) -> None:
        self._move_cursor(1)

    def action_cursor_up(self) -> None:
        self._move_cursor(-1)

    def _move_cursor(self, delta: int) -> None:
        if self.typing:
            return
        if self._roll_page(delta):
            return
        table = self.view.table
        table.action_cursor_down() if delta > 0 else table.action_cursor_up()

    def _roll_page(self, delta: int) -> bool:
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
            self.view.table.move_cursor(row=max(0, len(self.view.records) - 1))
            return True
        return False

    def action_focus_repos(self) -> None:
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

    def action_cycle_window(self) -> None:
        if self.typing or self.tab != HISTORY:
            return
        picker = self.history.dates
        picker.focus()
        picker.expanded = True

    @on(Select.Changed, "#date_filter")
    def _apply_window(self, event: Select.Changed) -> None:
        if event.value is Select.BLANK or event.value == self.session.window:
            return
        self.session = self.session.with_window(int(event.value))
        self._reload_history(time.time())
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

    def action_backfill(self) -> None:
        runner = self.runtime.backfiller
        if self.typing or self.tab != HISTORY or self.asking:
            return
        if runner is None:
            self.notify("backfill needs ./blinky.sh --backfill here", timeout=4)
            return
        if runner.busy:
            # The same key stops it. The sweep ends between pages and keeps
            # everything it has already committed.
            runner.cancel()
            self.notify("stopping the backfill…", timeout=3)
            return
        self.push_screen(screens.range_question(), self._begin_backfill)

    def _begin_backfill(self, range_key: str | None) -> None:
        if range_key and self.runtime.backfiller is not None:
            self.runtime.backfiller.start(range_key)

    def _advance_backfill(self) -> None:
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

    def action_conversation(self) -> None:
        """Open what was actually said about the row under the cursor.

        Asked for rather than kept: everything already on record would show
        nothing under a scheme that only stored what it wrote from now on, and
        a thread the author resolved in the browser is only knowable by asking.
        """
        runner = self.runtime.conversations
        record = self.view.current
        if self.typing or self.asking or record is None:
            return
        if runner is None:
            self.notify("reading reviews needs the watch loop running", timeout=4)
            return
        if not runner.knows(record.repo):
            return
        self.push_screen(
            screens.ConversationScreen(runner, record.repo, record.number)
        )

    def action_describe(self) -> None:
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
            self.notify(f"#{record.number} already has a summary", timeout=3)
            return

        if runner.request(record.repo, record.number):
            self.notify(f"summarising #{record.number}…", timeout=3)
        else:
            self.notify(f"#{record.number} is already queued", timeout=3)

    def _advance_summaries(self) -> None:
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

    def action_quit(self) -> None:  # type: ignore[override]
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
