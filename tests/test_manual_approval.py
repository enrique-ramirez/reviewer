"""Which pull requests are held back for a person.

The rule that matters here is ``approval.manual_only_when.touches_paths``, and
it is the one that is easiest to get silently wrong: it needs the list of files
a pull request changed, and for a long time the dashboard scan passed it an
empty list. Every rule still ran, none of them could match, and a repository
whose only manual-approval rule was a path pattern showed every pull request as
needing nobody — right up until a review finished and found out otherwise.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

from reviewer import gates
from reviewer.config import REPO_DEFAULTS, RepoConfig, _deep_merge

MIGRATION = "packages/db/src/migrations/0252_stamp_completion.sql"
APP_CODE = "apps/api-functions/src/functions/patients/index.ts"


def config(**approval: Any) -> RepoConfig:
    data = _deep_merge(REPO_DEFAULTS, {"approval": approval})
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


def snapshot(*, additions: int = 100, labels: list[str] | None = None) -> Any:
    class Snapshot:
        pass

    s = Snapshot()
    s.additions = additions  # type: ignore[attr-defined]
    s.deletions = 0  # type: ignore[attr-defined]
    s.labels = labels or []  # type: ignore[attr-defined]
    return s


HELD_PATHS = {
    "mode": "auto",
    "manual_only_when": {"touches_paths": ["packages/db/src/migrations/**"]},
}


class TouchingHeldPaths(unittest.TestCase):
    def test_a_migration_is_held_for_a_human(self) -> None:
        held, reason = gates.needs_manual_approval(
            snapshot(), config(**HELD_PATHS), [APP_CODE, MIGRATION]
        )
        self.assertTrue(held)
        self.assertIn("packages/db/src/migrations", reason)

    def test_ordinary_code_is_not(self) -> None:
        held, _ = gates.needs_manual_approval(
            snapshot(), config(**HELD_PATHS), [APP_CODE]
        )
        self.assertFalse(held)

    def test_without_the_file_list_the_rule_cannot_fire_at_all(self) -> None:
        # This is the bug, written down: same pull request, same config, and the
        # only difference is that nobody told the rule what changed.
        held, _ = gates.needs_manual_approval(snapshot(), config(**HELD_PATHS), [])
        self.assertFalse(held)

    def test_the_other_rules_do_not_need_the_file_list(self) -> None:
        # Which is why the bug hid: a repo using any of these looked fine.
        by_size, _ = gates.needs_manual_approval(
            snapshot(additions=5000),
            config(mode="auto", manual_only_when={"changed_lines_over": 1000}),
            [],
        )
        self.assertTrue(by_size)

        by_label, _ = gates.needs_manual_approval(
            snapshot(labels=["risky"]),
            config(mode="auto", manual_only_when={"pr_has_labels": ["risky"]}),
            [],
        )
        self.assertTrue(by_label)

        by_mode, _ = gates.needs_manual_approval(snapshot(), config(mode="manual"), [])
        self.assertTrue(by_mode)


if __name__ == "__main__":
    unittest.main()
