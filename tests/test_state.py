from __future__ import annotations

import tempfile
import threading
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


class OpeningTheSameDatabaseTwice(unittest.TestCase):
    """The dashboard runs two connections — the reviewer's and the interface's.

    On a first run they open the same brand-new file at the same time, and both
    try to switch it to WAL. That needs a brief exclusive lock which SQLite
    refuses immediately rather than waiting for, so one of them used to raise
    ``database is locked`` — on the very first launch and never again.
    """

    def test_many_threads_can_open_a_new_database_at_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            threads = 8
            start = threading.Barrier(threads)
            failures: list[str] = []
            opened: list[str] = []
            lock = threading.Lock()

            def open_one() -> None:
                # A connection belongs to the thread that made it, so this one
                # opens, checks and closes without ever handing it back.
                store = None
                try:
                    start.wait(timeout=10)
                    store = Store(path)
                    mode = store.conn.execute("PRAGMA journal_mode").fetchone()[0]
                    with lock:
                        opened.append(str(mode).lower())
                except BaseException as exc:  # noqa: BLE001 - reported, not raised
                    with lock:
                        failures.append(f"{type(exc).__name__}: {exc}")
                finally:
                    if store is not None:
                        store.close()

            workers = [threading.Thread(target=open_one) for _ in range(threads)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=30)

            self.assertEqual(failures, [])
            # And every one of them ended up on WAL, whoever won the race.
            self.assertEqual(opened, ["wal"] * threads)


class WorkInFlight(unittest.TestCase):
    """What the board reads to tell a slow review from a stuck one."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self._tmp.name))

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def row(self) -> dict:
        return self.store.active_reviews()["acme/widgets#7"]

    def test_a_new_row_has_no_heartbeat_yet(self) -> None:
        self.store.begin_active("acme/widgets", 7, "reviewing")
        self.assertIsNone(self.row()["heartbeat_at"])

    def test_a_beat_records_what_the_call_is_doing(self) -> None:
        self.store.begin_active("acme/widgets", 7, "reviewing")
        self.store.beat_active(
            "acme/widgets", 7, slept_seconds=720, silent_seconds=4, note="reading x.py"
        )
        row = self.row()
        self.assertIsNotNone(row["heartbeat_at"])
        self.assertEqual(row["slept_seconds"], 720)
        self.assertEqual(row["silent_seconds"], 4)
        self.assertEqual(row["note"], "reading x.py")

    def test_a_beat_never_resurrects_finished_work(self) -> None:
        # An UPDATE, not an upsert. If the row is gone the work is over — or
        # the tick that owned it died and cleared it — and a late heartbeat
        # must not put a review back on the board that nobody is running.
        self.store.begin_active("acme/widgets", 7, "reviewing")
        self.store.end_active("acme/widgets", 7)
        self.store.beat_active("acme/widgets", 7, note="too late")
        self.assertEqual(self.store.active_reviews(), {})

    def test_a_new_phase_does_not_inherit_the_last_one_s_heartbeat(self) -> None:
        self.store.begin_active("acme/widgets", 7, "replying")
        self.store.beat_active("acme/widgets", 7, silent_seconds=600, note="stale")
        self.store.begin_active("acme/widgets", 7, "reviewing")
        row = self.row()
        self.assertEqual(row["phase"], "reviewing")
        self.assertIsNone(row["note"])
        self.assertIsNone(row["silent_seconds"])

    def test_an_older_database_gains_the_columns_on_open(self) -> None:
        # The migration is additive, so a database written before any of this
        # existed opens and reads as "nothing to report" rather than failing.
        with tempfile.TemporaryDirectory() as older:
            path = Path(older)
            legacy = Store(path)
            legacy.conn.execute("DROP TABLE active_reviews")
            legacy.conn.execute(
                "CREATE TABLE active_reviews (repo TEXT NOT NULL, "
                "pr_number INTEGER NOT NULL, phase TEXT NOT NULL, "
                "started_at REAL NOT NULL, PRIMARY KEY (repo, pr_number))"
            )
            legacy.conn.execute(
                "INSERT INTO active_reviews VALUES ('acme/widgets', 7, "
                "'reviewing', 1000.0)"
            )
            legacy.conn.commit()
            legacy.close()

            reopened = Store(path)
            self.addCleanup(reopened.close)
            row = reopened.active_reviews()["acme/widgets#7"]
            self.assertEqual(row["phase"], "reviewing")
            self.assertIsNone(row["heartbeat_at"])


if __name__ == "__main__":
    unittest.main()
