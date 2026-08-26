from __future__ import annotations

import unittest

from reviewer.tui import theme
from reviewer.tui.data import MergePage
from reviewer.tui.filling import BackfillStatus, in_flight_lines, progress_note
from reviewer.tui.models import Activity
from reviewer.tui.session import Session
from reviewer.tui.views import board, merges
from reviewer.tui.widgets import Progress, signature_of
from tests.builders import NOW, merge, pull_request

REPOS = ("acme/widgets", "acme/gadgets")


def session(**kwargs: object) -> Session:
    return Session(repos=REPOS, started_at=NOW, **kwargs)  # type: ignore[arg-type]


class InView(unittest.TestCase):
    def test_flagged_pull_requests_come_first_then_the_newest(self) -> None:
        ordered = board.in_view(
            [
                pull_request(pr_number=1),
                pull_request(pr_number=2),
                pull_request(pr_number=3, needs_human=1),
            ],
            session(),
        )
        self.assertEqual([pr.number for pr in ordered], [3, 2, 1])

    def test_only_the_chosen_repository_is_shown(self) -> None:
        scoped = board.in_view(
            [
                pull_request(repo="acme/widgets"),
                pull_request(repo="acme/gadgets", pr_number=9),
            ],
            session().with_repo(2),
        )
        self.assertEqual([pr.repo for pr in scoped], ["acme/gadgets"])

    def test_the_attention_filter_hides_everything_settled(self) -> None:
        filtered = board.in_view(
            [pull_request(pr_number=1), pull_request(pr_number=2, needs_human=1)],
            session(only_attention=True),
        )
        self.assertEqual([pr.number for pr in filtered], [2])


class Counts(unittest.TestCase):
    def test_attention_is_counted_per_repository(self) -> None:
        counts = board.attention_by_repo(
            [
                pull_request(repo="acme/widgets", needs_human=1),
                pull_request(repo="acme/widgets", pr_number=2, needs_human=1),
                pull_request(repo="acme/gadgets", pr_number=3),
            ]
        )
        self.assertEqual(counts, {"acme/widgets": 2})

    def test_the_subtitle_says_how_many_want_you(self) -> None:
        rows = [pull_request(), pull_request(pr_number=2, needs_human=1)]
        self.assertEqual(board.subtitle(rows, session()), "2 open — 1 need you")
        self.assertEqual(
            board.subtitle(rows, session(only_attention=True)),
            "2 open — 1 need you  [filtered]",
        )

    def test_the_longest_running_review_drives_the_timer(self) -> None:
        rows = [
            pull_request(activity=Activity("reviewing", NOW - 30)),
            pull_request(pr_number=2, activity=Activity("replying", NOW - 90)),
            pull_request(pr_number=3),
        ]
        self.assertEqual(board.longest_running(rows, NOW), 90)
        self.assertEqual(board.longest_running([pull_request()], NOW), 0.0)


class BoardCells(unittest.TestCase):
    def test_an_idle_row_shows_its_verdict_plainly(self) -> None:
        cells = board.row_cells(pull_request(last_action="APPROVE"), NOW, 0)
        self.assertEqual(cells[0].plain, " ")
        self.assertEqual(cells[2].plain, "approved")

    def test_a_live_row_carries_a_spinner_and_stays_inside_its_column(self) -> None:
        working = pull_request(activity=Activity("reviewing", NOW - 900))
        cells = board.row_cells(working, NOW, 3)
        self.assertTrue(cells[2].plain.startswith(theme.spinner_frame(3)))
        self.assertLessEqual(len(cells[2].plain), 16)

    def test_a_flagged_row_wears_its_glyph(self) -> None:
        cells = board.row_cells(pull_request(needs_human=1), NOW, 0)
        self.assertEqual(cells[0].plain, theme.APPROVAL.glyph)


class MergeCells(unittest.TestCase):
    def test_a_written_summary_is_shown_plainly(self) -> None:
        cells = merges.summary_cells(
            merge(description="Renames the cache key.", description_source="model"),
            NOW,
        )
        self.assertEqual(cells[5].plain, "Renames the cache key.")
        self.assertEqual(str(cells[5].style), "")

    def test_a_backfilled_title_is_not_dressed_up_as_a_summary(self) -> None:
        cells = merges.summary_cells(
            merge(description="Ship it", description_source="title"), NOW
        )
        self.assertEqual(str(cells[5].style), "italic")

    def test_history_falls_back_to_a_dash(self) -> None:
        cells = merges.history_cells(merge(description=""))
        self.assertEqual(cells[5].plain, "—")

    def test_history_drops_the_owner_from_the_repository_column(self) -> None:
        cells = merges.history_cells(merge(repo="acme/widgets"))
        self.assertEqual(cells[0].plain, "widgets")


class MergeDetail(unittest.TestCase):
    def test_a_pull_request_we_never_saw_says_so(self) -> None:
        text = merges.detail_text(merge()).plain
        self.assertIn("not reviewed by this tool", text)

    def test_our_own_comments_are_totalled(self) -> None:
        text = merges.detail_text(merge(our_reviews=2, our_comments=5,
                                        our_blockers=1)).plain
        self.assertIn("5 comment(s) over 2 round(s), 1 blocking", text)


class HistoryEmpty(unittest.TestCase):
    def _context(self, **kwargs: object) -> merges.HistoryContext:
        defaults: dict = {"session": session(), "page": MergePage((), 0)}
        defaults.update(kwargs)
        return merges.HistoryContext(**defaults)  # type: ignore[arg-type]

    def test_a_filtered_view_offers_the_way_out(self) -> None:
        text = merges.history_empty(
            self._context(session=session().with_author("ada"))
        ).plain
        self.assertIn("Escape clears them", text)

    def test_an_empty_page_of_a_full_history_says_which(self) -> None:
        text = merges.history_empty(self._context(page=MergePage((), 90, 3))).plain
        self.assertEqual(text, "Nothing on this page.")

    def test_the_offer_matches_how_the_tool_was_started(self) -> None:
        self.assertIn("press b", merges.history_empty(
            self._context(can_backfill=True)).plain)
        self.assertIn("./run.sh --backfill", merges.history_empty(
            self._context(can_backfill=False)).plain)


class Backfill(unittest.TestCase):
    def test_a_missing_field_does_not_break_the_status(self) -> None:
        status = BackfillStatus.from_status({"phase": "running"})
        self.assertTrue(status.working)
        self.assertEqual(status.filed, 0)

    def test_an_idle_backfill_says_nothing(self) -> None:
        self.assertIsNone(progress_note(BackfillStatus(), 0))
        self.assertEqual(in_flight_lines(BackfillStatus()), ())

    def test_progress_names_what_it_has_and_how_to_stop(self) -> None:
        note = progress_note(
            BackfillStatus(phase="running", filed=120, total=500), 0
        )
        assert note is not None
        self.assertIn("120", note.plain)
        self.assertIn("b to stop", note.plain)


class Pacing(unittest.TestCase):
    def test_a_scan_in_progress_has_no_countdown(self) -> None:
        self.assertFalse(Progress(phase="reviewing #4").counting_down)

    def test_the_track_fills_as_the_wait_runs_down(self) -> None:
        waiting = Progress(phase="waiting", remaining=300.0, total=900.0)
        self.assertTrue(waiting.counting_down)
        self.assertAlmostEqual(waiting.fraction, 2 / 3)
        self.assertEqual(waiting.countdown, "5m00s")


class Signatures(unittest.TestCase):
    def test_a_style_change_alone_counts_as_a_change(self) -> None:
        before = board.row_cells(pull_request(), NOW, 0)
        after = board.row_cells(pull_request(needs_human=1), NOW, 0)
        self.assertNotEqual(signature_of(before), signature_of(after))

    def test_the_same_row_twice_is_the_same_signature(self) -> None:
        self.assertEqual(
            signature_of(board.row_cells(pull_request(), NOW, 0)),
            signature_of(board.row_cells(pull_request(), NOW, 0)),
        )


if __name__ == "__main__":
    unittest.main()
