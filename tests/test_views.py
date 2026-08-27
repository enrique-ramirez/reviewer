from __future__ import annotations

import unittest

from reviewer import backfill
from reviewer.tui import prose, theme
from reviewer.tui.views.base import READ_REVIEW
from reviewer.tui.widgets import ActionBar
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

    def test_work_held_for_later_sorts_below_work_that_wants_you(self) -> None:
        ordered = board.in_view(
            [
                pull_request(
                    pr_number=1, needs_human=1, review_decision="CHANGES_REQUESTED"
                ),
                pull_request(pr_number=2, needs_human=1),
            ],
            session(),
        )
        self.assertEqual([pr.number for pr in ordered], [2, 1])


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
        # And not how many are open: that is on the Dashboard tab now, which is
        # the thing it is true of.
        rows = [pull_request(), pull_request(pr_number=2, needs_human=1)]
        self.assertEqual(board.subtitle(rows), "1 need you")

    def test_a_quiet_board_says_nothing_after_the_title(self) -> None:
        self.assertEqual(board.subtitle([pull_request()]), "")

    def test_a_board_of_only_held_work_promises_you_nothing(self) -> None:
        # "1 need you" against a pull request whose author still has work to do
        # sends you looking for a job that is not there.
        held = pull_request(needs_human=1, review_decision="CHANGES_REQUESTED")
        self.assertEqual(board.subtitle([held]), "")

    def test_the_longest_running_review_drives_the_timer(self) -> None:
        rows = [
            pull_request(activity=Activity("reviewing", NOW - 30)),
            pull_request(pr_number=2, activity=Activity("replying", NOW - 90)),
            pull_request(pr_number=3),
        ]
        self.assertEqual(board.longest_running(rows, NOW), 90)
        self.assertEqual(board.longest_running([pull_request()], NOW), 0.0)


class Legend(unittest.TestCase):
    def test_it_explains_only_the_marks_that_are_on_the_board(self) -> None:
        rows = [pull_request(pr_number=1, needs_human=1), pull_request(pr_number=2)]
        plain = board.legend(rows, 200).plain
        self.assertIn("needs your approval", plain)
        self.assertNotIn("replies to check", plain)
        self.assertNotIn("ready to merge", plain)

    def test_a_board_with_nothing_flagged_says_nothing(self) -> None:
        self.assertEqual(board.legend([pull_request()], 200).plain, "")

    def test_it_never_runs_past_the_width_it_was_given(self) -> None:
        rows = [
            pull_request(pr_number=1, capped_threads=1),
            pull_request(pr_number=2, needs_human=1),
            pull_request(pr_number=3, threads_awaiting_us=2),
            pull_request(
                pr_number=4, needs_human=1, review_decision="CHANGES_REQUESTED"
            ),
        ]
        for width in range(4, 130):
            self.assertLessEqual(
                board.legend(rows, width).cell_len,
                width,
                f"legend overflowed at width {width}",
            )

    def test_what_it_drops_it_counts(self) -> None:
        rows = [
            pull_request(pr_number=1, capped_threads=1),
            pull_request(pr_number=2, needs_human=1),
            pull_request(pr_number=3, threads_awaiting_us=2),
        ]
        narrow = board.legend(rows, 50).plain
        self.assertIn("settle a disagreement", narrow)  # most urgent survives
        self.assertIn("+2 more", narrow)

    def test_no_width_means_no_fitting(self) -> None:
        # Before the first layout there is nothing to fit to; showing the whole
        # thing beats showing one entry and a count of what was hidden.
        rows = [
            pull_request(pr_number=1, capped_threads=1),
            pull_request(pr_number=2, needs_human=1),
        ]
        self.assertNotIn("more", board.legend(rows, 0).plain)


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

    def test_a_quiet_row_shows_how_long_it_has_been_quiet(self) -> None:
        # Not how long it has been running: a call that has said nothing for
        # twelve minutes is the thing worth reading, and it is also the shorter
        # number, which is what keeps this inside sixteen cells.
        quiet = pull_request(
            activity=Activity("reviewing", NOW - 3600, silent_seconds=720)
        )
        cells = board.row_cells(quiet, NOW, 3)
        self.assertIn("quiet", cells[2].plain)
        self.assertIn("12m", cells[2].plain)
        self.assertLessEqual(len(cells[2].plain), 16)


class ActivityLine(unittest.TestCase):
    """The live line under a selected pull request, which is where a review
    that slept through the night has to stop looking like a hung one."""

    def line(self, activity: Activity) -> str:
        return board._activity_line(pull_request(activity=activity), NOW, 0).plain

    def test_a_plain_review_says_only_how_long(self) -> None:
        text = self.line(Activity("reviewing", NOW - 90))
        self.assertIn("reviewing right now", text)
        self.assertIn("1m 30s", text)
        self.assertNotIn("asleep", text)

    def test_time_asleep_is_called_out(self) -> None:
        # The whole incident this exists for: fifteen minutes on the clock, of
        # which twelve were the laptop being shut.
        text = self.line(
            Activity("reviewing", NOW - 940, slept_seconds=720, silent_seconds=4)
        )
        self.assertIn("15m 40s", text)
        self.assertIn("12m of that asleep", text)

    def test_a_few_seconds_of_drift_is_not_worth_mentioning(self) -> None:
        text = self.line(Activity("reviewing", NOW - 90, slept_seconds=3))
        self.assertNotIn("asleep", text)

    def test_a_note_says_what_the_model_is_doing(self) -> None:
        text = self.line(Activity("reviewing", NOW - 90, note="reading auth.py"))
        self.assertIn("reading auth.py", text)

    def test_a_long_silent_final_write_up_is_not_called_quiet(self) -> None:
        # The stream carries one event per turn, not per token, so the last
        # turn — the write-up itself — is legitimately silent for minutes. The
        # threshold sits above that on purpose.
        text = self.line(Activity("reviewing", NOW - 900, silent_seconds=420))
        self.assertNotIn("quiet", text)

    def test_going_quiet_replaces_the_note(self) -> None:
        # Silence is the more useful thing to say, and it says the note is
        # stale anyway.
        text = self.line(
            Activity(
                "reviewing", NOW - 900, silent_seconds=600, note="reading auth.py"
            )
        )
        self.assertIn("quiet for 10m", text)
        self.assertNotIn("reading auth.py", text)


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
            BackfillStatus(phase="running", filed=120, scanned=300, total=500), 0
        )
        assert note is not None
        self.assertIn("300", note.plain)
        self.assertIn("of about 500", note.plain)
        self.assertIn("120 new", note.plain)
        self.assertIn("b to stop", note.plain)

    def test_a_repository_already_on_record_still_shows_movement(self) -> None:
        # The bug this replaced: progress counted rows *filed*, so re-running a
        # backfill over history already on record sat at 0 for the whole sweep
        # and was indistinguishable from a hang.
        early = progress_note(
            BackfillStatus(phase="running", filed=0, scanned=400, total=1594), 0
        )
        later = progress_note(
            BackfillStatus(phase="running", filed=0, scanned=1200, total=1594), 0
        )
        assert early is not None and later is not None
        self.assertIn("400", early.plain)
        self.assertIn("1,200", later.plain)
        self.assertNotEqual(early.plain, later.plain)
        # Nothing new is not worth a "0 new" that reads as a failure.
        self.assertNotIn("new", early.plain)

    def test_what_it_says_when_it_finishes(self) -> None:
        self.assertEqual(
            backfill._outcome(120, 500), "120 added to the history"
        )
        # "0 added" is true and reads as failure. Say what was actually learnt.
        self.assertIn("already up to date", backfill._outcome(0, 1594))
        self.assertIn("1,594 checked", backfill._outcome(0, 1594))
        self.assertEqual(backfill._outcome(0, 0), "nothing found to add")

    def test_quitting_mid_sweep_admits_how_far_it_got(self) -> None:
        lines = in_flight_lines(
            BackfillStatus(phase="running", filed=0, scanned=700, total=1594)
        )
        self.assertIn("700", lines[0].plain)
        self.assertIn("1,594", lines[0].plain)


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


class Panels(unittest.TestCase):
    """The detail panes, after they were given some structure."""

    def test_sections_are_announced_rather_than_implied(self) -> None:
        text = merges.detail_text(merge(), width=50).plain
        for heading in ("what it changed", "the change", "our part in it"):
            self.assertIn(heading, text)

    def test_a_backfilled_row_does_not_repeat_its_own_title(self) -> None:
        # The title is already the headline two lines up; printing it again as
        # a summary said the same thing twice and looked like content.
        row = merge(
            title="ci: add retention policy",
            description="ci: add retention policy",
            description_source="title",
        )
        text = merges.detail_text(row, width=50).plain
        self.assertEqual(text.count("ci: add retention policy"), 1)
        self.assertIn("Not summarised", text)

    def test_a_written_summary_gets_a_bar_down_its_edge(self) -> None:
        row = merge(description="It bounded the retry.", description_source="model")
        text = merges.detail_text(row, width=50).plain
        self.assertIn(f"{theme.CURSOR} It bounded the retry.", text)

    def test_a_long_summary_keeps_the_bar_on_every_line(self) -> None:
        # A bar drawn once above a paragraph that then wraps is a bar against
        # one line of it.
        row = merge(description=" ".join(["word"] * 60), description_source="model")
        body = merges.detail_text(row, width=50).plain
        lines = [ln for ln in body.splitlines() if "word" in ln]
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(ln.startswith(theme.CURSOR) for ln in lines))

    def test_labels_are_drawn_as_badges(self) -> None:
        # Padded and backgrounded, so a label reads as an object rather than as
        # more comma-separated prose.
        badges = prose.badges(["i18n", "automation"])
        self.assertEqual(badges.plain, " i18n   automation ")
        self.assertTrue(
            all(str(span.style) == theme.BADGE for span in badges.spans),
            f"expected every badge styled {theme.BADGE}: {badges.spans}",
        )
        self.assertIn(" i18n ", merges.detail_text(merge(labels=["i18n"]), width=50).plain)

    def test_churn_is_green_and_red(self) -> None:
        churn = prose.churn(283, 14)
        self.assertEqual(churn.plain, "+283 −14")
        added, removed = churn.spans
        self.assertEqual(str(added.style), theme.ADDED)
        self.assertEqual(str(removed.style), theme.REMOVED)

    def test_the_key_hint_is_gone_from_the_prose(self) -> None:
        # It is a button now; saying it twice is how the two drift apart.
        text = merges.detail_text(merge(), width=50).plain
        self.assertNotIn("press o", text)
        self.assertNotIn("press g", text)


class PanelActions(unittest.TestCase):
    def test_a_merge_without_a_summary_offers_to_write_one(self) -> None:
        left, right = merges.merge_actions(
            merge(description="", description_source="")
        )
        self.assertIn(merges.WRITE_SUMMARY, left)
        self.assertIsNotNone(right)

    def test_one_that_has_a_summary_does_not(self) -> None:
        left, _ = merges.merge_actions(
            merge(description="written", description_source="model")
        )
        self.assertNotIn(
            merges.WRITE_SUMMARY, left, "never offer to buy the same sentence twice"
        )

    def test_reading_it_is_offered_only_where_we_said_something(self) -> None:
        reviewed, _ = merges.merge_actions(merge(our_comments=3, our_reviews=1))
        self.assertIn(READ_REVIEW, reviewed)
        untouched, _ = merges.merge_actions(merge(our_comments=0, our_reviews=0))
        self.assertNotIn(READ_REVIEW, untouched)

    def test_nothing_selected_offers_nothing(self) -> None:
        self.assertEqual(merges.merge_actions(None), ((), None))

    def test_a_row_with_no_link_has_nowhere_to_open(self) -> None:
        _, right = merges.merge_actions(merge(url=""))
        self.assertIsNone(right)

    def test_only_as_many_buttons_as_there_are_slots(self) -> None:
        left, _ = merges.merge_actions(
            merge(our_comments=3, our_reviews=1, description="", description_source="")
        )
        self.assertLessEqual(len(left), len(ActionBar.SLOTS))

    def test_the_shortcut_letter_is_underlined(self) -> None:
        from reviewer.tui.widgets import Action

        self.assertEqual(
            Action("open", "Open on GitHub", "o").markup, "[u]O[/u]pen on GitHub"
        )
        self.assertEqual(
            Action("describe", "Generate summary", "g").markup,
            "[u]G[/u]enerate summary",
        )

    def test_a_label_without_its_letter_still_says_the_key(self) -> None:
        from reviewer.tui.widgets import Action

        self.assertEqual(Action("x", "Do it", "z").markup, "Do it (z)")

