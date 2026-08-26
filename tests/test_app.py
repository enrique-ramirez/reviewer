"""Drives the real app headlessly, which is the only way to catch a layout,
stylesheet or Textual-API mistake that no pure test can see."""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any

from reviewer.state import Store
from reviewer.tui import Runtime
from reviewer.tui.app import Dashboard
from textual.widgets import TabbedContent

from reviewer.tui.logs import LogRelay
from reviewer.tui.views.sidebar import RepoRow
from reviewer.tui import theme
from reviewer.tui.app import RunHeader
from reviewer.tui.widgets import PacTimer
from reviewer.tui.views import board
from reviewer.tui.views.board import BoardView
from reviewer.tui.views.merges import HistoryView, SummaryView
from reviewer.tui.views.sidebar import RepoSidebar, SidebarHeader
from reviewer.tui.widgets import StatusBar

REPOS = ("acme/widgets", "acme/gadgets")


def _seed(store: Store, now: float) -> None:
    store.upsert_pr_view(
        {
            "repo": "acme/widgets",
            "pr_number": 12,
            "title": "Tidy the cache key",
            "author": "ada",
            "url": "https://example.invalid/12",
            "opened_at": now - 3 * 86400,
            "seen_at": now,
            "base_ref": "main",
            "head_sha": "0123456789abcdef",
            "additions": 40,
            "deletions": 12,
            "changed_files": 3,
            "labels": ["cleanup"],
            "reviews": [{"author": "grace", "state": "COMMENTED"}],
            "requested_reviewers": [],
            "needs_human": 1,
            "needs_human_reason": "clean, but wants a human",
            "last_action": "COMMENT",
            "mergeable": "MERGEABLE",
            "ci_state": "green",
            "open_threads": 2,
            "threads_awaiting_us": 1,
        }
    )
    store.upsert_pr_view(
        {
            "repo": "acme/gadgets",
            "pr_number": 5,
            "title": "Add the widget",
            "author": "me",
            "is_ours": 1,
            "url": "https://example.invalid/5",
            "opened_at": now - 3600,
            "seen_at": now,
            "base_ref": "main",
            "head_sha": "fedcba9876543210",
        }
    )
    store.record_review_event("acme/widgets", 12, head_sha="0123456789abcdef",
                              event="COMMENT", inline=3)
    store.begin_active("acme/gadgets", 5, "reviewing")
    store.record_merged(
        {
            "repo": "acme/widgets",
            "pr_number": 11,
            "title": "Ship the thing",
            "author": "grace",
            "url": "https://example.invalid/11",
            "base_ref": "main",
            "labels": ["feature"],
            "opened_at": now - 5 * 86400,
            "merged_at": now - 86400,
            "merged_by": "grace",
            "recorded_at": now,
            "additions": 9,
            "deletions": 2,
            "changed_files": 1,
            "our_reviews": 1,
            "our_comments": 3,
            "description": "Caches the widget lookup.",
            "description_source": "model",
        }
    )


class DashboardSmoke(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.now = time.time()
        self.store = Store(Path(self._tmp.name))
        _seed(self.store, self.now)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def _app(self, summariser: Any = None) -> Dashboard:
        status: dict[str, Any] = {
            "phase": "waiting",
            "remaining": 300.0,
            "total": 900.0,
        }
        return Dashboard(
            Runtime(
                store=self.store,
                repos=REPOS,
                relay=LogRelay(),
                stop=threading.Event(),
                status=lambda: dict(status),
                started_at=self.now - 60,
                summariser=summariser,
            )
        )

    async def test_every_tab_renders_its_rows(self) -> None:
        app = self._app()
        async with app.run_test() as pilot:
            self.assertEqual(len(app.pull_requests), 2)
            self.assertEqual(app.board.table.row_count, 2)
            self.assertIn("need you", app.sub_title)

            await pilot.press("s")
            self.assertEqual(app.tab, "summary")
            self.assertEqual(app.summary.table.row_count, 1)
            self.assertIn("Caches the widget lookup", app.summary.detail_text(
                app.summary.current).plain)

            await pilot.press("h")
            self.assertEqual(app.tab, "history")
            self.assertEqual(app.history.table.row_count, 1)

    async def test_the_attention_filter_and_the_repository_scope_hold_together(
        self,
    ) -> None:
        app = self._app()
        async with app.run_test() as pilot:
            await pilot.press("a")
            self.assertTrue(app.session.only_attention)
            self.assertEqual([pr.number for pr in app.pull_requests], [12])

            await pilot.press("a")
            await pilot.press("e", "down")
            self.assertTrue(app.sidebar.has_focus)
            self.assertEqual(app.session.scope, ("acme/widgets",))
            self.assertEqual([pr.repo for pr in app.pull_requests], ["acme/widgets"])

            await pilot.press("escape")
            self.assertFalse(app.sidebar.has_focus)
            self.assertTrue(app.board.table.has_focus)

    async def test_history_filters_narrow_and_clear(self) -> None:
        app = self._app()
        async with app.run_test() as pilot:
            await pilot.press("h")
            await pilot.pause()
            app.history.dates.value = 1
            await pilot.pause()
            self.assertEqual(app.session.window, 1)

            await pilot.press("slash")
            self.assertTrue(app.typing)
            await pilot.press("z", "z", "enter")
            self.assertEqual(app.session.author, "zz")
            self.assertEqual(app.history.table.row_count, 0)
            self.assertIn("Nothing matches", app.history.empty_text().plain)

            await pilot.press("escape")
            self.assertFalse(app.session.filtered)
            self.assertEqual(app.history.table.row_count, 1)

    async def test_keys_are_offered_only_where_they_do_something(self) -> None:
        app = self._app()
        async with app.run_test():
            self.assertTrue(app.check_action("toggle_filter", ()))
            self.assertFalse(app.check_action("backfill", ()))
            self.assertFalse(app.check_action("describe", ()))
            self.assertTrue(app.check_action("focus_repos", ()))
            self.assertTrue(app.check_action("quit", ()))

    async def test_the_spinner_advances_without_rebuilding_the_table(self) -> None:
        app = self._app()
        async with app.run_test():
            live = next(
                index
                for index, pr in enumerate(app.pull_requests)
                if pr.activity is not None
            )
            before = app.board.table.get_row_at(live)
            app.spin()
            app.spin()
            after = app.board.table.get_row_at(live)
            self.assertNotEqual(after[2].plain, before[2].plain)
            self.assertEqual(after[6].plain, before[6].plain)
            self.assertEqual(app.board.table.row_count, 2)

    async def test_quitting_asks_first_and_names_the_work_in_flight(self) -> None:
        app = self._app()
        async with app.run_test() as pilot:
            await pilot.press("q")
            self.assertTrue(app.asking)
            self.assertIn("reviewing acme/gadgets#5", app.screen.body.plain)

            await pilot.press("n")
            self.assertFalse(app.asking)
            self.assertFalse(app.runtime.stop.is_set())

            await pilot.press("q", "y")
            self.assertTrue(app.runtime.stop.is_set())

    async def test_clicking_a_repository_scopes_every_tab_to_it(self) -> None:
        app = self._app()
        async with app.run_test() as pilot:
            rows = app.sidebar.rows
            self.assertEqual(len(rows), 3)

            await pilot.click(rows[2])
            self.assertEqual(app.session.scope, ("acme/gadgets",))
            self.assertEqual([pr.repo for pr in app.pull_requests], ["acme/gadgets"])
            self.assertTrue(app.sidebar.has_focus)

            await pilot.click(rows[0])
            self.assertEqual(app.session.scope, REPOS)

    async def test_the_sidebar_folds_to_a_rail_rather_than_vanishing(self) -> None:
        app = self._app()
        async with app.run_test() as pilot:
            sidebar = app.sidebar
            self.assertFalse(sidebar.collapsed)

            await pilot.press("E")
            self.assertTrue(sidebar.collapsed)
            # Still one row per repository, so "there are others" survives.
            self.assertEqual(len(sidebar.rows), 3)
            self.assertIn(RepoSidebar, [type(w) for w in app.query(RepoSidebar)])

            # Folded, a row is still a row: clicking a ghost scopes to it.
            await pilot.click(sidebar.rows[2])
            self.assertEqual(app.session.scope, ("acme/gadgets",))

            await pilot.click(app.query_one(SidebarHeader))
            self.assertFalse(sidebar.collapsed)

    async def test_folding_hands_focus_back_and_e_unfolds(self) -> None:
        app = self._app()
        async with app.run_test() as pilot:
            await pilot.press("e")
            self.assertTrue(app.sidebar.has_focus)

            await pilot.press("E")
            self.assertTrue(app.sidebar.collapsed)
            self.assertTrue(app.board.table.has_focus)

            await pilot.press("e")
            self.assertFalse(app.sidebar.collapsed)
            self.assertTrue(app.sidebar.has_focus)

    async def test_the_sidebar_reports_each_repositorys_state(self) -> None:
        app = self._app()
        async with app.run_test():
            widgets = next(s for s in app.repo_stats if s.label == "widgets")
            gadgets = next(s for s in app.repo_stats if s.label == "gadgets")
            self.assertEqual(widgets.subtitle, "acme")
            self.assertEqual((widgets.open_count, widgets.waiting), (1, 1))
            self.assertTrue(gadgets.busy)

    async def test_focus_follows_the_tab_so_the_arrows_land_somewhere(self) -> None:
        app = self._app()
        async with app.run_test() as pilot:
            await pilot.press("s")
            self.assertTrue(app.summary.table.has_focus)
            await pilot.press("h")
            self.assertTrue(app.history.table.has_focus)
            await pilot.press("d")
            self.assertTrue(app.board.table.has_focus)

    async def test_the_board_and_the_detail_pane_agree_about_a_skip(self) -> None:
        # A pull request we reviewed, skipped on this pass because nothing
        # moved: the row used to say "not reviewed" over a detail pane saying
        # "skipped: nothing changed since last review".
        self.store.set_pr_action(
            "acme/widgets", 12, "skipped: nothing changed since last review"
        )
        app = self._app()
        async with app.run_test():
            reviewed = next(pr for pr in app.pull_requests if pr.number == 12)
            self.assertTrue(reviewed.reviewed_by_us)

            row = board.row_cells(reviewed, time.time(), 0)
            self.assertEqual(row[2].plain, "reviewed")

            detail = app.board.detail_text(reviewed).plain
            self.assertIn("status     reviewed", detail)
            self.assertIn("skipped: nothing changed since last review", detail)

    async def test_a_pull_request_we_never_reviewed_still_says_so(self) -> None:
        self.store.upsert_pr_view(
            {
                "repo": "acme/widgets",
                "pr_number": 20,
                "title": "Waiting on the build",
                "author": "ada",
                "seen_at": self.now,
                "opened_at": self.now - 600,
                "base_ref": "main",
                "last_action": "skipped: CI still running: build",
            }
        )
        app = self._app()
        async with app.run_test():
            untouched = next(pr for pr in app.pull_requests if pr.number == 20)
            self.assertFalse(untouched.reviewed_by_us)
            self.assertEqual(
                board.row_cells(untouched, time.time(), 0)[2].plain, "not reviewed"
            )

    async def test_clicking_a_board_row_moves_the_cursor_to_it(self) -> None:
        app = self._app()
        async with app.run_test() as pilot:
            first = app.board.current
            await pilot.click(app.board.table, offset=(4, 2))
            moved = app.board.current
            self.assertIsNot(moved, first)
            # The detail pane follows the click, not just the keyboard.
            self.assertIn(f"#{moved.number}", app.board.detail_text(moved).plain)

    async def test_one_repository_needs_no_sidebar(self) -> None:
        app = Dashboard(
            Runtime(
                store=self.store,
                repos=("acme/widgets",),
                relay=LogRelay(),
                stop=threading.Event(),
                status=dict,
                started_at=self.now,
            )
        )
        async with app.run_test():
            self.assertIsNone(app.sidebar)
            self.assertFalse(app.check_action("focus_repos", ()))
            self.assertFalse(app.check_action("toggle_repos", ()))

    async def test_the_status_bars_say_what_each_tab_is_showing(self) -> None:
        app = self._app()
        async with app.run_test():
            # Two per tab: the counts on the left and the page count centred.
            self.assertEqual(len(app.query("StatusBar.counts")), 3)
            self.assertEqual(len(app.query("StatusBar.pager")), 3)
            for view in (BoardView, SummaryView, HistoryView):
                self.assertEqual(len(app.query(view)), 1)
            self.assertIn("merged", app.history.status_text().plain)
            self.assertIn("this run", app.summary.status_text().plain)
            self.assertIn("needs your approval", app.board.status_text().plain)


if __name__ == "__main__":
    unittest.main()


class Summarising(unittest.IsolatedAsyncioTestCase):
    """Pressing g on a merge in the History tab."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.now = time.time()
        self.store = Store(Path(self._tmp.name))
        _seed(self.store, self.now)
        # A backfilled row, carrying the author's title rather than a summary.
        # Newest, so it is the one under the cursor when History opens.
        self.store.record_merged(
            {
                "repo": "acme/widgets",
                "pr_number": 13,
                "title": "fix: stop the retry storm",
                "author": "ada",
                "url": "https://example.invalid/13",
                "merged_at": self.now - 60,
                "recorded_at": self.now,
                "description": "fix: stop the retry storm",
                "description_source": "title",
                "description_tries": 99,
                "backfilled": 1,
            }
        )

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def _app(self, summariser: Any) -> Dashboard:
        return Dashboard(
            Runtime(
                store=self.store,
                repos=REPOS,
                relay=LogRelay(),
                stop=threading.Event(),
                status=dict,
                started_at=self.now - 60,
                summariser=summariser,
            )
        )

    async def _press(self, runner: Any, *keys: str) -> Dashboard:
        app = self._app(runner)
        async with app.run_test() as pilot:
            await pilot.press(*keys)
            await pilot.pause()
        return app

    async def test_the_key_asks_for_the_row_under_the_cursor(self) -> None:
        runner = FakeSummariser()
        app = self._app(runner)
        async with app.run_test() as pilot:
            await pilot.press("h")
            await pilot.pause()
            record = app.view.current
            self.assertEqual(record.number, 13, "expected the backfilled row first")
            await pilot.press("g")
            await pilot.pause()
        self.assertEqual(runner.asked, [("acme/widgets", 13)])

    async def test_a_row_we_already_summarised_is_not_bought_again(self) -> None:
        runner = FakeSummariser()
        app = self._app(runner)
        async with app.run_test() as pilot:
            await pilot.press("h", "down")
            await pilot.pause()
            self.assertTrue(app.view.current.described_by_model)
            await pilot.press("g")
            await pilot.pause()
        self.assertEqual(runner.asked, [])

    async def test_pressing_it_twice_asks_once(self) -> None:
        # The runner refuses duplicates too, but the interface should not be
        # relying on that to avoid paying twice for one keystroke repeated.
        runner = FakeSummariser(accept_once=True)
        await self._press(runner, "h", "g", "g")
        self.assertEqual(runner.asked.count(("acme/widgets", 13)), 2)
        self.assertEqual(runner.accepted, 1)

    async def test_the_key_does_nothing_on_the_other_tabs(self) -> None:
        runner = FakeSummariser()
        await self._press(runner, "d", "g")
        self.assertEqual(runner.asked, [])

    async def test_without_a_runner_nothing_breaks(self) -> None:
        # --lean and one-shot runs have no summariser wired.
        app = self._app(None)
        async with app.run_test() as pilot:
            await pilot.press("h", "g")
            await pilot.pause()


class FakeSummariser:
    """Enough of summarize.Runner for the interface to talk to."""

    busy = False

    def __init__(self, accept_once: bool = False) -> None:
        self.asked: list[tuple[str, int]] = []
        self.accepted = 0
        self._accept_once = accept_once

    def request(self, repo: str, number: int) -> bool:
        self.asked.append((repo, number))
        if self._accept_once and self.accepted:
            return False
        self.accepted += 1
        return True

    def status(self) -> dict[str, Any]:
        return {"phase": "idle"}

    def dismiss(self) -> None:
        pass

    def cancel(self) -> None:
        pass


class Paging(unittest.IsolatedAsyncioTestCase):
    """Moving through a history too long to hold on one screen.

    The bug behind these: the cursor stopped dead at the last row of a page,
    which reads as "that is all there is" to someone with sixty-three more
    pages, and the only way onward was a key hidden from the footer.
    """

    ROWS = 100

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.now = time.time()
        self.store = Store(Path(self._tmp.name))
        for i in range(self.ROWS):
            self.store.record_merged(
                {
                    "repo": "acme/widgets",
                    "pr_number": 1900 - i,
                    "title": f"PR {1900 - i}",
                    "author": "ada",
                    "url": "https://example.invalid",
                    "merged_at": self.now - i * 3600,
                    "recorded_at": self.now,
                }
            )

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def _app(self) -> Dashboard:
        return Dashboard(
            Runtime(
                store=self.store,
                repos=("acme/widgets",),
                relay=LogRelay(),
                stop=threading.Event(),
                status=dict,
                started_at=self.now,
            )
        )

    async def _history(self, pilot, app) -> None:
        await pilot.press("h")
        # Twice: the page is re-fitted once the tab has a height to measure.
        await pilot.pause()
        await pilot.pause()

    async def test_a_page_fills_the_window_it_is_given(self) -> None:
        seen = {}
        for height in (50, 30):
            app = self._app()
            async with app.run_test(size=(120, height)) as pilot:
                await self._history(pilot, app)
                seen[height] = app.history.page.size
                self.assertEqual(len(app.view.records), seen[height])
        self.assertGreater(
            seen[50], seen[30], "a taller window should hold more history"
        )

    async def test_the_cursor_carries_on_to_the_next_page(self) -> None:
        app = self._app()
        async with app.run_test(size=(120, 50)) as pilot:
            await self._history(pilot, app)
            size = app.history.page.size
            first = app.view.current.number
            for _ in range(size):
                await pilot.press("j")
            await pilot.pause()
            self.assertEqual(app.session.page, 1)
            self.assertEqual(app.view.current.number, first - size)

    async def test_and_back_again_lands_where_it_started(self) -> None:
        app = self._app()
        async with app.run_test(size=(120, 50)) as pilot:
            await self._history(pilot, app)
            size = app.history.page.size
            for _ in range(size):
                await pilot.press("j")
            await pilot.pause()
            await pilot.press("k")
            await pilot.pause()
            self.assertEqual(app.session.page, 0)
            self.assertEqual(app.view.current.number, 1900 - (size - 1))

    async def test_it_stops_at_the_end_of_the_history(self) -> None:
        app = self._app()
        async with app.run_test(size=(120, 50)) as pilot:
            await self._history(pilot, app)
            last = app.history.page.pages - 1
            for _ in range(self.ROWS + 20):
                await pilot.press("j")
            await pilot.pause()
            self.assertEqual(app.session.page, last)

    async def test_page_keys_that_exist_on_every_keyboard(self) -> None:
        # `[` and `]` need a modifier on several non-US layouts, so they cannot
        # be the only way through.
        app = self._app()
        async with app.run_test(size=(120, 50)) as pilot:
            await self._history(pilot, app)
            await pilot.press("pagedown")
            await pilot.pause()
            self.assertEqual(app.session.page, 1)
            await pilot.press("pageup")
            await pilot.pause()
            self.assertEqual(app.session.page, 0)

    async def test_the_dashboard_cursor_is_left_alone(self) -> None:
        # Rolling is a History idea; the board is one unpaged list.
        app = self._app()
        async with app.run_test(size=(120, 50)) as pilot:
            await pilot.press("d")
            for _ in range(10):
                await pilot.press("j")
            await pilot.pause()
            self.assertEqual(app.session.page, 0)


class DatePicker(unittest.IsolatedAsyncioTestCase):
    """The date range, which used to be a blind four-way cycle on `t`."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.now = time.time()
        self.store = Store(Path(self._tmp.name))
        for i in range(40):
            self.store.record_merged(
                {
                    "repo": "acme/widgets",
                    "pr_number": 100 - i,
                    "title": f"PR {100 - i}",
                    "author": "ada",
                    "url": "https://example.invalid",
                    # Spread across months, so a narrow range really is narrower.
                    "merged_at": self.now - i * 4 * 86400,
                    "recorded_at": self.now,
                }
            )

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def _app(self) -> Dashboard:
        return Dashboard(
            Runtime(
                store=self.store,
                repos=("acme/widgets",),
                relay=LogRelay(),
                stop=threading.Event(),
                status=dict,
                started_at=self.now,
            )
        )

    async def _open(self, pilot) -> None:
        await pilot.press("h")
        await pilot.pause()
        await pilot.pause()

    async def test_t_opens_the_list_rather_than_guessing_for_you(self) -> None:
        app = self._app()
        async with app.run_test(size=(120, 40)) as pilot:
            await self._open(pilot)
            await pilot.press("t")
            await pilot.pause()
            self.assertTrue(app.history.dates.expanded)

    async def test_choosing_a_range_narrows_the_history(self) -> None:
        app = self._app()
        async with app.run_test(size=(120, 40)) as pilot:
            await self._open(pilot)
            everything = app.history.table.row_count
            await pilot.press("t", "down", "enter")
            await pilot.pause()
            await pilot.pause()
            self.assertEqual(app.session.window_label, "last 7 days")
            self.assertLess(app.history.table.row_count, everything)

    async def test_it_hands_the_keyboard_back_when_you_are_done(self) -> None:
        # Leaving focus in the picker would leave every letter key inert.
        app = self._app()
        async with app.run_test(size=(120, 40)) as pilot:
            await self._open(pilot)
            await pilot.press("t", "down", "enter")
            await pilot.pause()
            await pilot.pause()
            self.assertTrue(app.history.table.has_focus)

    async def test_letter_keys_do_not_leak_through_an_open_list(self) -> None:
        # `q` quitting from under an open dropdown would be a trap.
        app = self._app()
        async with app.run_test(size=(120, 40)) as pilot:
            await self._open(pilot)
            await pilot.press("t")
            await pilot.pause()
            self.assertTrue(app.typing)
            before = app.session.window
            await pilot.press("g", "b", "r")
            await pilot.pause()
            self.assertEqual(app.session.window, before)
            self.assertTrue(app.history.dates.expanded)

    async def test_escape_closes_the_list_then_leaves_the_control(self) -> None:
        app = self._app()
        async with app.run_test(size=(120, 40)) as pilot:
            await self._open(pilot)
            await pilot.press("t")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            self.assertFalse(app.history.dates.expanded)
            await pilot.press("escape")
            await pilot.pause()
            self.assertTrue(app.history.table.has_focus)

    async def test_clearing_the_filters_moves_the_picker_back(self) -> None:
        # The control shows the range as well as setting it, so it has to
        # follow a change made from anywhere else.
        app = self._app()
        async with app.run_test(size=(120, 40)) as pilot:
            await self._open(pilot)
            await pilot.press("t", "down", "enter")
            await pilot.pause()
            await pilot.pause()
            self.assertEqual(app.history.dates.value, 1)
            await pilot.press("escape")
            await pilot.pause()
            await pilot.pause()
            self.assertFalse(app.session.filtered)
            self.assertEqual(app.history.dates.value, 0)


class Footer(unittest.IsolatedAsyncioTestCase):
    """The one place the keys are listed, now the dim hint line is gone."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.now = time.time()
        self.store = Store(Path(self._tmp.name))
        _seed(self.store, self.now)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def _shown(self, app: Dashboard) -> dict[str, str]:
        return {
            value.binding.key_display or value.binding.key: value.binding.description
            for value in app.active_bindings.values()
            if value.binding.show
        }

    async def test_the_page_keys_are_offered_on_history(self) -> None:
        app = Dashboard(
            Runtime(
                store=self.store,
                repos=REPOS,
                relay=LogRelay(),
                stop=threading.Event(),
                status=dict,
                started_at=self.now,
            )
        )
        async with app.run_test(size=(200, 40)) as pilot:
            await pilot.press("h")
            await pilot.pause()
            shown = self._shown(app)
            self.assertIn("[", shown)
            self.assertIn("]", shown)
            self.assertIn("t", shown)
            # And read as symbols rather than as key names.
            self.assertIn("/", shown)
            self.assertNotIn("slash", shown)

            await pilot.press("d")
            await pilot.pause()
            board = self._shown(app)
            self.assertNotIn("[", board, "paging means nothing on the board")
            self.assertNotIn("t", board)


class Layout(unittest.IsolatedAsyncioTestCase):
    """Where things sit, which is the part a passing test suite kept missing."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.now = time.time()
        self.store = Store(Path(self._tmp.name))
        _seed(self.store, self.now)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def _app(self, status: dict[str, Any] | None = None) -> Dashboard:
        state = status or {"phase": "waiting", "remaining": 664.0, "total": 900.0}
        return Dashboard(
            Runtime(
                store=self.store,
                repos=REPOS,
                relay=LogRelay(),
                stop=threading.Event(),
                status=lambda: dict(state),
                started_at=self.now,
            )
        )

    async def test_the_run_status_sits_in_the_header_beside_the_clock(self) -> None:
        app = self._app()
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.pause()
            pac = app.query_one(PacTimer).region
            clock = app.query_one("HeaderClock").region
            self.assertEqual(pac.y, 0, "it belongs to the run, not to a tab")
            self.assertLess(pac.x, clock.x, "the clock keeps the corner")

    async def test_it_never_runs_under_the_clock(self) -> None:
        # Two widgets docked right both anchor to the edge rather than
        # stacking, so the clock's width is reserved by hand. If that reservation
        # ever drifts, the overlap is silent.
        for width in (80, 100, 140, 200):
            for status in (
                {"phase": "waiting", "remaining": 664.0, "total": 900.0},
                {"phase": "scanning 4/6 — #1792", "remaining": None, "total": None},
            ):
                with self.subTest(width=width, phase=status["phase"]):
                    app = self._app(status)
                    async with app.run_test(size=(width, 30)) as pilot:
                        await pilot.pause()
                        pac = app.query_one(PacTimer).region
                        clock = app.query_one("HeaderClock").region
                        self.assertLessEqual(pac.right, clock.x)

    async def test_the_header_stays_aligned_when_it_is_clicked_taller(self) -> None:
        """Clicking the header makes it three rows.

        The icon and the clock are docked and fill that height, centring
        themselves in it. The title and the countdown are not, so they stayed a
        single row pinned to the top while everything around them moved down.
        """
        app = self._app()
        async with app.run_test(size=(150, 30)) as pilot:
            await pilot.pause()
            header = app.query_one(RunHeader)
            for tall in (False, True, False):
                with self.subTest(tall=tall):
                    header.tall = tall
                    await pilot.pause()
                    await pilot.pause()
                    spans = {
                        name: (r.y, r.y + r.height)
                        for name, r in (
                            (n, app.query_one(n).region)
                            for n in ("HeaderIcon", "HeaderTitle", "HeaderClock")
                        )
                    }
                    pac = app.query_one(PacTimer).region
                    spans["PacTimer"] = (pac.y, pac.y + pac.height)
                    self.assertEqual(
                        len(set(spans.values())), 1, f"not all on the same rows: {spans}"
                    )

    async def test_the_status_line_belongs_to_the_list_not_the_screen(self) -> None:
        app = self._app()
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.press("h")
            await pilot.pause()
            await pilot.pause()
            listing = app.history.query_one(".listing").region
            details = app.history.query_one(".details").region
            bar = app.history.query_one(".underbar").region
            self.assertEqual(bar.x, listing.x)
            self.assertLessEqual(bar.right, details.x, "it must not run under the detail")
            self.assertEqual(bar.height, 1)

    async def test_the_filters_sit_at_the_far_end_of_that_line(self) -> None:
        app = self._app()
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.press("h")
            await pilot.pause()
            await pilot.pause()
            bar = app.history.query_one(".underbar").region
            status = app.history.status_bar.region
            filters = app.history.query_one("#history_filters").region
            self.assertEqual(status.x, bar.x, "counts at the left edge")
            self.assertEqual(filters.right, bar.right, "controls at the right edge")
            self.assertEqual(status.y, filters.y, "on one line, not two")


class Sidebar(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.now = time.time()
        self.store = Store(Path(self._tmp.name))
        _seed(self.store, self.now)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def _app(self) -> Dashboard:
        return Dashboard(
            Runtime(
                store=self.store,
                repos=REPOS,
                relay=LogRelay(),
                stop=threading.Event(),
                status=dict,
                started_at=self.now,
            )
        )

    async def test_the_title_is_one_line_with_the_arrow_in_front(self) -> None:
        app = self._app()
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.pause()
            header = app.query_one("SidebarHeader")
            text = header.render().plain
            self.assertTrue(text.strip().endswith("REPOSITORIES"))
            self.assertNotIn("hide", text, "the arrow already says what it does")
            self.assertEqual(header.region.height, 1)

    async def test_collapsed_the_arrow_lines_up_with_the_rail(self) -> None:
        app = self._app()
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.pause()
            await pilot.press("E")
            await pilot.pause()
            await pilot.pause()
            header = app.query_one("SidebarHeader")
            row = app.query(RepoRow)[0]

            def glyph_column(widget) -> int:
                """The terminal column the widget's last glyph lands in.

                The last one, not the first: a chosen rail row leads with a
                cursor bar, so measuring from the left would compare the bar
                against the arrow instead of the ghost against the arrow.
                """
                text = widget.render().plain.rstrip()
                index = max(0, len(text) - 1)
                if widget.styles.content_align_horizontal == "center":
                    index += (widget.region.width - len(text)) // 2
                return widget.region.x + index

            self.assertEqual(glyph_column(header), glyph_column(row))
            # And the same for a row that is not the selected one, which is
            # drawn without the leading cursor bar.
            self.assertEqual(glyph_column(header), glyph_column(app.query(RepoRow)[1]))


class UnderBar(unittest.IsolatedAsyncioTestCase):
    """The line under the list: counts left, page centred, filters right."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.now = time.time()
        self.store = Store(Path(self._tmp.name))
        for i in range(60):
            self.store.record_merged(
                {
                    "repo": "acme/widgets",
                    "pr_number": 900 - i,
                    "title": "Ship it",
                    "author": "ada",
                    "url": "https://example.invalid",
                    "merged_at": self.now - i * 3600,
                    "recorded_at": self.now,
                }
            )

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def _app(self) -> Dashboard:
        return Dashboard(
            Runtime(
                store=self.store,
                repos=REPOS,
                relay=LogRelay(),
                stop=threading.Event(),
                status=dict,
                started_at=self.now,
            )
        )

    async def _history(self, pilot) -> None:
        await pilot.press("h")
        await pilot.pause()
        await pilot.pause()

    async def test_the_page_count_sits_on_the_centre_of_the_bar(self) -> None:
        app = self._app()
        async with app.run_test(size=(140, 32)) as pilot:
            await self._history(pilot)
            bar = app.history.query_one(".underbar").region
            pager = app.history.pager_bar.region
            self.assertEqual(
                (pager.x + pager.right) // 2, (bar.x + bar.right) // 2
            )
            self.assertIn("page", app.history.pager_text().plain)

    async def test_it_is_the_only_place_the_page_count_appears(self) -> None:
        app = self._app()
        async with app.run_test(size=(140, 32)) as pilot:
            await self._history(pilot)
            self.assertNotIn("page", app.history.status_text().plain)

    async def test_the_scope_is_left_to_the_sidebar(self) -> None:
        # It is shown there, highlighted. Repeating it made the busiest line on
        # the screen carry the least new information.
        app = self._app()
        async with app.run_test(size=(140, 32)) as pilot:
            await self._history(pilot)
            counts = app.history.status_text().plain
            self.assertIn("merged", counts)
            self.assertNotIn("all repos", counts)

    async def test_the_filters_stay_inside_the_bar_at_any_width(self) -> None:
        # Equal thirds is what centres the page count, but a third of a narrow
        # column cannot hold a date control; the floor must win over the centring.
        for width in (100, 120, 140, 200):
            with self.subTest(width=width):
                app = self._app()
                async with app.run_test(size=(width, 32)) as pilot:
                    await self._history(pilot)
                    bar = app.history.query_one(".underbar").region
                    self.assertLessEqual(app.history.dates.region.right, bar.right)

    async def test_a_single_page_says_nothing(self) -> None:
        # Tall enough to hold every row, so there is no page to be on.
        app = self._app()
        async with app.run_test(size=(140, 90)) as pilot:
            await self._history(pilot)
            self.assertEqual(app.history.page.pages, 1)
            self.assertEqual(app.history.pager_text().plain, "")

    async def test_the_board_has_no_page_count_to_show(self) -> None:
        app = self._app()
        async with app.run_test(size=(140, 32)) as pilot:
            await pilot.pause()
            self.assertEqual(app.board.pager_text().plain, "")


class HeaderSpacing(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.now = time.time()
        self.store = Store(Path(self._tmp.name))
        _seed(self.store, self.now)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    async def test_the_countdown_is_not_flush_against_the_clock(self) -> None:
        app = Dashboard(
            Runtime(
                store=self.store,
                repos=REPOS,
                relay=LogRelay(),
                stop=threading.Event(),
                status=lambda: {"phase": "waiting", "remaining": 664.0, "total": 900.0},
                started_at=self.now,
            )
        )
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.pause()
            pac = app.query_one(PacTimer).region
            clock = app.query_one("HeaderClock").region
            self.assertGreaterEqual(clock.x - pac.right, 2, "they read as one string")

    async def test_the_sidebar_title_has_room_to_breathe(self) -> None:
        app = Dashboard(
            Runtime(
                store=self.store,
                repos=REPOS,
                relay=LogRelay(),
                stop=threading.Event(),
                status=dict,
                started_at=self.now,
            )
        )
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.pause()
            header = app.query_one("SidebarHeader").region
            first = app.query(RepoRow)[0].region
            self.assertGreaterEqual(first.y - (header.y + header.height), 1)


class Naming(unittest.IsolatedAsyncioTestCase):
    """The title, and the count that moved onto the tab it is true of."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.now = time.time()
        self.store = Store(Path(self._tmp.name))
        _seed(self.store, self.now)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def _app(self, repos: tuple[str, ...] = REPOS) -> Dashboard:
        return Dashboard(
            Runtime(
                store=self.store,
                repos=repos,
                relay=LogRelay(),
                stop=threading.Event(),
                status=dict,
                started_at=self.now,
            )
        )

    async def test_the_title_names_the_run_and_its_scope(self) -> None:
        app = self._app()
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.pause()
            self.assertEqual(app.title, "ᗣ Blinky: All repositories")

    async def test_the_ghost_wears_the_red_it_wears_in_the_countdown(self) -> None:
        app = self._app()
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.pause()
            text = app.query_one(RunHeader).format_title()
            self.assertTrue(text.plain.startswith(theme.GHOST))
            ghost = next(s for s in text.spans if s.start == 0)
            self.assertEqual(str(ghost.style), theme.URGENT)
            self.assertEqual(ghost.end, len(theme.GHOST), "only the ghost is red")

    async def test_the_title_itself_stays_a_plain_string(self) -> None:
        # The terminal's own tab title reads app.title; markup would show up
        # there literally.
        app = self._app()
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.pause()
            self.assertNotIn("[", app.title)
            self.assertEqual(app.title, f"{theme.GHOST} Blinky: All repositories")

    async def test_it_follows_the_sidebar(self) -> None:
        app = self._app()
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.pause()
            app.session = app.session.with_repo(1)
            app.reload()
            await pilot.pause()
            # The owner is dropped: every repo in a run tends to share it, and
            # the sidebar spells it out anyway.
            self.assertEqual(app.title, "ᗣ Blinky: widgets")

    async def test_one_repository_is_named_rather_than_called_all_of_them(self) -> None:
        app = self._app(repos=("acme/widgets",))
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.pause()
            self.assertEqual(app.title, "ᗣ Blinky: widgets")

    async def test_the_open_count_rides_on_the_dashboard_tab(self) -> None:
        app = self._app()
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.pause()
            tabs = app.query_one(TabbedContent)
            self.assertEqual(str(tabs.get_tab("dashboard").label), "Dashboard (2)")
            # And nowhere else: a title saying "2 open" while you read History
            # answers a question nobody asked.
            self.assertNotIn("open", app.title)
            self.assertNotIn("open", app.sub_title)

    async def test_the_other_tabs_are_left_unnumbered(self) -> None:
        app = self._app()
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.pause()
            tabs = app.query_one(TabbedContent)
            self.assertEqual(str(tabs.get_tab("summary").label), "Summary")
            self.assertEqual(str(tabs.get_tab("history").label), "History")


class Renamed(unittest.TestCase):
    """A rename must not orphan what the old name wrote."""

    def test_comments_already_on_github_are_still_recognised(self) -> None:
        from reviewer import render

        self.assertTrue(render.is_ours(render.marker("abc123def456")))
        self.assertTrue(render.is_ours("<!-- pr-reviewer:v1:abc123def456 -->"))
        self.assertFalse(render.is_ours("someone else's comment"))

    def test_worktrees_left_by_an_older_build_are_still_pruned(self) -> None:
        from reviewer import worktree

        self.assertTrue(worktree._is_ours(Path("/tmp/blinky-42-xyz")))
        self.assertTrue(worktree._is_ours(Path("/tmp/pr-reviewer-42-xyz")))
        self.assertFalse(worktree._is_ours(Path("/tmp/somebody-elses-worktree")))

    def test_the_history_is_carried_to_the_renamed_directory(self) -> None:
        from reviewer import state as state_mod

        root = Path(tempfile.mkdtemp())
        legacy = root / state_mod.LEGACY_STATE_DIR_NAME
        legacy.mkdir()
        (legacy / "state.sqlite3").write_text("history", encoding="utf-8")

        new = root / state_mod.STATE_DIR_NAME
        self.assertEqual(state_mod.adopt_legacy_state_dir(new), legacy)
        self.assertEqual((new / "state.sqlite3").read_text(encoding="utf-8"), "history")
        # Once only, and never over the top of something already there.
        self.assertIsNone(state_mod.adopt_legacy_state_dir(new))

    def test_a_directory_the_user_named_is_never_moved(self) -> None:
        from reviewer import state as state_mod

        root = Path(tempfile.mkdtemp())
        (root / state_mod.LEGACY_STATE_DIR_NAME).mkdir()
        self.assertIsNone(state_mod.adopt_legacy_state_dir(root / "somewhere-else"))
