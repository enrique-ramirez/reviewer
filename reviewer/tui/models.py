"""Immutable views of the rows the reviewer writes to SQLite.

The store speaks in ``dict[str, Any]``. Everything above this module speaks in
these types instead, so a missing column or a stale JSON blob is dealt with once,
here, rather than by every caller guessing at a ``.get``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

Row = Mapping[str, Any]


def _text(row: Row, key: str) -> str:
    return str(row.get(key) or "")


def _count(row: Row, key: str) -> int:
    try:
        return int(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _epoch(row: Row, key: str) -> float | None:
    value = row.get(key)
    try:
        return float(value) if value else None
    except (TypeError, ValueError):
        return None


def _strings(row: Row, key: str) -> tuple[str, ...]:
    value = row.get(key)
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if item)


def key_of(row: Row) -> str:
    return f"{_text(row, 'repo')}#{_count(row, 'pr_number')}"


def short_name(repo: str) -> str:
    """The repository without its owner, which every row shares anyway."""
    _, _, name = repo.partition("/")
    return name or repo


def owner_of(repo: str) -> str:
    owner, _, _ = repo.partition("/")
    return owner


@dataclass(frozen=True, slots=True)
class Activity:
    """Work the reviewer is doing on a pull request right now."""

    phase: str
    started_at: float | None = None

    @property
    def is_replying(self) -> bool:
        return self.phase == "replying"

    def running_for(self, now: float) -> float:
        """Seconds spent so far. Zero when an older build recorded no start."""
        return max(0.0, now - self.started_at) if self.started_at else 0.0

    @classmethod
    def from_row(cls, row: Row | None) -> "Activity | None":
        if not row or not row.get("phase"):
            return None
        return cls(phase=str(row["phase"]), started_at=_epoch(row, "started_at"))


@dataclass(frozen=True, slots=True)
class Review:
    author: str
    state: str


def _reviews(row: Row) -> tuple[Review, ...]:
    value = row.get("reviews")
    if not isinstance(value, list):
        return ()
    return tuple(
        Review(author=str(item.get("author") or ""), state=str(item.get("state") or ""))
        for item in value
        if isinstance(item, dict) and item.get("author")
    )


@dataclass(frozen=True, slots=True)
class PullRequest:
    """One open pull request, as the board knows it."""

    repo: str
    number: int
    title: str
    author: str
    url: str
    is_ours: bool
    is_draft: bool
    opened_at: float | None
    seen_at: float
    head_sha: str
    base_ref: str
    additions: int
    deletions: int
    changed_files: int
    labels: tuple[str, ...]
    review_decision: str
    reviews: tuple[Review, ...]
    requested_reviewers: tuple[str, ...]
    mergeable: str
    ci_state: str
    open_threads: int
    threads_awaiting_us: int
    capped_threads: int
    needs_human: bool
    needs_human_reason: str
    last_action: str
    activity: Activity | None = None
    reviewed_by_us: bool = False
    """Whether we have ever posted a review on it, from the event log."""

    @property
    def key(self) -> str:
        return f"{self.repo}#{self.number}"

    @property
    def is_approved(self) -> bool:
        return self.review_decision == "APPROVED"

    @property
    def wants_changes(self) -> bool:
        return self.review_decision == "CHANGES_REQUESTED"

    @classmethod
    def from_row(
        cls,
        row: Row,
        *,
        activity: Activity | None = None,
        reviewed: bool = False,
    ) -> "PullRequest":
        return cls(
            repo=_text(row, "repo"),
            number=_count(row, "pr_number"),
            title=_text(row, "title"),
            author=_text(row, "author"),
            url=_text(row, "url"),
            is_ours=bool(row.get("is_ours")),
            is_draft=bool(row.get("is_draft")),
            opened_at=_epoch(row, "opened_at"),
            seen_at=_epoch(row, "seen_at") or 0.0,
            head_sha=_text(row, "head_sha"),
            base_ref=_text(row, "base_ref"),
            additions=_count(row, "additions"),
            deletions=_count(row, "deletions"),
            changed_files=_count(row, "changed_files"),
            labels=_strings(row, "labels"),
            review_decision=_text(row, "review_decision").upper(),
            reviews=_reviews(row),
            requested_reviewers=_strings(row, "requested_reviewers"),
            mergeable=(_text(row, "mergeable") or "UNKNOWN").upper(),
            ci_state=_text(row, "ci_state") or "unknown",
            open_threads=_count(row, "open_threads"),
            threads_awaiting_us=_count(row, "threads_awaiting_us"),
            capped_threads=_count(row, "capped_threads"),
            needs_human=bool(row.get("needs_human")),
            needs_human_reason=_text(row, "needs_human_reason"),
            last_action=_text(row, "last_action"),
            activity=activity,
            reviewed_by_us=reviewed,
        )


@dataclass(frozen=True, slots=True)
class Merge:
    """One pull request that landed, as the Summary and History tabs know it."""

    repo: str
    number: int
    title: str
    author: str
    url: str
    base_ref: str
    labels: tuple[str, ...]
    is_ours: bool
    additions: int
    deletions: int
    changed_files: int
    opened_at: float | None
    merged_at: float | None
    merged_by: str
    recorded_at: float
    our_reviews: int
    our_comments: int
    our_blockers: int
    last_event: str
    description: str
    description_source: str

    @property
    def key(self) -> str:
        return f"{self.repo}#{self.number}"

    @property
    def short_repo(self) -> str:
        return short_name(self.repo)

    @property
    def landed_at(self) -> float:
        """When it merged, falling back to when we noticed."""
        return self.merged_at or self.recorded_at or 0.0

    @property
    def open_for(self) -> float | None:
        if self.opened_at and self.merged_at:
            return self.merged_at - self.opened_at
        return None

    @property
    def described_by_model(self) -> bool:
        return self.description_source == "model"

    @property
    def reviewed_by_us(self) -> bool:
        return bool(self.our_reviews or self.our_comments)

    @classmethod
    def from_row(cls, row: Row) -> "Merge":
        return cls(
            repo=_text(row, "repo"),
            number=_count(row, "pr_number"),
            title=_text(row, "title"),
            author=_text(row, "author"),
            url=_text(row, "url"),
            base_ref=_text(row, "base_ref"),
            labels=_strings(row, "labels"),
            is_ours=bool(row.get("is_ours")),
            additions=_count(row, "additions"),
            deletions=_count(row, "deletions"),
            changed_files=_count(row, "changed_files"),
            opened_at=_epoch(row, "opened_at"),
            merged_at=_epoch(row, "merged_at"),
            merged_by=_text(row, "merged_by"),
            recorded_at=_epoch(row, "recorded_at") or 0.0,
            our_reviews=_count(row, "our_reviews"),
            our_comments=_count(row, "our_comments"),
            our_blockers=_count(row, "our_blockers"),
            last_event=_text(row, "last_event"),
            description=_text(row, "description"),
            description_source=_text(row, "description_source"),
        )
