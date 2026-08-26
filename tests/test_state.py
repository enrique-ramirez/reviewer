from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from reviewer.state import Store


class ReviewedPullRequests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self._tmp.name))

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def test_nothing_reviewed_is_an_empty_set(self) -> None:
        self.assertEqual(self.store.reviewed_pull_requests(), set())

    def test_a_posted_review_counts_once_however_many_rounds_it_took(self) -> None:
        for _ in range(3):
            self.store.record_review_event(
                "acme/widgets", 12, head_sha="abc", event="COMMENT"
            )
        self.assertEqual(self.store.reviewed_pull_requests(), {"acme/widgets#12"})

    def test_a_review_predating_the_event_log_still_counts(self) -> None:
        # Written by an older build: pr_state only, no review_events row.
        self.store.record_review("acme/widgets", 9, "abc", "APPROVE")
        self.assertIn("acme/widgets#9", self.store.reviewed_pull_requests())

    def test_a_pull_request_only_ever_scanned_does_not_count(self) -> None:
        self.store.record_comment_scan("acme/widgets", 4, 100)
        self.store.set_pr_action("acme/widgets", 4, "skipped: CI still running")
        self.assertEqual(self.store.reviewed_pull_requests(), set())

    def test_the_two_sources_are_merged_rather_than_double_counted(self) -> None:
        self.store.record_review("acme/widgets", 12, "abc", "COMMENT")
        self.store.record_review_event(
            "acme/widgets", 12, head_sha="abc", event="COMMENT"
        )
        self.assertEqual(self.store.reviewed_pull_requests(), {"acme/widgets#12"})

    def test_the_repository_filter_applies_to_both_sources(self) -> None:
        self.store.record_review_event(
            "acme/widgets", 1, head_sha="abc", event="COMMENT"
        )
        self.store.record_review("acme/gadgets", 2, "def", "APPROVE")
        self.assertEqual(
            self.store.reviewed_pull_requests(["acme/widgets"]), {"acme/widgets#1"}
        )
        self.assertEqual(
            self.store.reviewed_pull_requests(["acme/gadgets"]), {"acme/gadgets#2"}
        )
        self.assertEqual(len(self.store.reviewed_pull_requests()), 2)


if __name__ == "__main__":
    unittest.main()
