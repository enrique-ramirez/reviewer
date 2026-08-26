"""Eligibility and re-review triggers.

Everything here is cheap: it runs against a snapshot that has already been
fetched, and decides whether to spend a model call. Each decision carries a
reason string so the log says *why* a PR was skipped rather than going quiet.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Any

from .config import RepoConfig
from .gh.graphql import PRSnapshot
from .state import PRState

PASSING_CONCLUSIONS = {"SUCCESS"}
PENDING_STATUSES = {"QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "REQUESTED"}


@dataclass
class Decision:
    should_review: bool
    reason: str
    trigger: str = ""

    def __bool__(self) -> bool:
        return self.should_review


def _skip(reason: str) -> Decision:
    return Decision(False, reason)


def _go(reason: str, trigger: str) -> Decision:
    return Decision(True, reason, trigger)


def ci_status(snapshot: PRSnapshot, cfg: RepoConfig) -> tuple[str, list[str]]:
    """Reduce the check rollup to ``green`` / ``pending`` / ``failing`` / ``unknown``.

    The rollup covers check runs and commit statuses. Branch-protection rules —
    including "an approving review is required" — are not check runs and never
    appear here, so waiting on CI never means waiting on ourselves.

    ``unknown`` means the token cannot read the rollup at all. That is
    deliberately not folded into ``green``: an empty context list means a
    repository without CI, which is fine to review, while an unreadable rollup
    means we would be approving blind.
    """
    gates = cfg.gates

    if not snapshot.checks.accessible:
        return "unknown", []
    ignore = {n.lower() for n in gates.get("ignore_checks", [])}
    neutral_ok = gates.get("treat_neutral_as_pass", True)
    skipped_ok = gates.get("treat_skipped_as_pass", True)

    passing = set(PASSING_CONCLUSIONS)
    if neutral_ok:
        passing.add("NEUTRAL")
    if skipped_ok:
        passing.add("SKIPPED")

    failing: list[str] = []
    pending: list[str] = []

    for ctx in snapshot.checks.contexts:
        name = (ctx.get("name") or "").strip()
        if name.lower() in ignore:
            continue
        status = (ctx.get("status") or "").upper()
        conclusion = (ctx.get("conclusion") or "").upper()

        if status in PENDING_STATUSES or (not conclusion and status != "COMPLETED"):
            pending.append(name)
        elif conclusion in passing:
            continue
        else:
            failing.append(f"{name}={conclusion or status or 'UNKNOWN'}")

    if failing:
        return "failing", failing
    if pending:
        return "pending", pending
    if not snapshot.checks.contexts:
        # No checks configured at all. Treated as green — a repo without CI
        # should not be a repo the reviewer refuses to look at.
        return "green", []
    return "green", []


def evaluate(
    snapshot: PRSnapshot,
    cfg: RepoConfig,
    pr_state: PRState,
    *,
    has_new_comments: bool,
) -> Decision:
    """Decide whether this PR earns a model call right now."""
    gates = cfg.gates
    identity = cfg.identity

    # --- unconditional skips -------------------------------------------------

    if gates.get("skip_drafts", True) and snapshot.is_draft:
        return _skip("draft")

    if gates.get("skip_own_prs", True) and identity:
        if snapshot.author.lower() == identity.lower():
            return _skip("authored by us")

    blocking = {label.lower() for label in gates.get("blocking_labels", [])}
    if blocking:
        present = [lbl for lbl in snapshot.labels if lbl.lower() in blocking]
        if present:
            return _skip(f"blocking label: {', '.join(present)}")

    required = {label.lower() for label in gates.get("required_labels", [])}
    if required and not any(lbl.lower() in required for lbl in snapshot.labels):
        return _skip("missing a required label")

    bases = gates.get("base_branches") or []
    if bases and snapshot.base_ref not in bases:
        return _skip(f"base branch {snapshot.base_ref} is out of scope")

    # --- an explicit re-review request outranks the rest ---------------------
    #
    # GitHub removes us from the requested-reviewer list when we submit a
    # review, and puts us back when someone clicks "Re-request review". Standing
    # presence in that list is therefore the signal that a review is wanted, and
    # it is deliberately checked before the approved-by-others gate: if you
    # asked for it, you get it.

    explicitly_requested = snapshot.review_requested_from(identity)

    if not explicitly_requested:
        if gates.get("only_if_review_requested", False):
            return _skip("we are not a requested reviewer")
        if gates.get("skip_if_approved_by_others", True) and snapshot.approved_by_others(
            identity
        ):
            return _skip("already approved by someone else")

    # --- CI -----------------------------------------------------------------

    if gates.get("require_ci_green", True):
        status, detail = ci_status(snapshot, cfg)
        if status == "unknown":
            return _skip(
                "cannot read CI status — the token needs 'Checks: Read-only' and "
                "'Commit statuses: Read-only'. Add them, or set "
                "gates.require_ci_green to false to review without checking CI"
            )
        if status == "failing":
            return _skip(f"CI failing: {', '.join(detail[:4])}")
        if status == "pending":
            return _skip(f"CI still running: {', '.join(detail[:4])}")

    # --- has anything changed since last time? ------------------------------

    already_reviewed_this_sha = (
        pr_state.last_reviewed_head_sha == snapshot.head_sha and snapshot.head_sha
    )

    if explicitly_requested:
        return _go("re-review requested", "review_requested")

    if not already_reviewed_this_sha:
        if pr_state.last_reviewed_head_sha is None:
            return _go("not reviewed yet", "first_review")
        return _go("new commits since last review", "new_commits")

    if has_new_comments:
        return _go("new comments since last scan", "new_comments")

    return _skip("nothing changed since last review")


def needs_manual_approval(
    snapshot: PRSnapshot, cfg: RepoConfig, changed_paths: list[str]
) -> tuple[bool, str]:
    """Check the conditions that hold an approval back for a human.

    Returns ``(True, reason)`` when this PR should wait for you even though the
    review itself found nothing to block on.
    """
    approval = cfg.approval
    if approval.get("mode") == "manual":
        return True, "approval.mode is manual for this repo"

    rules: dict[str, Any] = approval.get("manual_only_when") or {}

    limit = rules.get("changed_lines_over")
    if limit is not None:
        total = snapshot.additions + snapshot.deletions
        if total > int(limit):
            return True, f"{total} changed lines is over the {limit} line limit"

    patterns = rules.get("touches_paths") or []
    if patterns:
        hits = [
            path
            for path in changed_paths
            if any(fnmatch.fnmatch(path, pattern) for pattern in patterns)
        ]
        if hits:
            sample = ", ".join(sorted(hits)[:3])
            return True, f"touches paths held for manual approval: {sample}"

    labels = {lbl.lower() for lbl in (rules.get("pr_has_labels") or [])}
    if labels:
        present = [lbl for lbl in snapshot.labels if lbl.lower() in labels]
        if present:
            return True, f"carries label {', '.join(present)}"

    return False, ""
