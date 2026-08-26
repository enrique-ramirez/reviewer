"""Row builders, so a test says only what it is actually about."""

from __future__ import annotations

from typing import Any

from reviewer.tui.models import Merge, PullRequest

NOW = 1_700_000_000.0
DAY = 86400.0


def pull_request(**overrides: Any) -> PullRequest:
    row: dict[str, Any] = {
        "repo": "acme/widgets",
        "pr_number": 42,
        "title": "Tidy the thing",
        "author": "someone",
        "url": "https://example.invalid/42",
        "seen_at": NOW,
        "opened_at": NOW - DAY,
        "base_ref": "main",
        "head_sha": "abcdef1234567890",
        "additions": 10,
        "deletions": 3,
        "changed_files": 2,
    }
    row.update(overrides)
    return PullRequest.from_row(
        row,
        activity=overrides.get("activity"),
        reviewed=bool(overrides.get("reviewed")),
    )


def merge(**overrides: Any) -> Merge:
    row: dict[str, Any] = {
        "repo": "acme/widgets",
        "pr_number": 7,
        "title": "Ship it",
        "author": "someone",
        "url": "https://example.invalid/7",
        "base_ref": "main",
        "merged_at": NOW - DAY,
        "opened_at": NOW - 3 * DAY,
        "recorded_at": NOW,
        "additions": 4,
        "deletions": 1,
        "changed_files": 1,
    }
    row.update(overrides)
    return Merge.from_row(row)
