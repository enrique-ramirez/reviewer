"""Which merged pull requests get written down.

The record behind both the Summary and the History tab is filled by one filter,
and what it lets through is what those tabs can ever show.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from reviewer.config import GLOBAL_DEFAULTS, REPO_DEFAULTS, GlobalConfig, RepoConfig
from reviewer.pipeline import Reviewer
from reviewer.state import Store

IDENTITY = "enrique-ramirez"


def repo_config() -> RepoConfig:
    return RepoConfig(
        repo="acme/widgets",
        owner="acme",
        name="widgets",
        enabled=True,
        local_path=None,
        identity=IDENTITY,
        language="en",
        agent_language="en",
        model={},
        gates=REPO_DEFAULTS["gates"],
        diff=REPO_DEFAULTS["diff"],
        review=REPO_DEFAULTS["review"],
        approval=REPO_DEFAULTS["approval"],
        notify_on=REPO_DEFAULTS["notify_on"],
        source_file=Path("/dev/null"),
    )


def global_config() -> GlobalConfig:
    """Everything the merge path touches, with the summary model turned off.

    A description is a model call, and what is under test here is the filter in
    front of it rather than the writing.
    """
    return GlobalConfig(
        tick_seconds=900,
        default_language="en",
        max_reviews_per_tick=5,
        max_tick_seconds=None,
        provider="none",
        providers={},
        merge_summary={**GLOBAL_DEFAULTS["merge_summary"], "enabled": False},
        thread_reply=GLOBAL_DEFAULTS["thread_reply"],
        notifications={},
        logging={},
        api_url="https://api.invalid",
        graphql_url="https://api.invalid/graphql",
        token="x",
    )


class FakeRest:
    """``get_pull`` and nothing else — the only call this path makes."""

    def __init__(self, pulls: dict[int, dict[str, Any]]) -> None:
        self.pulls = pulls
        self.asked: list[int] = []

    def get_pull(self, owner: str, name: str, number: int) -> dict[str, Any]:
        self.asked.append(number)
        return self.pulls[number]


def pull(number: int, author: str) -> dict[str, Any]:
    return {
        "number": number,
        "title": f"something #{number}",
        "user": {"login": author},
        "html_url": f"https://example.invalid/{number}",
        "base": {"ref": "main"},
        "labels": [],
        "additions": 10,
        "deletions": 2,
        "changed_files": 3,
        "created_at": "2026-08-20T09:00:00Z",
        "merged_at": "2026-08-22T17:00:00Z",
        "merged_by": {"login": author},
    }


class WhatLandsInTheRecord(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self._tmp.name))
        self.rest = FakeRest(
            {
                1800: pull(1800, IDENTITY),
                1801: pull(1801, "ada"),
                1802: pull(1802, "ada"),
            }
        )

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def _board(self, number: int, author: str) -> None:
        self.store.upsert_pr_view(
            {
                "repo": "acme/widgets",
                "pr_number": number,
                "title": f"something #{number}",
                "author": author,
                "url": f"https://example.invalid/{number}",
                "is_ours": int(author == IDENTITY),
                "base_ref": "main",
                "head_sha": "abcdef",
            }
        )

    def _reviewer(self) -> Reviewer:
        return Reviewer(
            cfg=repo_config(),
            global_cfg=global_config(),
            rest=self.rest,  # type: ignore[arg-type]
            graphql=None,  # type: ignore[arg-type]
            store=self.store,
            personality_dir=Path("/dev/null"),
            debug=None,  # type: ignore[arg-type]
        )

    def _recorded(self) -> set[int]:
        return {
            int(row["pr_number"])
            for row in self.store.list_merged(["acme/widgets"])
        }

    def test_our_own_merge_is_recorded_though_we_never_reviewed_it(self) -> None:
        """The bug: skip_own_prs meant nothing we shipped ever reached history."""
        self._board(1800, IDENTITY)
        self._reviewer()._record_merges(open_numbers=[])
        self.assertEqual(self._recorded(), {1800})

    def test_it_is_filed_as_ours(self) -> None:
        self._board(1800, IDENTITY)
        self._reviewer()._record_merges(open_numbers=[])
        row = self.store.list_merged(["acme/widgets"])[0]
        self.assertTrue(row["is_ours"])
        self.assertEqual(row["author"], IDENTITY)
        # Nobody reviewed it, and the record says so rather than implying we did.
        self.assertEqual(row["our_reviews"], 0)

    def test_someone_elses_merge_still_needs_a_review_from_us(self) -> None:
        """The cost model is unchanged for everything we did not write."""
        self._board(1801, "ada")
        self._board(1802, "ada")
        self.store.record_review_event(
            "acme/widgets", 1802, head_sha="abcdef", event="COMMENT", inline=1
        )
        self._reviewer()._record_merges(open_numbers=[])
        self.assertEqual(self._recorded(), {1802})
        # And the one we ignored never cost a request.
        self.assertEqual(self.rest.asked, [1802])

    def test_a_merge_already_on_record_is_not_filed_twice(self) -> None:
        self._board(1800, IDENTITY)
        reviewer = self._reviewer()
        reviewer._record_merges(open_numbers=[])
        reviewer._record_merges(open_numbers=[])
        self.assertEqual(self.rest.asked, [1800])

    def test_one_of_ours_that_is_still_open_is_left_alone(self) -> None:
        self._board(1800, IDENTITY)
        self._reviewer()._record_merges(open_numbers=[1800])
        self.assertEqual(self._recorded(), set())


if __name__ == "__main__":
    unittest.main()
