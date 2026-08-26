"""What a pull request's state means, derived from the row and nothing else."""

from __future__ import annotations

from dataclasses import dataclass

from . import theme
from .models import PullRequest, Review
from .theme import DONE, LIVE, NEEDS_YOU, PENDING, SETTLED, URGENT, Flag


@dataclass(frozen=True, slots=True)
class Status:
    text: str
    style: str


REVIEW_STATES = {
    "APPROVED": "approved",
    "CHANGES_REQUESTED": "wants changes",
    "COMMENTED": "commented",
    "DISMISSED": "dismissed",
    "PENDING": "drafting",
}

CI_STYLES = {"green": SETTLED, "failing": URGENT, "pending": PENDING}

MERGE_STATES = {
    "MERGEABLE": Status("no conflicts", SETTLED),
    "CONFLICTING": Status("has conflicts", URGENT),
}
MERGE_UNKNOWN = Status("GitHub still computing", PENDING)


def describe_review(review: Review) -> str:
    return f"@{review.author} {REVIEW_STATES.get(review.state.upper(), 'reviewed')}"


def merge_state(pull_request: PullRequest) -> Status:
    return MERGE_STATES.get(pull_request.mergeable, MERGE_UNKNOWN)


def ci_state(pull_request: PullRequest) -> Status:
    state = pull_request.ci_state
    text = "not visible to this token" if state == "unknown" else state
    # A pending build is waiting, not asking anything of you.
    return Status(text, CI_STYLES.get(state, PENDING))


def peer_reviews(pull_request: PullRequest) -> tuple[Review, ...]:
    """Everyone's review except our own."""
    ours = pull_request.author.lower() if pull_request.is_ours else ""
    return tuple(
        review
        for review in pull_request.reviews
        if review.author and review.author.lower() != ours
    )


def attention(pull_request: PullRequest) -> Flag | None:
    """Why this pull request wants a human, most urgent first."""
    ours_and_approved = pull_request.is_ours and pull_request.is_approved

    if pull_request.capped_threads:
        return theme.DISAGREEMENT
    if pull_request.needs_human:
        return theme.APPROVAL
    if ours_and_approved and pull_request.mergeable == "CONFLICTING":
        return theme.CONFLICT
    if ours_and_approved and pull_request.mergeable == "MERGEABLE":
        return theme.MERGEABLE
    if ours_and_approved:
        # UNKNOWN means GitHub has not finished computing mergeability, and
        # calling that "ready to merge" would be a guess.
        return theme.MERGE_PENDING
    if pull_request.threads_awaiting_us and not pull_request.is_ours:
        return theme.REPLIES
    return None


def rank(pull_request: PullRequest) -> int:
    flag = attention(pull_request)
    return flag.rank if flag else theme.UNFLAGGED_RANK


def peer_verdict(pull_request: PullRequest) -> Status:
    """Where one of *our own* pull requests stands.

    The reviewer skips these, so its own verdict says nothing. What matters is
    whether anyone else has looked, and whether it has the approval it needs.
    """
    states = {review.state.upper() for review in peer_reviews(pull_request)}

    if "CHANGES_REQUESTED" in states:
        return Status("changes req.", URGENT)
    if "APPROVED" in states:
        # Approved, but reviewDecision is not — usually branch protection
        # wanting a second approval.
        return Status("needs 1 more", NEEDS_YOU)
    if states:
        # Reviewed without approving, which is what the reviewer itself does
        # when a change is clean but the approval is held back for a human.
        return Status("needs sign-off", NEEDS_YOU)
    if pull_request.requested_reviewers:
        return Status("awaiting review", PENDING)
    return Status("no reviewer", PENDING)


def status_of(pull_request: PullRequest) -> Status:
    """The one-phrase answer to "where is this", live work first."""
    activity = pull_request.activity
    if activity is not None:
        # No trailing ellipsis: the board prefixes a spinner, which says "still
        # going" better than punctuation does.
        return Status("replying" if activity.is_replying else "reviewing", LIVE)
    if pull_request.is_draft:
        return Status("draft", PENDING)
    if pull_request.is_approved:
        return Status("approved", SETTLED)
    if pull_request.wants_changes:
        return Status("changes req.", URGENT)

    # Before the last_action fallback, which for our own pull requests only
    # ever says "skipped: authored by us".
    if pull_request.is_ours:
        return peer_verdict(pull_request)

    action = pull_request.last_action
    if action.startswith("REQUEST_CHANGES"):
        return Status("changes req.", URGENT)
    if action.startswith("APPROVE"):
        return Status("approved", SETTLED)
    if action.startswith("COMMENT"):
        # We commented and the ball is with the author: nothing waits on this
        # reader, which is why it is not the colour of the statuses that do.
        return Status("reviewed", DONE)
    if action.startswith("skipped"):
        # A skip overwrites whatever the review said, so the action alone
        # cannot tell you whether we have an opinion here: "skipped: nothing
        # changed since last review" is the outcome of having reviewed it,
        # which is the opposite of what it reads as. The event log knows.
        if pull_request.reviewed_by_us:
            return Status("reviewed", DONE)
        return Status("not reviewed", PENDING)
    return Status("awaiting", PENDING)


def reports_last_pass(pull_request: PullRequest) -> bool:
    """Our own pull requests are skipped by design, so saying so is noise."""
    return not (pull_request.is_ours and pull_request.last_action.startswith("skipped"))
