"""Writing a summary for a merge nobody watched land.

The thing worth guarding is spend. Every path here either buys exactly one model
call or buys none, and a keystroke that lands twice must not pay twice.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from reviewer import state, summarize


def repo_cfg(name: str = "acme/widgets") -> SimpleNamespace:
    owner, _, short = name.partition("/")
    return SimpleNamespace(repo=name, owner=owner, name=short, model={})


def global_cfg() -> SimpleNamespace:
    return SimpleNamespace(
        token="t",
        api_url="https://api.github.com",
        summary_provider_for=lambda _cfg: {"type": "claude"},
    )


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.store = state.Store(self.dir, "state.sqlite3")
        self.addCleanup(self.store.close)
        self.store.record_merged(
            {
                "repo": "acme/widgets",
                "pr_number": 42,
                "title": "fix: stop the retry storm",
                "author": "ada",
                "url": "https://example.invalid/42",
                "description": "fix: stop the retry storm",
                "description_source": "title",
                "description_tries": 99,
                "backfilled": 1,
            }
        )

    def _runner(self, *, describe_returns="A bounded retry was added.", pull=None):
        runner = summarize.Runner(
            open_store=lambda: state.Store(self.dir, "state.sqlite3"),
            global_cfg=global_cfg(),
            repos=[repo_cfg()],
        )
        rest = mock.MagicMock()
        rest.get_pull.return_value = pull if pull is not None else {"number": 42}
        rest.get.return_value = [{"filename": "sender.py"}]
        return runner, rest, mock.patch.multiple(
            summarize,
            RestClient=mock.MagicMock(return_value=rest),
            describe=mock.MagicMock(return_value=describe_returns),
        )

    def _drain(self, runner: summarize.Runner, timeout: float = 5.0) -> None:
        deadline = time.time() + timeout
        while runner.busy and time.time() < deadline:
            time.sleep(0.01)
        self.assertFalse(runner.busy, "the worker thread did not finish")

    def description(self) -> tuple[str, str]:
        row = self.store.list_merged(["acme/widgets"], limit=1, offset=0)[0]
        return row["description"], row["description_source"]

    # ------------------------------------------------------------ the work

    def test_it_stores_the_summary_as_if_a_tick_had_written_it(self):
        runner, _rest, patched = self._runner()
        with patched:
            self.assertTrue(runner.request("acme/widgets", 42))
            self._drain(runner)
        self.assertEqual(
            self.description(), ("A bounded retry was added.", "model")
        )

    def test_the_file_list_is_fetched_when_we_never_reviewed_it(self):
        # Which is every backfilled row. Without it the model has the title and
        # the line counts and nothing else to go on.
        runner, rest, patched = self._runner()
        with patched:
            runner.request("acme/widgets", 42)
            self._drain(runner)
            self.assertEqual(
                summarize.describe.call_args.kwargs["files"], ["sender.py"]
            )

    def test_a_repository_that_is_not_configured_is_refused(self):
        runner, _rest, patched = self._runner()
        with patched:
            self.assertFalse(runner.request("other/thing", 1))
        self.assertFalse(runner.busy)

    # ---------------------------------------------------------- not paying

    def test_the_same_pull_request_twice_buys_one_call(self):
        runner, _rest, patched = self._runner()
        with patched:
            self.assertTrue(runner.request("acme/widgets", 42))
            self.assertFalse(runner.request("acme/widgets", 42))
            self._drain(runner)
            self.assertEqual(summarize.describe.call_count, 1)

    def test_a_failed_call_leaves_the_title_alone(self):
        # Better the author's own words than a blank row.
        runner, _rest, patched = self._runner(describe_returns="")
        with patched:
            runner.request("acme/widgets", 42)
            self._drain(runner)
        self.assertEqual(self.description(), ("fix: stop the retry storm", "title"))
        self.assertIn("could not", runner.status()["message"])

    def test_github_refusing_the_pull_request_is_survivable(self):
        from reviewer.gh import GitHubError

        runner, rest, patched = self._runner()
        rest.get_pull.side_effect = GitHubError(404, "gone", "/x")
        with patched:
            runner.request("acme/widgets", 42)
            self._drain(runner)
        self.assertEqual(runner.status()["phase"], "done")
        self.assertEqual(self.description()[1], "title")

    def test_cancelling_drops_what_is_queued(self):
        runner, _rest, patched = self._runner()
        with patched:
            runner.request("acme/widgets", 42)
            runner.cancel()
            self._drain(runner)
        self.assertEqual(runner.status()["pending"], 0)

    # ------------------------------------------------------------- status

    def test_it_reports_what_it_did_and_then_clears(self):
        runner, _rest, patched = self._runner()
        with patched:
            runner.request("acme/widgets", 42)
            self._drain(runner)
        self.assertEqual(runner.status()["phase"], "done")
        self.assertEqual(runner.status()["written"], 1)
        runner.dismiss()
        self.assertEqual(runner.status()["phase"], "idle")

    def test_after_dismissing_the_same_row_can_be_asked_for_again(self):
        # Deduplication is per run, not for ever: a summary that came out badly
        # should be re-askable without restarting the tool.
        runner, _rest, patched = self._runner()
        with patched:
            runner.request("acme/widgets", 42)
            self._drain(runner)
            runner.dismiss()
            self.assertTrue(runner.request("acme/widgets", 42))
            self._drain(runner)


if __name__ == "__main__":
    unittest.main()
