"""What a repeat review of the same pull request is allowed to do.

The behaviour under test is the one that keeps a long-running pull request from
collecting a fresh set of findings every time somebody pushes: a narrower diff,
a rising severity bar, and a reviewer that can see what it already said.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

from reviewer import gates, prompt, publish
from reviewer.config import REPO_DEFAULTS, RepoConfig, _deep_merge
from reviewer.gh.graphql import ReviewThread, ThreadComment
from reviewer.pipeline import Reviewer
from reviewer.render import Finding
from reviewer.state import PRState


def repo_config(
    gates_overrides: dict[str, Any] | None = None, **review_overrides: Any
) -> RepoConfig:
    data = _deep_merge(
        REPO_DEFAULTS,
        {"review": review_overrides, "gates": gates_overrides or {}},
    )
    return RepoConfig(
        repo="acme/widgets",
        owner="acme",
        name="widgets",
        enabled=True,
        local_path=None,
        identity="reviewer-bot",
        language="en",
        agent_language="en",
        model={},
        gates=data["gates"],
        diff=data["diff"],
        review=data["review"],
        approval=data["approval"],
        notify_on=data["notify_on"],
        source_file=Path("/dev/null"),
    )


def finding(severity: str, path: str = "a.py", line: int = 1) -> Finding:
    return Finding(
        axis="standards",
        severity=severity,
        path=path,
        line=line,
        side="RIGHT",
        title=f"a {severity}",
        human="something",
        agent_task="do something",
    )


def thread(
    *,
    resolved: bool,
    author: str = "reviewer-bot",
    path: str = "billing/credits.py",
    line: int = 84,
    body: str = "Credit is spent before its owner is checked",
) -> ReviewThread:
    return ReviewThread(
        node_id=f"T_{path}_{line}",
        is_resolved=resolved,
        is_outdated=False,
        path=path,
        line=line,
        original_line=line,
        diff_side="RIGHT",
        comments=[
            ThreadComment(
                node_id="C_1",
                database_id=1,
                author=author,
                body=body,
                created_at="2026-01-01T00:00:00Z",
                url="https://example.invalid/1",
            )
        ],
    )


class TheSeverityBarRises(unittest.TestCase):
    def test_round_one_may_say_anything(self) -> None:
        cfg = repo_config()
        self.assertEqual(
            publish.severities_allowed(cfg, 1),
            {"blocker", "correctness", "nit", "note"},
        )

    def test_nits_stop_after_their_round(self) -> None:
        cfg = repo_config()
        self.assertEqual(
            publish.severities_allowed(cfg, 2), {"blocker", "correctness"}
        )

    def test_eventually_only_blockers_get_through(self) -> None:
        cfg = repo_config()
        self.assertEqual(publish.severities_allowed(cfg, 4), {"blocker"})
        self.assertEqual(publish.severities_allowed(cfg, 9), {"blocker"})

    def test_the_rules_can_be_turned_off(self) -> None:
        cfg = repo_config(
            rounds={"nits_until_round": None, "blockers_only_from_round": None}
        )
        self.assertEqual(
            publish.severities_allowed(cfg, 12),
            {"blocker", "correctness", "nit", "note"},
        )


class FilteringFindingsByRound(unittest.TestCase):
    def test_the_first_round_keeps_everything(self) -> None:
        findings = [finding("blocker"), finding("nit"), finding("note")]
        kept, dropped = publish.filter_by_round(findings, repo_config(), 1)
        self.assertEqual(len(kept), 3)
        self.assertEqual(dropped, [])

    def test_a_later_round_holds_back_the_small_stuff(self) -> None:
        findings = [finding("correctness"), finding("nit"), finding("note")]
        kept, dropped = publish.filter_by_round(findings, repo_config(), 2)
        self.assertEqual([f.severity for f in kept], ["correctness"])
        self.assertEqual({f.severity for f in dropped}, {"nit", "note"})

    def test_round_four_keeps_only_what_holds_up_a_merge(self) -> None:
        findings = [finding("blocker"), finding("correctness"), finding("nit")]
        kept, dropped = publish.filter_by_round(findings, repo_config(), 4)
        self.assertEqual([f.severity for f in kept], ["blocker"])
        self.assertEqual(len(dropped), 2)

    def test_a_clean_later_round_posts_nothing_and_that_is_fine(self) -> None:
        kept, dropped = publish.filter_by_round(
            [finding("nit"), finding("note")], repo_config(), 5
        )
        self.assertEqual(kept, [])
        # Dropped findings are not surfaced anywhere. Silence is the point.
        self.assertEqual(len(dropped), 2)


class TheReviewerRemembersWhatItSaid(unittest.TestCase):
    def test_resolved_threads_of_ours_come_back_as_settled_points(self) -> None:
        block = prompt._settled_block(
            [thread(resolved=True)], identity="reviewer-bot"
        )
        self.assertIn("billing/credits.py:84", block)
        self.assertIn("Credit is spent before its owner is checked", block)
        self.assertIn("Do not raise these again", block)

    def test_open_threads_are_not_listed_as_settled(self) -> None:
        self.assertEqual(
            prompt._settled_block([thread(resolved=False)], identity="reviewer-bot"),
            "",
        )

    def test_somebody_elses_resolved_thread_is_not_ours_to_remember(self) -> None:
        self.assertEqual(
            prompt._settled_block(
                [thread(resolved=True, author="someone-else")],
                identity="reviewer-bot",
            ),
            "",
        )

    def test_only_the_headline_is_carried_not_the_whole_argument(self) -> None:
        long_body = "The point\n\n" + ("padding. " * 200)
        block = prompt._settled_block(
            [thread(resolved=True, body=long_body)], identity="reviewer-bot"
        )
        self.assertIn("The point", block)
        self.assertNotIn("padding. padding.", block)


class TheRoundBriefSaysWhatIsDifferent(unittest.TestCase):
    def test_round_one_is_told_to_read_all_of_it(self) -> None:
        brief = prompt._round_brief(1, None, None)
        self.assertIn("First review", brief)
        self.assertIn("Read all of it", brief)

    def test_an_incremental_round_says_so(self) -> None:
        brief = prompt._round_brief(3, "abcdef1234", {"blocker", "correctness"})
        self.assertIn("only what changed since your last review", brief)
        self.assertIn("abcdef12", brief)
        self.assertIn("No nits, no notes", brief)

    def test_a_full_reread_is_warned_about_rather_than_pretended_away(self) -> None:
        brief = prompt._round_brief(3, None, {"blocker", "correctness"})
        self.assertIn("whole pull request again", brief)
        self.assertIn("You have read most of this before", brief)

    def test_a_blockers_only_round_is_told_empty_is_expected(self) -> None:
        brief = prompt._round_brief(5, "abcdef1234", {"blocker"})
        self.assertIn("Only `blocker` findings", brief)
        self.assertIn("empty findings array", brief)


class TheMaxRoundsStop(unittest.TestCase):
    def _snapshot(self, *, review_requested: bool = False) -> Any:
        class Snapshot:
            number = 42
            head_sha = "newsha"
            author = "someone"
            labels: list[str] = []
            base_ref = "main"
            is_draft = False
            additions = 10
            deletions = 1

            def approved_by_others(self, identity: str | None) -> bool:
                return False

            def review_requested_from(self, identity: str | None) -> bool:
                return review_requested

        return Snapshot()

    def _state(self, rounds: int) -> PRState:
        return PRState(last_reviewed_head_sha="oldsha", review_round=rounds)

    def test_no_limit_by_default(self) -> None:
        decision = gates.evaluate(
            self._snapshot(),
            repo_config(gates_overrides={"require_ci_green": False}),
            self._state(30),
            has_new_comments=False,
        )
        self.assertTrue(decision.should_review)

    def test_at_the_limit_it_stops_and_says_why(self) -> None:
        decision = gates.evaluate(
            self._snapshot(),
            repo_config(
                gates_overrides={"require_ci_green": False}, rounds={"max_rounds": 6}
            ),
            self._state(6),
            has_new_comments=False,
        )
        self.assertFalse(decision.should_review)
        self.assertIn("max_rounds", decision.reason)

    def test_asking_for_a_re_review_still_overrides_the_limit(self) -> None:
        decision = gates.evaluate(
            self._snapshot(review_requested=True),
            repo_config(
                gates_overrides={"require_ci_green": False}, rounds={"max_rounds": 6}
            ),
            self._state(9),
            has_new_comments=False,
        )
        self.assertTrue(decision.should_review)


class FakeRest:
    """Just the two calls ``_files_for_review`` makes."""

    def __init__(
        self,
        pr_files: list[dict[str, Any]],
        compare: list[dict[str, Any]] | None = None,
    ) -> None:
        self.pr_files = pr_files
        self.compare = compare
        self.compared: list[tuple[str, str]] = []

    def list_pull_files(self, owner: str, repo: str, number: int) -> list[dict[str, Any]]:
        return self.pr_files

    def compare_commits(
        self, owner: str, repo: str, base: str, head: str
    ) -> list[dict[str, Any]] | None:
        self.compared.append((base, head))
        return self.compare


def files(*names: str) -> list[dict[str, Any]]:
    return [{"filename": n, "additions": 5, "deletions": 1, "patch": "@@"} for n in names]


def reviewer_with(rest: FakeRest, cfg: RepoConfig) -> Reviewer:
    obj = Reviewer.__new__(Reviewer)
    obj.rest = rest  # type: ignore[attr-defined]
    obj.cfg = cfg  # type: ignore[attr-defined]
    return obj


class ChoosingWhatToRead(unittest.TestCase):
    def test_the_first_round_reads_the_whole_pull_request(self) -> None:
        rest = FakeRest(files("a.py", "b.py"))
        got, since = reviewer_with(rest, repo_config())._files_for_review(
            42, None, "head", 1
        )
        self.assertEqual(len(got or []), 2)
        self.assertIsNone(since)
        self.assertEqual(rest.compared, [])

    def test_a_later_round_reads_only_what_arrived_since(self) -> None:
        rest = FakeRest(files("a.py", "b.py"), compare=files("b.py"))
        got, since = reviewer_with(rest, repo_config())._files_for_review(
            42, "oldsha", "newsha", 3
        )
        self.assertEqual([f["filename"] for f in got or []], ["b.py"])
        self.assertEqual(since, "oldsha")
        self.assertEqual(rest.compared, [("oldsha", "newsha")])

    def test_base_branch_merges_do_not_count_as_the_authors_work(self) -> None:
        # The compare picks up files from a merged-in base branch. They are not
        # part of this pull request and must not be reviewed as if they were.
        rest = FakeRest(
            files("a.py", "b.py"), compare=files("b.py", "unrelated/from-main.py")
        )
        got, _ = reviewer_with(rest, repo_config())._files_for_review(
            42, "oldsha", "newsha", 2
        )
        self.assertEqual([f["filename"] for f in got or []], ["b.py"])

    def test_a_push_with_nothing_of_ours_in_it_is_not_reviewed(self) -> None:
        rest = FakeRest(files("a.py"), compare=files("unrelated/from-main.py"))
        got, since = reviewer_with(rest, repo_config())._files_for_review(
            42, "oldsha", "newsha", 2
        )
        self.assertIsNone(got)
        self.assertEqual(since, "oldsha")

    def test_an_unreadable_compare_falls_back_to_the_whole_thing(self) -> None:
        # Force-push, orphaned SHA. Reviewing everything beats reviewing nothing.
        rest = FakeRest(files("a.py", "b.py"), compare=None)
        got, since = reviewer_with(rest, repo_config())._files_for_review(
            42, "oldsha", "newsha", 3
        )
        self.assertEqual(len(got or []), 2)
        self.assertIsNone(since)

    def test_the_incremental_read_can_be_switched_off(self) -> None:
        rest = FakeRest(files("a.py", "b.py"), compare=files("b.py"))
        cfg = repo_config(rounds={"incremental_after_first": False})
        got, since = reviewer_with(rest, cfg)._files_for_review(
            42, "oldsha", "newsha", 5
        )
        self.assertEqual(len(got or []), 2)
        self.assertIsNone(since)
        self.assertEqual(rest.compared, [])

    def test_an_unchanged_head_is_not_compared_against_itself(self) -> None:
        rest = FakeRest(files("a.py"), compare=files())
        got, since = reviewer_with(rest, repo_config())._files_for_review(
            42, "samesha", "samesha", 2
        )
        self.assertEqual(len(got or []), 1)
        self.assertIsNone(since)
        self.assertEqual(rest.compared, [])


if __name__ == "__main__":
    unittest.main()
