"""Filling in merge history that happened before this tool existed.

Never runs on its own. Reviewing is the tool's job; reaching back through a
repository's whole history is a thing the user asks for, once, knowing roughly
what it will cost — so everything here is driven by an explicit request and
reports its size before it starts.

Two deliberate differences from the merge records written during a tick:

* **Everything merged is recorded, not only what we reviewed.** The point of
  history is to answer "what has this person shipped since March", and filtering
  it to the handful of pull requests this tool happened to review would answer
  almost nothing.
* **No model calls.** A description costs a model call each, and a repository
  with two thousand merges would cost two thousand of them. Backfilled rows fall
  back to the pull request's own title, which is what its author wrote to
  describe it. Anything merged from here on still gets a written summary.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from . import log
from .gh import GitHubError, GraphQLClient
from .state import Store

PAGE_SIZE = 100

# What the interface and the CLI offer. Ordered cheapest first, which is also
# oldest-last, so the obvious choice is the top one.
RANGES: tuple[tuple[str, str, int | None], ...] = (
    ("yesterday", "Yesterday", 1),
    ("week", "Last 7 days", 7),
    ("month", "Last 30 days", 30),
    ("quarter", "Last 90 days", 90),
    ("year", "Last 12 months", 365),
    ("all", "Everything", None),
)

RANGE_LABELS = {key: label for key, label, _ in RANGES}
RANGE_DAYS = {key: days for key, _, days in RANGES}

# Above this many pull requests the interface asks a second time. Chosen so a
# normal catch-up goes through without ceremony and a whole-history sweep of a
# busy repository does not.
LARGE = 500


def parse_range(name: str) -> int | None:
    """Days back for a named range. None means everything."""
    if name not in RANGE_DAYS:
        raise ValueError(
            f"unknown range {name!r} — pick one of: {', '.join(RANGE_DAYS)}"
        )
    return RANGE_DAYS[name]


def since_epoch(days: int | None) -> float | None:
    return None if days is None else time.time() - days * 86400.0


def since_date(days: int | None) -> str | None:
    """The cutoff as ``YYYY-MM-DD``, which is what GitHub search wants."""
    if days is None:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return cutoff.strftime("%Y-%m-%d")


def _epoch(stamp: Any) -> float | None:
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


@dataclass
class Plan:
    """What a backfill would do, worked out before doing any of it."""

    repo: str
    owner: str
    name: str
    range_key: str
    days: int | None
    count: int | None
    """Merged pull requests in range, or None if GitHub would not say."""
    already_covered_since: float | None = None
    already_filed: int = 0

    @property
    def requests(self) -> int:
        """Roughly how many API calls this will take."""
        if self.count is None:
            return 0
        return max(1, -(-self.count // PAGE_SIZE))

    @property
    def is_large(self) -> bool:
        return self.count is not None and self.count >= LARGE

    def describe(self) -> str:
        span = RANGE_LABELS.get(self.range_key, self.range_key).lower()
        if self.count is None:
            return f"{self.repo}: {span} — size unknown until it runs"
        return (
            f"{self.repo}: {self.count:,} merged pull request(s) over {span}, "
            f"about {self.requests} request(s), no model calls"
        )


@dataclass
class Result:
    scanned: int = 0
    filed: int = 0
    skipped: int = 0
    """Already on record — a previous backfill, or a merge we saw ourselves."""
    pages: int = 0
    stopped_early: bool = False
    errors: list[str] = field(default_factory=list)


def plan(
    graphql: GraphQLClient, store: Store, repo: str, range_key: str
) -> Plan:
    """Work out the size of a backfill without fetching any of it."""
    owner, _, name = repo.partition("/")
    days = parse_range(range_key)
    coverage = store.backfill_coverage(repo) or {}
    return Plan(
        repo=repo,
        owner=owner,
        name=name,
        range_key=range_key,
        days=days,
        count=graphql.merged_count(owner, name, since_date(days)),
        already_covered_since=coverage.get("covered_since"),
        already_filed=int(coverage.get("filed") or 0),
    )


def run(
    graphql: GraphQLClient,
    store: Store,
    plan: Plan,
    *,
    identity: str | None = None,
    progress: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> Result:
    """Walk the merged pull requests and file them.

    Committed a page at a time rather than at the end, so a sweep that is
    interrupted — the window closed, the process killed — keeps everything it
    had already fetched instead of throwing the lot away.
    """
    result = Result()
    cutoff = since_epoch(plan.days)
    cursor: str | None = None
    me = (identity or "").lower()
    report = progress or (lambda _filed, _scanned: None)
    stop = should_stop or (lambda: False)

    while True:
        if stop():
            result.stopped_early = True
            break
        try:
            rows, cursor = graphql.merged_page(
                plan.owner, plan.name, cursor, PAGE_SIZE
            )
        except GitHubError as exc:
            result.errors.append(str(exc))
            log.get().warning("%s: backfill stopped: %s", plan.repo, exc)
            break

        result.pages += 1
        exhausted = False

        for row in rows:
            merged = _epoch(row.get("merged_at"))
            updated = _epoch(row.get("updated_at"))

            # Pages arrive newest-updated first, and a merged pull request is
            # always updated at or after its merge, so once updates predate the
            # cutoff nothing further back can belong in the range.
            if cutoff is not None and updated is not None and updated < cutoff:
                exhausted = True
                break
            result.scanned += 1
            if merged is None or (cutoff is not None and merged < cutoff):
                continue

            number = row.get("number")
            if not isinstance(number, int):
                continue
            if store.is_merge_recorded(plan.repo, number):
                result.skipped += 1
                continue

            # Where we did review it, its stats come along. Most backfilled rows
            # have none, and read as merges we simply were not part of.
            tally = store.review_tally(plan.repo, number)
            author = row.get("author") or "ghost"
            filed = store.record_merged(
                {
                    "repo": plan.repo,
                    "pr_number": number,
                    "title": row.get("title") or "",
                    "author": author,
                    "url": row.get("url") or "",
                    "base_ref": row.get("base_ref") or "",
                    "labels": row.get("labels") or [],
                    "is_ours": int(author.lower() == me) if me else 0,
                    "additions": row.get("additions") or 0,
                    "deletions": row.get("deletions") or 0,
                    "changed_files": row.get("changed_files") or 0,
                    "opened_at": _epoch(row.get("created_at")),
                    "merged_at": merged,
                    "merged_by": row.get("merged_by") or "",
                    "our_reviews": tally["rounds"],
                    "our_comments": tally["comments"],
                    "our_blockers": tally["blockers"],
                    "last_event": tally["last_event"],
                    # The author's own one-line account of the change. Marked as
                    # a title so the interface does not present it as a summary
                    # this tool wrote.
                    "description": (row.get("title") or "").strip() or None,
                    "description_source": "title",
                    # Already answered, so the tick that retries missing
                    # descriptions leaves these alone rather than buying a model
                    # call for every one.
                    "description_tries": 99,
                    # Not something we watched land. Without this the row would
                    # count as "merged during this run" purely because that is
                    # when it was written down.
                    "backfilled": 1,
                }
            )
            if filed:
                result.filed += 1
            else:
                result.skipped += 1

        report(result.filed, result.scanned)
        if exhausted or cursor is None:
            break

    store.record_backfill(plan.repo, cutoff, result.filed, result.scanned)
    return result


class Runner:
    """Drives a backfill from the interface, off the event loop.

    Textual's loop must never block, and every step here talks to GitHub, so the
    work happens on its own thread with its own database connection — SQLite
    connections belong to the thread that opened them, and the reviewer's is
    busy elsewhere. The interface polls ``status()`` on the timer it already
    runs and never touches anything this writes directly.

    The pause between estimating and fetching is the point of the whole class:
    the size of a sweep is only known after asking GitHub, and it should be the
    user who decides whether that size is acceptable.
    """

    def __init__(
        self,
        open_store: Callable[[], Store],
        graphql: GraphQLClient,
        repos: list[Any],
    ) -> None:
        self.open_store = open_store
        self.graphql = graphql
        self.repos = repos
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._go = threading.Event()
        self._stop = threading.Event()
        self._state: dict[str, Any] = {"phase": "idle"}

    # ------------------------------------------------------------- reading

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _set(self, **fields: Any) -> None:
        with self._lock:
            self._state.update(fields)

    # ------------------------------------------------------------- driving

    def start(self, range_key: str) -> bool:
        """Begin estimating. False if one is already under way."""
        if self.busy:
            return False
        self._go.clear()
        self._stop.clear()
        with self._lock:
            self._state = {
                "phase": "estimating",
                "range_key": range_key,
                "label": RANGE_LABELS.get(range_key, range_key),
                "total": None,
                "requests": 0,
                "filed": 0,
                "scanned": 0,
                "lines": [],
                "message": "",
            }
        self._thread = threading.Thread(
            target=self._work, args=(range_key,), name="backfill", daemon=True
        )
        self._thread.start()
        return True

    def confirm(self) -> None:
        self._go.set()

    def cancel(self) -> None:
        """Decline, or stop one that is already running.

        A running sweep stops between pages and keeps everything it committed.
        """
        self._stop.set()
        self._go.set()

    def dismiss(self) -> None:
        """Clear a finished run so the interface stops reporting it."""
        if not self.busy:
            self._set(phase="idle")

    # -------------------------------------------------------------- worker

    def _work(self, range_key: str) -> None:
        store = self.open_store()
        try:
            plans = []
            lines = []
            total = 0
            requests = 0
            for cfg in self.repos:
                item = plan(self.graphql, store, cfg.repo, range_key)
                plans.append((cfg, item))
                lines.append(item.describe())
                requests += item.requests
                if item.count is not None:
                    total += item.count

            self._set(
                phase="confirm", total=total, requests=requests, lines=lines
            )

            # Wait for an answer. Polled rather than blocked outright so a quit
            # while the question is on screen does not leave a thread behind.
            while not self._go.wait(0.2):
                pass
            if self._stop.is_set():
                self._set(phase="idle")
                return
            if total == 0:
                self._set(phase="done", message="already up to date")
                return

            self._set(phase="running")
            filed = 0
            for cfg, item in plans:
                if self._stop.is_set():
                    break

                def progress(f: int, s: int, base: int = filed) -> None:
                    self._set(filed=base + f, scanned=s)

                result = run(
                    self.graphql,
                    store,
                    item,
                    identity=getattr(cfg, "identity", None),
                    progress=progress,
                    should_stop=self._stop.is_set,
                )
                filed += result.filed
                self._set(filed=filed)
                for message in result.errors:
                    log.get().warning("%s: backfill: %s", cfg.repo, message)

            stopped = " (stopped)" if self._stop.is_set() else ""
            self._set(
                phase="done", message=f"{filed:,} added to the history{stopped}"
            )
            log.get().info("backfill finished — %d added to the history", filed)
        except Exception as exc:  # noqa: BLE001 - a dead thread must not hang the UI
            log.get().exception("backfill failed: %s", exc)
            self._set(phase="error", message=str(exc)[:200])
        finally:
            store.close()
