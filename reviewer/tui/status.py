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
    return Status(text, CI_STYLES.get(state, PENDING))


def peer_reviews(pull_request: PullRequest) -> tuple[Review, ...]:
    ours = pull_request.author.lower() if pull_request.is_ours else ""
    return tuple(
        review
        for review in pull_request.reviews
        if review.author and review.author.lower() != ours
    )


def attention(pull_request: PullRequest) -> Flag | None:
    ours_and_approved = pull_request.is_ours and pull_request.is_approved

    if pull_request.capped_threads:
        return theme.DISAGREEMENT
    if pull_request.needs_human:
        if pull_request.wants_changes:
            return theme.HELD
        return theme.APPROVAL
    if ours_and_approved and pull_request.mergeable == "CONFLICTING":
        return theme.CONFLICT
    if ours_and_approved and pull_request.mergeable == "MERGEABLE":
        return theme.MERGEABLE
    if ours_and_approved:
        return theme.MERGE_PENDING
    if pull_request.threads_awaiting_us and not pull_request.is_ours:
        return theme.REPLIES
    return None


def rank(pull_request: PullRequest) -> int:
    flag = attention(pull_request)
    return flag.rank if flag else theme.UNFLAGGED_RANK


def wants_you(pull_request: PullRequest) -> bool:
    flag = attention(pull_request)
    return bool(flag and flag.wants_you)


def peer_verdict(pull_request: PullRequest) -> Status:
    states = {review.state.upper() for review in peer_reviews(pull_request)}

    if "CHANGES_REQUESTED" in states:
        return Status("changes req.", URGENT)
    if "APPROVED" in states:
        return Status("needs 1 more", NEEDS_YOU)
    if states:
        return Status("needs sign-off", NEEDS_YOU)
    if pull_request.requested_reviewers:
        return Status("awaiting review", PENDING)
    return Status("no reviewer", PENDING)


def status_of(pull_request: PullRequest) -> Status:
    activity = pull_request.activity
    if activity is not None:
        if activity.is_stalled:
            return Status("quiet", URGENT)
        return Status("replying" if activity.is_replying else "reviewing", LIVE)
    if pull_request.is_draft:
        return Status("draft", PENDING)
    if pull_request.is_approved:
        return Status("approved", SETTLED)
    if pull_request.wants_changes:
        return Status("changes req.", URGENT)

    if pull_request.is_ours:
        return peer_verdict(pull_request)

    action = pull_request.last_action
    if action.startswith("REQUEST_CHANGES"):
        return Status("changes req.", URGENT)
    if action.startswith("APPROVE"):
        return Status("approved", SETTLED)
    if action.startswith("COMMENT"):
        return Status("reviewed", DONE)
    if action.startswith("skipped"):
        if pull_request.reviewed_by_us:
            return Status("reviewed", DONE)
        return Status("not reviewed", PENDING)
    return Status("awaiting", PENDING)


def reports_last_pass(pull_request: PullRequest) -> bool:
    return not (pull_request.is_ours and pull_request.last_action.startswith("skipped"))
