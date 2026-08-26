"""The Dashboard tab: what is open right now, and what wants a human."""

from __future__ import annotations

from typing import Any, Sequence

from rich.text import Text

from .. import formatting, prose, status, theme
from ..models import PullRequest
from ..session import Session
from ..widgets import Cells, Column
from .base import RecordView

BOARD_COLUMNS = (
    Column("", 3),
    Column("PR", 7),
    Column("Status", 16),
    Column("Size", 12),
    Column("Open", 5),
    Column("Author", 20),
    Column("Title"),
)
STATUS_COLUMN = 2

AUTHOR_WIDTH = 20
SHA_WIDTH = 8


def in_view(
    pull_requests: Sequence[PullRequest], session: Session
) -> tuple[PullRequest, ...]:
    """The scoped, filtered, most-urgent-first order the board is read in."""
    scope = set(session.scope)
    chosen = [pr for pr in pull_requests if pr.repo in scope]
    if session.only_attention:
        chosen = [pr for pr in chosen if status.attention(pr)]
    return tuple(sorted(chosen, key=lambda pr: (status.rank(pr), -pr.number)))


def attention_by_repo(pull_requests: Sequence[PullRequest]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for pull_request in pull_requests:
        if status.attention(pull_request):
            counts[pull_request.repo] = counts.get(pull_request.repo, 0) + 1
    return counts


def busy_repos(pull_requests: Sequence[PullRequest]) -> frozenset[str]:
    return frozenset(pr.repo for pr in pull_requests if pr.activity)


def longest_running(pull_requests: Sequence[PullRequest], now: float) -> float:
    spans = [pr.activity.running_for(now) for pr in pull_requests if pr.activity]
    return max(spans, default=0.0)


def subtitle(pull_requests: Sequence[PullRequest], session: Session) -> str:
    flagged = sum(1 for pr in pull_requests if status.attention(pr))
    waiting = f" — {flagged} need you" if flagged else ""
    filtered = "  [filtered]" if session.only_attention else ""
    return f"{len(pull_requests)} open{waiting}{filtered}"


def work_in_flight(pull_requests: Sequence[PullRequest], now: float) -> tuple[Text, ...]:
    """Named work under way, for the quit confirmation."""
    return tuple(
        prose.join(
            prose.span(
                f"  {'replying to' if pr.activity.is_replying else 'reviewing'} "
                f"{pr.key}",
                theme.LIVE,
            ),
            prose.span(
                f"   {formatting.elapsed(pr.activity.running_for(now))}", theme.MUTED
            ),
        )
        for pr in pull_requests
        if pr.activity
    )


def legend() -> Text:
    text = Text("  ")
    for flag in theme.FLAGS:
        text.append(flag.glyph, style=flag.style)
        text.append(f" {flag.label}   ", style=theme.MUTED)
    return text


def live_status(pull_request: PullRequest, now: float, frame: int) -> str:
    """The status column while work is in flight, kept inside its 16 cells."""
    label = status.status_of(pull_request).text
    running = formatting.elapsed_brief(pull_request.activity.running_for(now))
    return f"{theme.spinner_frame(frame)} {label} {running}"


def row_cells(pull_request: PullRequest, now: float, frame: int) -> Cells:
    flag = status.attention(pull_request)
    verdict = status.status_of(pull_request)
    label = (
        live_status(pull_request, now, frame)
        if pull_request.activity
        else verdict.text
    )
    return (
        Text(flag.glyph if flag else " ", style=flag.style if flag else ""),
        Text(f"#{pull_request.number}", style="bold" if flag else ""),
        Text(label, style=verdict.style),
        Text(
            formatting.churn(pull_request.additions, pull_request.deletions),
            style=theme.MUTED,
        ),
        Text(
            formatting.age_label(pull_request.opened_at, now),
            style=formatting.age_style(pull_request.opened_at, now),
        ),
        Text(
            formatting.clip(pull_request.author, AUTHOR_WIDTH),
            style=theme.KEY if pull_request.is_ours else theme.MUTED,
        ),
        Text(formatting.one_line(pull_request.title)),
    )


def _byline(pull_request: PullRequest) -> Text:
    byline = prose.span(f"by @{pull_request.author}", theme.MUTED)
    if pull_request.is_ours:
        byline.append("  (yours)", style=theme.KEY)
    return byline


def _activity_line(pull_request: PullRequest, now: float, frame: int) -> Text | None:
    activity = pull_request.activity
    if activity is None:
        return None
    verb = "replying to threads" if activity.is_replying else "reviewing"
    running = formatting.elapsed(activity.running_for(now))
    return prose.join(
        prose.line(
            f"{theme.spinner_frame(frame)} {verb} right now — {running}", theme.LIVE
        ),
        prose.blank(),
    )


def _flag_lines(pull_request: PullRequest) -> Text | None:
    flag = status.attention(pull_request)
    if flag is None:
        return None
    reason = (
        prose.line(f"   {pull_request.needs_human_reason}", theme.NEEDS_YOU)
        if flag is theme.APPROVAL and pull_request.needs_human_reason
        else None
    )
    return prose.join(
        prose.line(f"{flag.glyph} {flag.detail}", flag.style), reason, prose.blank()
    )


def _open_for(pull_request: PullRequest, now: float) -> Text | None:
    opened = pull_request.opened_at
    if not opened:
        return None
    return prose.field(
        "open for",
        f"{formatting.duration(now - opened)}  (since {formatting.timestamp(opened)})",
        formatting.age_style(opened, now),
    )


def _reviewers(pull_request: PullRequest) -> Text | None:
    peers = status.peer_reviews(pull_request)
    if peers:
        return prose.field(
            "reviewers", ", ".join(status.describe_review(r) for r in peers)
        )
    if pull_request.requested_reviewers:
        return prose.field(
            "requested",
            ", ".join(f"@{name}" for name in pull_request.requested_reviewers),
            theme.MUTED,
        )
    if pull_request.is_ours:
        return prose.field("reviewers", "nobody asked yet", theme.MUTED)
    return None


def _threads(pull_request: PullRequest) -> Text | None:
    if not pull_request.open_threads:
        return None
    parts = [f"{pull_request.open_threads} open"]
    if pull_request.threads_awaiting_us:
        parts.append(f"{pull_request.threads_awaiting_us} awaiting our reply")
    if pull_request.capped_threads:
        parts.append(f"{pull_request.capped_threads} parked")
    # A parked thread is waiting on a human to settle it.
    style = theme.NEEDS_YOU if pull_request.capped_threads else ""
    return prose.field("threads", ", ".join(parts), style)


def _last_pass(pull_request: PullRequest) -> Text | None:
    if not status.reports_last_pass(pull_request):
        return None
    return prose.join(
        prose.line("\nlast pass", theme.MUTED),
        prose.line(f"  {pull_request.last_action or 'not looked at yet'}"),
    )


def detail_text(pull_request: PullRequest, now: float, frame: int) -> Text:
    verdict = status.status_of(pull_request)
    merge = status.merge_state(pull_request)
    ci = status.ci_state(pull_request)
    return prose.join(
        prose.headline(pull_request.number, pull_request.title, _byline(pull_request)),
        _activity_line(pull_request, now, frame),
        _flag_lines(pull_request),
        prose.field("status", verdict.text, verdict.style),
        _open_for(pull_request, now),
        prose.field(
            "branch",
            f"{pull_request.base_ref} ← {pull_request.head_sha[:SHA_WIDTH]}",
        ),
        prose.field(
            "size",
            f"{formatting.churn(pull_request.additions, pull_request.deletions)} "
            f"in {pull_request.changed_files} files",
        ),
        prose.field("merge", merge.text, merge.style),
        prose.field("ci", ci.text, ci.style),
        prose.field("labels", ", ".join(pull_request.labels))
        if pull_request.labels
        else None,
        _reviewers(pull_request),
        _threads(pull_request),
        _last_pass(pull_request),
        prose.span(f"\nseen {formatting.ago(now - pull_request.seen_at)}", theme.MUTED),
    )


class BoardView(RecordView):
    COLUMNS = BOARD_COLUMNS

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._now = 0.0
        self._frame = 0

    def show(self, records: Sequence[PullRequest], *, now: float, frame: int) -> None:
        self._now, self._frame = now, frame
        super().show(records)

    def animate(self, now: float, frame: int) -> None:
        """Advance the spinner without touching the database or the cursor."""
        self._now, self._frame = now, frame
        for pull_request in self._records:
            if pull_request.activity:
                self.table.refresh_cell(
                    pull_request.key,
                    row_cells(pull_request, now, frame),
                    STATUS_COLUMN,
                )
        current = self.current
        if current is not None and current.activity:
            self.redraw_detail()

    def row_cells(self, record: PullRequest) -> Cells:
        return row_cells(record, self._now, self._frame)

    def detail_text(self, record: PullRequest) -> Text:
        return detail_text(record, self._now, self._frame)

    def empty_text(self) -> Text:
        return prose.span("waiting for the first scan…", theme.MUTED)

    def status_text(self) -> Text:
        return legend()
