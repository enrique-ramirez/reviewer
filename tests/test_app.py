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
from reviewer.tui.logs import LogRelay
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

    def _app(self) -> Dashboard:
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
            await pilot.press("h", "t")
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
            self.assertEqual(len(app.query(StatusBar)), 3)
            for view in (BoardView, SummaryView, HistoryView):
                self.assertEqual(len(app.query(view)), 1)
            self.assertIn("merged", app.history.status_text().plain)
            self.assertIn("this run", app.summary.status_text().plain)
            self.assertIn("needs your approval", app.board.status_text().plain)


if __name__ == "__main__":
    unittest.main()
