from __future__ import annotations

import unittest

from reviewer.tui import theme
from reviewer.tui.models import Activity
from reviewer.tui.status import (
    attention,
    peer_reviews,
    peer_verdict,
    reports_last_pass,
    status_of,
)
from tests.builders import pull_request


class Attention(unittest.TestCase):
    def test_nothing_outstanding_flies_no_flag(self) -> None:
        self.assertIsNone(attention(pull_request()))

    def test_a_parked_thread_outranks_everything(self) -> None:
        flagged = pull_request(capped_threads=1, needs_human=1, is_ours=1)
        self.assertIs(attention(flagged), theme.DISAGREEMENT)

    def test_a_held_back_approval_asks_for_a_human(self) -> None:
        self.assertIs(attention(pull_request(needs_human=1)), theme.APPROVAL)

    def test_our_own_approved_work_is_flagged_by_mergeability(self) -> None:
        ours = dict(is_ours=1, review_decision="APPROVED")
        self.assertIs(
            attention(pull_request(**ours, mergeable="CONFLICTING")), theme.CONFLICT
        )
        self.assertIs(
            attention(pull_request(**ours, mergeable="MERGEABLE")), theme.MERGEABLE
        )
        self.assertIs(
            attention(pull_request(**ours, mergeable="UNKNOWN")), theme.MERGE_PENDING
        )

    def test_replies_are_only_ours_to_answer_on_other_peoples_work(self) -> None:
        self.assertIs(
            attention(pull_request(threads_awaiting_us=2)), theme.REPLIES
        )
        self.assertIsNone(attention(pull_request(threads_awaiting_us=2, is_ours=1)))


class PeerReviews(unittest.TestCase):
    def test_our_own_review_of_our_own_pull_request_is_not_a_peer_review(self) -> None:
        mine = pull_request(
            is_ours=1,
            author="me",
            reviews=[{"author": "me", "state": "COMMENTED"},
                     {"author": "you", "state": "APPROVED"}],
        )
        self.assertEqual([r.author for r in peer_reviews(mine)], ["you"])

    def test_every_review_counts_on_someone_elses_pull_request(self) -> None:
        theirs = pull_request(
            author="them", reviews=[{"author": "them", "state": "COMMENTED"}]
        )
        self.assertEqual(len(peer_reviews(theirs)), 1)


class PeerVerdict(unittest.TestCase):
    def _ours(self, **kwargs: object) -> object:
        return peer_verdict(pull_request(is_ours=1, author="me", **kwargs))

    def test_changes_requested_wins_over_an_approval(self) -> None:
        verdict = self._ours(
            reviews=[{"author": "a", "state": "APPROVED"},
                     {"author": "b", "state": "CHANGES_REQUESTED"}]
        )
        self.assertEqual(verdict.text, "changes req.")

    def test_an_approval_without_a_decision_wants_one_more(self) -> None:
        verdict = self._ours(reviews=[{"author": "a", "state": "APPROVED"}])
        self.assertEqual(verdict.text, "needs 1 more")

    def test_a_comment_without_an_approval_wants_signing_off(self) -> None:
        verdict = self._ours(reviews=[{"author": "a", "state": "COMMENTED"}])
        self.assertEqual(verdict.text, "needs sign-off")

    def test_nobody_asked_reads_differently_from_nobody_answered(self) -> None:
        self.assertEqual(self._ours().text, "no reviewer")
        self.assertEqual(
            self._ours(requested_reviewers=["a"]).text, "awaiting review"
        )


class StatusOf(unittest.TestCase):
    def test_live_work_outranks_the_last_pass(self) -> None:
        working = pull_request(
            review_decision="APPROVED", activity=Activity("replying", 0)
        )
        self.assertEqual(status_of(working).text, "replying")

    def test_a_decision_outranks_our_own_last_action(self) -> None:
        self.assertEqual(
            status_of(pull_request(review_decision="APPROVED",
                                   last_action="COMMENT")).text,
            "approved",
        )

    def test_our_own_pull_requests_report_what_others_did(self) -> None:
        mine = pull_request(is_ours=1, last_action="skipped: authored by us")
        self.assertEqual(status_of(mine).text, "no reviewer")

    def test_a_comment_leaves_nothing_outstanding_from_us(self) -> None:
        commented = status_of(pull_request(last_action="COMMENT"))
        self.assertEqual((commented.text, commented.style), ("reviewed", theme.DONE))

    def test_a_skip_after_a_review_does_not_read_as_never_reviewed(self) -> None:
        # The skip reason overwrites the review action, so without the event
        # log this row claimed "not reviewed" while its own detail pane said
        # "skipped: nothing changed since last review".
        settled = pull_request(
            last_action="skipped: nothing changed since last review", reviewed=True
        )
        verdict = status_of(settled)
        self.assertEqual((verdict.text, verdict.style), ("reviewed", theme.DONE))

    def test_a_skip_before_any_review_still_reads_as_never_reviewed(self) -> None:
        untouched = pull_request(last_action="skipped: CI still running: build")
        verdict = status_of(untouched)
        self.assertEqual((verdict.text, verdict.style), ("not reviewed", theme.PENDING))

    def test_a_skipped_pull_request_of_ours_says_nothing_about_the_last_pass(
        self,
    ) -> None:
        self.assertFalse(
            reports_last_pass(pull_request(is_ours=1, last_action="skipped: ours"))
        )
        self.assertTrue(reports_last_pass(pull_request(last_action="skipped: draft")))


if __name__ == "__main__":
    unittest.main()
