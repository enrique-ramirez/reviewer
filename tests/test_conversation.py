"""Reading a review conversation from inside the terminal.

Fetched rather than stored, so the things worth testing are what gets rendered
from a fetch and what happens when the fetch does not work. There is no schema
here to get wrong; there is a screen that must never come up blank.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from reviewer import conversation, state
from reviewer.gh import GitHubError
from reviewer.gh.graphql import PRSnapshot, ReviewThread, ThreadComment


def comment(author: str, body: str, url: str = "https://example.invalid/1"):
    return ThreadComment(
        node_id="c", database_id=1, author=author, body=body, created_at="", url=url
    )


def thread(*comments, resolved: bool = False, outdated: bool = False, path="src/a.py"):
    return ReviewThread(
        node_id="t",
        is_resolved=resolved,
        is_outdated=outdated,
        path=path,
        line=12,
        original_line=12,
        diff_side="RIGHT",
        comments=list(comments),
    )


def review(author: str, body: str = "", state_: str = "COMMENTED", ours: bool = False):
    return conversation.Review(
        author=author, state=state_, body=body, submitted_at="", url="", ours=ours
    )


def conv(**overrides) -> conversation.Conversation:
    fields = {"repo": "acme/widgets", "number": 7, "title": "Ship it", "identity": "blinky"}
    fields.update(overrides)
    return conversation.Conversation(**fields)


class Rendering(unittest.TestCase):
    def test_a_thread_carries_who_said_what_and_a_way_back(self):
        text = conversation.render(
            conv(threads=(thread(comment("blinky", "**Blocker** — it leaks.")),))
        )
        self.assertIn("src/a.py:12", text)
        self.assertIn("@blinky", text)
        self.assertIn("**Blocker** — it leaks.", text)
        self.assertIn("https://example.invalid/1", text)

    def test_our_own_comments_are_marked_as_ours(self):
        # Otherwise a page of bot reviews all read the same.
        text = conversation.render(
            conv(threads=(thread(comment("blinky", "x"), comment("ada", "y")),))
        )
        self.assertIn("@blinky *(this tool)*", text)
        self.assertNotIn("@ada *(this tool)*", text)

    def test_resolution_is_stated_because_it_is_the_thing_you_came_for(self):
        resolved = conversation.render(
            conv(threads=(thread(comment("ada", "x"), resolved=True, outdated=True),))
        )
        self.assertIn("resolved", resolved)
        self.assertIn("outdated", resolved)
        still_open = conversation.render(conv(threads=(thread(comment("ada", "x")),)))
        self.assertNotIn("resolved", still_open.split("###")[1])

    def test_the_counts_are_of_threads_not_comments(self):
        text = conversation.render(
            conv(
                threads=(
                    thread(comment("a", "x"), comment("b", "y"), resolved=True),
                    thread(comment("a", "z")),
                )
            )
        )
        self.assertIn("1 open, 1 resolved", text)

    def test_a_review_with_no_body_says_so_rather_than_showing_a_gap(self):
        text = conversation.render(conv(reviews=(review("ada", ""),)))
        self.assertIn("inline comments only", text)

    def test_nothing_said_is_a_sentence_not_an_empty_screen(self):
        text = conversation.render(conv())
        self.assertIn("Nothing said yet", text)

    def test_a_failed_fetch_says_what_went_wrong(self):
        text = conversation.render(conv(error="HTTP 404 for /pulls/7"))
        self.assertIn("Could not read it", text)
        self.assertIn("404", text)

    def test_a_very_long_comment_is_cut_and_says_so(self):
        text = conversation.render(
            conv(threads=(thread(comment("ada", "x" * 9000)),))
        )
        self.assertIn("truncated", text)
        self.assertLess(len(text), 9000)


class Capping(unittest.TestCase):
    """A busy repository can carry thirty reviews. Ours must not be buried."""

    def test_every_review_of_ours_survives_the_cap(self):
        reviews = tuple(
            [review(f"bot{i}") for i in range(20)] + [review("blinky", ours=True)] * 3
        )
        shown, dropped = conversation._worth_showing(reviews)
        self.assertEqual(sum(1 for r in shown if r.ours), 3)
        self.assertEqual(dropped, 20 - conversation.MAX_OTHER_REVIEWS)

    def test_what_was_left_out_is_said_out_loud(self):
        # A cap nobody is told about reads as "that was all of it".
        reviews = tuple(review(f"bot{i}") for i in range(20))
        text = conversation.render(conv(reviews=reviews))
        self.assertIn("not shown", text)
        self.assertIn(str(20 - conversation.MAX_OTHER_REVIEWS), text)

    def test_a_short_conversation_is_not_capped_or_apologised_for(self):
        text = conversation.render(conv(reviews=(review("ada", "looks fine"),)))
        self.assertNotIn("not shown", text)

    def test_the_order_they_were_left_in_is_kept(self):
        reviews = (review("a"), review("blinky", ours=True), review("c"))
        shown, _ = conversation._worth_showing(reviews)
        self.assertEqual([r.author for r in shown], ["a", "blinky", "c"])


class Fetching(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.cfg = SimpleNamespace(
            repo="acme/widgets", owner="acme", name="widgets", identity="blinky"
        )
        self.global_cfg = SimpleNamespace(
            token="t",
            api_url="https://api.github.com",
            graphql_url="https://api.github.com/graphql",
        )

    def _runner(self) -> conversation.Runner:
        return conversation.Runner(
            open_store=lambda: state.Store(self.dir, "state.sqlite3"),
            global_cfg=self.global_cfg,
            repos=[self.cfg],
        )

    def _drain(self, runner, repo="acme/widgets", number=7, timeout=5.0):
        deadline = time.time() + timeout
        while runner.result(repo, number) is None and time.time() < deadline:
            time.sleep(0.01)
        found = runner.result(repo, number)
        self.assertIsNotNone(found, "the fetch never finished")
        return found

    def _patched(self, snapshot=None, reviews=None, snapshot_error=None):
        graphql = mock.MagicMock()
        if snapshot_error is not None:
            graphql.pr_snapshot.side_effect = snapshot_error
        else:
            graphql.pr_snapshot.return_value = snapshot
        rest = mock.MagicMock()
        rest.list_reviews.return_value = reviews if reviews is not None else []
        return mock.patch.multiple(
            conversation,
            GraphQLClient=mock.MagicMock(return_value=graphql),
            RestClient=mock.MagicMock(return_value=rest),
        ), rest

    def _snapshot(self, threads=()):
        snap = mock.MagicMock(spec=PRSnapshot)
        snap.title = "Ship it"
        snap.threads = list(threads)
        return snap

    def test_it_brings_back_threads_and_reviews(self):
        patched, _rest = self._patched(
            snapshot=self._snapshot([thread(comment("blinky", "hi"))]),
            reviews=[{"user": {"login": "blinky"}, "state": "APPROVED", "body": "ok"}],
        )
        runner = self._runner()
        with patched:
            runner.request("acme/widgets", 7)
            found = self._drain(runner)
        self.assertEqual(len(found.threads), 1)
        self.assertEqual(len(found.reviews), 1)
        self.assertTrue(found.reviews[0].ours, "our own login should be recognised")

    def test_a_pending_review_is_somebody_still_typing(self):
        patched, _rest = self._patched(
            snapshot=self._snapshot(),
            reviews=[
                {"user": {"login": "ada"}, "state": "PENDING", "body": "half-written"},
                {"user": {"login": "ada"}, "state": "COMMENTED", "body": "done"},
            ],
        )
        runner = self._runner()
        with patched:
            runner.request("acme/widgets", 7)
            found = self._drain(runner)
        self.assertEqual([r.body for r in found.reviews], ["done"])

    def test_github_refusing_the_threads_is_reported_not_raised(self):
        patched, _rest = self._patched(
            snapshot_error=GitHubError(404, "gone", "/pulls/7")
        )
        runner = self._runner()
        with patched:
            runner.request("acme/widgets", 7)
            found = self._drain(runner)
        self.assertIn("404", found.error)
        self.assertIn("Could not read it", conversation.render(found))

    def test_losing_the_reviews_still_shows_the_threads(self):
        # The inline notes are the substance; a missing covering letter is worth
        # a log line rather than an empty screen.
        patched, rest = self._patched(
            snapshot=self._snapshot([thread(comment("ada", "look here"))])
        )
        rest.list_reviews.side_effect = GitHubError(403, "no", "/reviews")
        runner = self._runner()
        with patched:
            runner.request("acme/widgets", 7)
            found = self._drain(runner)
        self.assertEqual(found.error, "")
        self.assertIn("look here", conversation.render(found))

    def test_asking_twice_does_not_fetch_twice(self):
        patched, _rest = self._patched(snapshot=self._snapshot())
        runner = self._runner()
        with patched:
            runner.request("acme/widgets", 7)
            self._drain(runner)
            self.assertFalse(runner.request("acme/widgets", 7))

    def test_but_a_refresh_does(self):
        # The whole point of asking rather than storing is that it changes.
        patched, _rest = self._patched(snapshot=self._snapshot())
        runner = self._runner()
        with patched:
            runner.request("acme/widgets", 7)
            self._drain(runner)
            self.assertTrue(runner.request("acme/widgets", 7, refresh=True))

    def test_a_repository_we_do_not_watch_is_refused(self):
        runner = self._runner()
        self.assertFalse(runner.knows("other/thing"))
        self.assertFalse(runner.request("other/thing", 1))


if __name__ == "__main__":
    unittest.main()
