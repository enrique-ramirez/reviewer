"""Reading a pull request's review conversation without leaving the terminal.

Automating the reviewing makes it easy to stop reading the reviews. The board
can say *2 findings* and the History pane can say *4 comments over 2 rounds*,
and neither tells you what was actually said — for that you had to open a
browser, which is the habit this tool exists to remove.

Fetched on demand rather than stored. Three reasons, and the third is the one
that settles it:

* Everything already on record — every pull request this tool reviewed before
  today, and all of backfilled history — would show nothing under a scheme that
  only kept what it wrote from now on.
* GitHub is the truth. A stored copy is a snapshot of what we posted, and says
  nothing about the reply that came after it.
* **Resolution happens elsewhere.** A thread the author settled in the browser
  is only knowable by asking, so asking is the design.

It is cheap to ask. The GraphQL snapshot is one request and already carries the
threads, and the REST reviews call is ETag-cached through the same database the
rest of the tool uses, so looking twice costs a ``304``.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from . import log
from .gh import GitHubError, GraphQLClient, RestClient
from .gh.graphql import PRSnapshot, ReviewThread
from .state import Store

MAX_BODY_CHARS = 4000

#: How many other people's submitted reviews to render in full. Ours are never
#: dropped — they are what this screen exists for. A busy repository with three
#: review bots can carry thirty of them, and a reader scrolling for ours through
#: all of that is back to using the browser.
MAX_OTHER_REVIEWS = 8


@dataclass(frozen=True)
class Review:
    """One submitted review — the summary someone left, not an inline note."""

    author: str
    state: str
    body: str
    submitted_at: str
    url: str
    ours: bool = False


@dataclass(frozen=True)
class Conversation:
    """Everything said about one pull request, as of when it was asked for."""

    repo: str
    number: int
    title: str = ""
    reviews: tuple[Review, ...] = ()
    threads: tuple[ReviewThread, ...] = ()
    identity: str = ""
    fetched_at: float = 0.0
    error: str = ""

    @property
    def key(self) -> tuple[str, int]:
        return (self.repo, self.number)

    @property
    def empty(self) -> bool:
        return not self.reviews and not self.threads

    @property
    def open_threads(self) -> int:
        return sum(1 for t in self.threads if not t.is_resolved)


def _clip(body: str) -> str:
    body = (body or "").strip()
    if len(body) <= MAX_BODY_CHARS:
        return body
    return body[:MAX_BODY_CHARS].rstrip() + "\n\n*…truncated. Open it on GitHub for the rest.*"


def _thread_heading(thread: ReviewThread) -> str:
    where = thread.path or "the pull request"
    line = thread.line or thread.original_line
    if line:
        where = f"{where}:{line}"
    marks = []
    if thread.is_resolved:
        marks.append("resolved")
    if thread.is_outdated:
        marks.append("outdated")
    return f"{where}" + (f" — {', '.join(marks)}" if marks else "")


def render(conversation: Conversation) -> str:
    """The conversation as markdown, for the viewer.

    Threads first. The inline notes are what a review actually said; the
    submitted body is the covering letter, and reads better after them.
    """
    if conversation.error:
        return f"# Could not read it\n\n{conversation.error}\n"
    if conversation.empty:
        return (
            "# Nothing said yet\n\n"
            "No reviews and no comment threads on this pull request.\n"
        )

    out: list[str] = [f"# {conversation.title or f'#{conversation.number}'}\n"]
    me = (conversation.identity or "").lower()

    if conversation.threads:
        resolved = sum(1 for t in conversation.threads if t.is_resolved)
        out.append(
            f"## Threads — {conversation.open_threads} open, {resolved} resolved\n"
        )
        for thread in conversation.threads:
            out.append(f"### {_thread_heading(thread)}\n")
            for comment in thread.comments:
                who = f"@{comment.author}"
                if me and comment.author.lower() == me:
                    who += " *(this tool)*"
                out.append(f"**{who}**\n")
                out.append(f"{_clip(comment.body)}\n")
            if thread.comments and thread.comments[-1].url:
                out.append(f"[Open this thread]({thread.comments[-1].url})\n")

    shown, dropped = _worth_showing(conversation.reviews)
    if shown:
        out.append("## Submitted reviews\n")
        if dropped:
            out.append(
                f"*{dropped} older review(s) by other people are not shown. "
                "Open it on GitHub for the lot.*\n"
            )
        for review in shown:
            who = f"@{review.author}" + (" *(this tool)*" if review.ours else "")
            state = review.state.replace("_", " ").lower()
            out.append(f"### {who} — {state}\n")
            if review.body.strip():
                out.append(f"{_clip(review.body)}\n")
            else:
                out.append("*No summary — inline comments only.*\n")

    return "\n".join(out)


def _worth_showing(reviews: tuple[Review, ...]) -> tuple[list[Review], int]:
    """Every review of ours, and the most recent few of everyone else's.

    Returns what to render and how many were left out, because a cap nobody is
    told about reads as "that was all of it".
    """
    ours = [r for r in reviews if r.ours]
    theirs = [r for r in reviews if not r.ours]
    kept = theirs[-MAX_OTHER_REVIEWS:]
    order = {id(r): i for i, r in enumerate(reviews)}
    shown = sorted(ours + kept, key=lambda r: order[id(r)])
    return shown, len(theirs) - len(kept)


class Runner:
    """Fetches one pull request's conversation, off the interface's thread.

    Results are kept for the life of the run, so flicking back to a pull request
    you already looked at is instant. ``refresh`` is what asks GitHub again —
    the point of fetching rather than storing is that the answer changes.
    """

    def __init__(
        self,
        open_store: Callable[[], Store],
        global_cfg: Any,
        repos: list[Any],
        *,
        dry_run: bool = False,
    ) -> None:
        self.open_store = open_store
        self.global_cfg = global_cfg
        self.repos = {cfg.repo: cfg for cfg in repos}
        self.dry_run = dry_run
        self._lock = threading.Lock()
        self._done: dict[tuple[str, int], Conversation] = {}
        self._busy: set[tuple[str, int]] = set()

    # ------------------------------------------------------------- reading

    def result(self, repo: str, number: int) -> Conversation | None:
        """What was fetched, or None while it is still being asked for."""
        with self._lock:
            return self._done.get((repo, number))

    def busy(self, repo: str, number: int) -> bool:
        with self._lock:
            return (repo, number) in self._busy

    def knows(self, repo: str) -> bool:
        return repo in self.repos

    # ------------------------------------------------------------- driving

    def request(self, repo: str, number: int, *, refresh: bool = False) -> bool:
        """Ask for one conversation. False when there is nothing to do."""
        if repo not in self.repos:
            return False
        key = (repo, number)
        with self._lock:
            if key in self._busy:
                return False
            if key in self._done and not refresh:
                return False
            self._busy.add(key)
        threading.Thread(
            target=self._work, args=(repo, number), name="conversation", daemon=True
        ).start()
        return True

    def forget(self, repo: str, number: int) -> None:
        with self._lock:
            self._done.pop((repo, number), None)

    # -------------------------------------------------------------- worker

    def _work(self, repo: str, number: int) -> None:
        cfg = self.repos[repo]
        store = self.open_store()
        try:
            conversation = self._fetch(store, cfg, repo, number)
        except Exception as exc:  # noqa: BLE001 - a dead thread must not hang the UI
            log.get().exception("%s#%s: could not read the conversation", repo, number)
            conversation = Conversation(
                repo=repo, number=number, error=str(exc)[:300], fetched_at=time.time()
            )
        finally:
            store.close()
        with self._lock:
            self._done[(repo, number)] = conversation
            self._busy.discard((repo, number))

    def _fetch(self, store: Store, cfg: Any, repo: str, number: int) -> Conversation:
        graphql = GraphQLClient(
            self.global_cfg.token, self.global_cfg.graphql_url, dry_run=self.dry_run
        )
        rest = RestClient(
            self.global_cfg.token, self.global_cfg.api_url, store, dry_run=self.dry_run
        )
        identity = getattr(cfg, "identity", "") or ""

        title, threads = "", ()
        try:
            snapshot: PRSnapshot = graphql.pr_snapshot(cfg.owner, cfg.name, number)
            title, threads = snapshot.title, tuple(snapshot.threads)
        except GitHubError as exc:
            return Conversation(
                repo=repo, number=number, error=str(exc)[:300], fetched_at=time.time()
            )

        reviews: list[Review] = []
        try:
            for row in rest.list_reviews(cfg.owner, cfg.name, number):
                author = (row.get("user") or {}).get("login") or "ghost"
                state = str(row.get("state") or "")
                if state.upper() == "PENDING":
                    continue
                reviews.append(
                    Review(
                        author=author,
                        state=state,
                        body=str(row.get("body") or ""),
                        submitted_at=str(row.get("submitted_at") or ""),
                        url=str(row.get("html_url") or ""),
                        ours=bool(identity) and author.lower() == identity.lower(),
                    )
                )
        except GitHubError as exc:
            # The threads are the substance; a missing covering letter is worth
            # a log line rather than an empty screen.
            log.get().warning("%s#%s: could not list reviews: %s", repo, number, exc)

        return Conversation(
            repo=repo,
            number=number,
            title=title,
            reviews=tuple(reviews),
            threads=threads,
            identity=identity,
            fetched_at=time.time(),
        )
