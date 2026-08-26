"""The only place the dashboard talks to the store.

Rows go in, immutable models come out; every view above this reads models.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..state import Store
from .models import Activity, Merge, PullRequest, ReviewCost, key_of
from .session import Session

PAGE_SIZE = 25
"""Rows per page when the interface has not said how much room it has."""

MIN_PAGE_SIZE = 5
"""Below this a page is more turning than reading, so a tiny window pages less."""


@dataclass(frozen=True, slots=True)
class MergePage:
    """One page of merged pull requests, and how many there were in total."""

    merges: tuple[Merge, ...]
    total: int
    number: int = 0
    size: int = PAGE_SIZE

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.size))


def open_pull_requests(store: Store, repos: tuple[str, ...]) -> tuple[PullRequest, ...]:
    """Every open pull request across every watched repository, unscoped.

    Unscoped on purpose: the sidebar counts what is waiting in the repositories
    you are *not* looking at, which is most of the reason to have one.
    """
    in_flight = store.active_reviews(list(repos))
    reviewed = store.reviewed_pull_requests(list(repos))
    # One query for every row's most recent pass, rather than one per row: the
    # board redraws on a timer.
    last_pass = store.latest_review_events(list(repos))
    return tuple(
        PullRequest.from_row(
            row,
            activity=Activity.from_row(in_flight.get(key_of(row))),
            reviewed=key_of(row) in reviewed,
            cost=ReviewCost.from_row(
                last_pass.get((row.get("repo"), row.get("pr_number")))
            ),
        )
        for row in store.list_pr_view(list(repos))
    )


def merges_this_run(store: Store, session: Session) -> MergePage:
    """What landed since the run started, unfiltered and unpaged."""
    scope = list(session.scope)
    return MergePage(
        merges=tuple(
            Merge.from_row(row)
            for row in store.list_merged(
                scope, since=session.started_at, live_only=True, limit=PAGE_SIZE
            )
        ),
        total=store.count_merged(scope, since=session.started_at, live_only=True),
    )


def merge_history(
    store: Store, session: Session, now: float, size: int = PAGE_SIZE
) -> MergePage:
    """Everything on record, filtered and paged, with the page clamped in range.

    ``size`` is however many rows the table can actually show. A fixed page on a
    tall terminal means half a screen of history and half a screen of nothing,
    and on a short one it means rows that are fetched and then cut off.
    """
    size = max(MIN_PAGE_SIZE, size)
    scope = list(session.scope)
    after = session.merged_after(now)
    total = store.count_merged(scope, author=session.author, merged_after=after)
    number = min(session.page, max(0, -(-total // size) - 1))

    rows = store.list_merged(
        scope,
        author=session.author,
        merged_after=after,
        limit=size,
        offset=number * size,
    )
    return MergePage(
        merges=tuple(Merge.from_row(row) for row in rows),
        total=total,
        number=number,
        size=size,
    )
