"""Posting to GitHub.

Nothing the model produced reaches GitHub without passing through here first.
The important part is line validation: GitHub rejects a review comment whose line
is not part of the diff, and it rejects the *entire review* along with it — one
bad line number and a good review is lost. So every proposed comment is checked
against the real patch, and anything that does not fit is demoted into the
summary rather than dropped or guessed at.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import log, render
from .config import RepoConfig
from .diff import DiffBundle, commentable_lines
from .gh import GitHubError, GraphQLClient, RestClient
from .render import Finding

EVENT_APPROVE = "APPROVE"
EVENT_COMMENT = "COMMENT"
EVENT_REQUEST_CHANGES = "REQUEST_CHANGES"


@dataclass
class PlacedComments:
    inline: list[dict[str, Any]] = field(default_factory=list)
    unplaceable: list[Finding] = field(default_factory=list)


def place_comments(findings: list[Finding], bundle: DiffBundle) -> PlacedComments:
    """Map findings onto lines GitHub will accept.

    A finding whose line is not in the diff becomes a bullet in the summary
    instead. That keeps the content and loses only the pin, which is much better
    than a 422 that discards the whole review.
    """
    valid_by_path: dict[str, dict[str, set[int]]] = {}
    for file in bundle.files:
        valid_by_path[file.path] = commentable_lines(file.patch)

    placed = PlacedComments()

    for finding in findings:
        if not finding.path or finding.line is None:
            placed.unplaceable.append(finding)
            continue

        valid = valid_by_path.get(finding.path)
        if valid is None:
            placed.unplaceable.append(finding)
            continue

        side = finding.side if finding.side in valid else "RIGHT"
        if finding.line not in valid[side]:
            other = "LEFT" if side == "RIGHT" else "RIGHT"
            if finding.line in valid.get(other, set()):
                side = other
            else:
                log.get().debug(
                    "finding on %s:%s is not in the diff, demoting to summary",
                    finding.path,
                    finding.line,
                )
                placed.unplaceable.append(finding)
                continue

        placed.inline.append(
            {
                "path": finding.path,
                "line": finding.line,
                "side": side,
                "body": render.inline_comment_body(finding),
            }
        )

    return placed


def unplaceable_section(findings: list[Finding]) -> str:
    if not findings:
        return ""
    lines = ["", "#### Notes that did not land on a diff line", ""]
    for finding in findings:
        label = render.SEVERITY_LABEL.get(finding.severity, "Note")
        where = finding.path or "general"
        if finding.line:
            where += f":{finding.line}"
        lines.append(f"- **{label}** · `{where}` — {finding.human}")
    return "\n".join(lines)


def decide_event(findings: list[Finding], cfg: RepoConfig) -> tuple[str, bool]:
    """Choose the review event.

    Returns ``(event, invite_wave_off)``.
    """
    has_blocker = any(f.severity == "blocker" for f in findings)
    if has_blocker:
        return EVENT_REQUEST_CHANGES, False

    if not findings:
        return EVENT_APPROVE, False

    action = cfg.approval.get("no_blocker_action", "comment_and_invite")
    if action == "approve_with_notes":
        return EVENT_APPROVE, False
    if action == "comment_only":
        return EVENT_COMMENT, False
    return EVENT_COMMENT, True


def submit_review(
    rest: RestClient,
    cfg: RepoConfig,
    number: int,
    head_sha: str,
    *,
    event: str,
    body: str,
    comments: list[dict[str, Any]],
) -> bool:
    """Submit a review, degrading gracefully rather than losing it.

    If GitHub still rejects the comment set — its position rules have edge cases
    around renames and mode changes that a patch parser cannot fully predict —
    the review is resubmitted with the findings folded into the body. A review
    that reads slightly worse beats a review that never posted.
    """
    try:
        rest.create_review(
            cfg.owner,
            cfg.name,
            number,
            commit_id=head_sha,
            event=event,
            body=body,
            comments=comments,
        )
        return True
    except GitHubError as exc:
        if exc.status != 422 or not comments:
            log.get().error("could not post review on #%s: %s", number, exc)
            return False

        log.get().warning(
            "GitHub rejected the inline comments on #%s (%s); "
            "resubmitting with them inline in the summary",
            number,
            exc,
        )
        fallback = body + "\n\n#### Findings\n\n" + "\n\n".join(
            f"**`{c['path']}:{c['line']}`**\n\n{c['body']}" for c in comments
        )
        try:
            rest.create_review(
                cfg.owner,
                cfg.name,
                number,
                commit_id=head_sha,
                event=event,
                body=fallback,
                comments=None,
            )
            return True
        except GitHubError as inner:
            log.get().error("fallback review also failed on #%s: %s", number, inner)
            return False


def resolve_threads(
    graphql: GraphQLClient, thread_ids: list[str]
) -> tuple[int, int]:
    """Resolve conversations. Returns ``(resolved, failed)``.

    Worth remembering: resolving a thread does not unblock the merge button. A
    ``REQUEST_CHANGES`` review blocks until the same reviewer submits a new one,
    so the unblock always comes from a later ``APPROVE``. Resolution is signal to
    the author, not a merge gate.
    """
    resolved = 0
    failed = 0
    for thread_id in thread_ids:
        if graphql.resolve_thread(thread_id):
            resolved += 1
        else:
            failed += 1
    return resolved, failed
