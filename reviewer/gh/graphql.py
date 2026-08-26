"""GitHub GraphQL client.

Needed for three things REST cannot do:

* read review threads with ``isResolved`` / ``isOutdated``
* resolve a conversation (``resolveReviewThread`` — no REST equivalent exists)
* read the check rollup for a commit in one round trip

One deliberate choice about the check rollup: it contains check runs and commit
statuses, and *not* branch-protection rules. The "at least one approving review"
requirement lives in branch protection and surfaces as ``reviewDecision``, so
gating on the rollup means the reviewer never sits waiting for its own approval.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .. import log
from .rest import API_VERSION, USER_AGENT, GitHubError

PR_QUERY = """
query PR($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef { name }
    pullRequest(number: $number) {
      number
      title
      createdAt
      isDraft
      reviewDecision
      mergeable
      url
      headRefOid
      baseRefName
      additions
      deletions
      changedFiles
      author { login }
      labels(first: 50) { nodes { name } }
      reviewRequests(first: 50) {
        nodes {
          requestedReviewer {
            __typename
            ... on User { login }
            ... on Team { slug }
          }
        }
      }
      latestOpinionatedReviews(first: 50) {
        nodes { state submittedAt author { login } }
      }
      # latestOpinionatedReviews above is APPROVED/CHANGES_REQUESTED only, so it
      # cannot tell "nobody has looked at this" from "someone reviewed and left
      # the approval to a human". latestReviews includes COMMENTED, which is
      # what the board needs to say where your own pull requests stand.
      latestReviews(first: 50) {
        nodes { state submittedAt author { login } }
      }
      commits(last: 1) {
        nodes {
          commit {
            oid
            statusCheckRollup {
              state
              contexts(first: 100) {
                nodes {
                  __typename
                  ... on CheckRun { name conclusion status }
                  ... on StatusContext { context state }
                }
              }
            }
          }
        }
      }
      reviewThreads(first: 100) {
        nodes {
          id
          isResolved
          isOutdated
          isCollapsed
          path
          line
          startLine
          originalLine
          diffSide
          comments(first: 50) {
            nodes {
              id
              databaseId
              body
              createdAt
              url
              author { login }
            }
          }
        }
      }
    }
  }
}
"""

# Backfill. Ordered by UPDATED_AT rather than MERGED_AT, which GitHub does not
# offer as a sort key — but a merged pull request is always updated at or after
# it was merged, so "updated before the cutoff" is a safe place to stop. It over-
# fetches slightly (something merged long ago and touched since) and the caller
# filters those out; it never misses one that belongs in the range.
#
# Deliberately not the search API, which caps at 1000 results and would need the
# range sliced up to backfill a repository with more merges than that.
MERGED_QUERY = """
query Merged($owner: String!, $name: String!, $size: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: MERGED, first: $size, after: $cursor,
                 orderBy: {field: UPDATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        url
        createdAt
        mergedAt
        updatedAt
        additions
        deletions
        changedFiles
        baseRefName
        author { login }
        mergedBy { login }
        labels(first: 20) { nodes { name } }
      }
    }
  }
}
"""

# One request, one number, so the size of a backfill can be shown before it is
# started. Search caps its *results* at 1000 but reports the true total, which
# is all this is for.
COUNT_QUERY = """
query Count($q: String!) {
  search(query: $q, type: ISSUE) { issueCount }
}
"""

RESOLVE_MUTATION = """
mutation Resolve($threadId: ID!) {
  resolveReviewThread(input: {threadId: $threadId}) {
    thread { id isResolved }
  }
}
"""


@dataclass
class ThreadComment:
    node_id: str
    database_id: int | None
    author: str
    body: str
    created_at: str
    url: str


@dataclass
class ReviewThread:
    node_id: str
    is_resolved: bool
    is_outdated: bool
    path: str | None
    line: int | None
    original_line: int | None
    diff_side: str
    comments: list[ThreadComment] = field(default_factory=list)

    @property
    def started_by(self) -> str:
        return self.comments[0].author if self.comments else ""

    @property
    def last_comment(self) -> ThreadComment | None:
        return self.comments[-1] if self.comments else None

    def is_ours(self, identity: str | None) -> bool:
        return bool(identity) and self.started_by.lower() == (identity or "").lower()

    def awaiting_us(self, identity: str | None) -> bool:
        """True when someone else had the last word on a thread we started."""
        last = self.last_comment
        if last is None or not self.is_ours(identity):
            return False
        return last.author.lower() != (identity or "").lower()


@dataclass
class CheckState:
    rollup: str | None
    contexts: list[dict[str, Any]] = field(default_factory=list)
    accessible: bool = True
    """False when the token cannot read the check rollup.

    Kept separate from "there are no checks" on purpose. An empty context list
    means a repository with no CI, which is fine to review. An inaccessible
    rollup means we are blind, and treating blind as green would approve pull
    requests with failing CI.
    """


@dataclass
class PRSnapshot:
    number: int
    title: str
    is_draft: bool
    review_decision: str | None
    head_sha: str
    base_ref: str
    additions: int
    deletions: int
    changed_files: int
    author: str
    labels: list[str]
    requested_reviewers: list[str]
    opinionated_reviews: list[dict[str, Any]]
    checks: CheckState
    threads: list[ReviewThread]
    default_branch: str
    latest_reviews: list[dict[str, Any]] = field(default_factory=list)
    """Every reviewer's most recent review, including plain COMMENTED ones."""
    created_at: str = ""
    """When the pull request was opened, ISO 8601. Drives "how long has this
    been sitting there", which is about the PR and not about our review of
    it."""
    mergeable: str = "UNKNOWN"
    """MERGEABLE | CONFLICTING | UNKNOWN. UNKNOWN means GitHub has not finished
    computing it yet, which is common right after a push — it is not a synonym
    for CONFLICTING and should never be shown as one."""
    url: str = ""

    def approved_by_others(self, identity: str | None) -> bool:
        me = (identity or "").lower()
        return any(
            r.get("state") == "APPROVED" and (r.get("author") or "").lower() != me
            for r in self.opinionated_reviews
        )

    def our_last_review_state(self, identity: str | None) -> str | None:
        me = (identity or "").lower()
        for review in self.opinionated_reviews:
            if (review.get("author") or "").lower() == me:
                return review.get("state")
        return None

    def review_requested_from(self, identity: str | None) -> bool:
        me = (identity or "").lower()
        return any(r.lower() == me for r in self.requested_reviewers)


class GraphQLClient:
    def __init__(self, token: str, url: str, *, dry_run: bool = False) -> None:
        self.token = token
        self.url = url
        self.dry_run = dry_run

    def _execute(
        self, query: str, variables: dict[str, Any]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Run a query, returning ``(data, errors)``.

        GitHub's GraphQL API routinely answers with *partial* data: a field the
        token cannot read comes back null with an error beside it, while the rest
        of the response is complete and correct. Raising on any error throws away
        a usable response over one inaccessible field, so errors are returned for
        the caller to interpret and only a genuinely empty response raises.
        """
        payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            if exc.code in (403, 429):
                log.get().warning("GraphQL rate limited, sleeping 60s")
                time.sleep(60)
                return self._execute(query, variables)
            raise GitHubError(exc.code, detail, self.url) from exc
        except urllib.error.URLError as exc:
            raise GitHubError(0, f"network error: {exc.reason}", self.url) from exc

        errors = [e for e in (body.get("errors") or []) if isinstance(e, dict)]
        data = body.get("data") or {}

        if not data and errors:
            messages = "; ".join(e.get("message", "?") for e in errors)
            raise GitHubError(200, f"GraphQL error: {messages}", self.url)

        return data, errors

    @staticmethod
    def _errored_fields(errors: list[dict[str, Any]]) -> set[str]:
        """Field names mentioned in error paths, e.g. ``statusCheckRollup``."""
        fields: set[str] = set()
        for error in errors:
            for part in error.get("path") or []:
                if isinstance(part, str):
                    fields.add(part)
        return fields

    def pr_snapshot(self, owner: str, name: str, number: int) -> PRSnapshot:
        data, errors = self._execute(
            PR_QUERY, {"owner": owner, "name": name, "number": number}
        )

        errored = self._errored_fields(errors)
        rollup_accessible = "statusCheckRollup" not in errored
        if errors:
            log.get().debug(
                "GraphQL returned %d partial error(s) on %s/%s#%s: %s",
                len(errors),
                owner,
                name,
                number,
                "; ".join(e.get("message", "?") for e in errors)[:300],
            )

        repo = data.get("repository") or {}
        pr = repo.get("pullRequest") or {}
        if not pr:
            raise GitHubError(404, f"PR #{number} not found", self.url)

        requested = []
        for node in _nodes(pr.get("reviewRequests")):
            reviewer = node.get("requestedReviewer") or {}
            login = reviewer.get("login") or reviewer.get("slug")
            if login:
                requested.append(login)

        commit_nodes = _nodes(pr.get("commits"))
        commit = commit_nodes[0].get("commit", {}) if commit_nodes else {}
        rollup = commit.get("statusCheckRollup") or {}
        contexts = []
        for ctx in _nodes(rollup.get("contexts")):
            if ctx.get("__typename") == "CheckRun":
                contexts.append(
                    {
                        "kind": "check_run",
                        "name": ctx.get("name") or "",
                        "conclusion": ctx.get("conclusion"),
                        "status": ctx.get("status"),
                    }
                )
            elif ctx.get("__typename") == "StatusContext":
                contexts.append(
                    {
                        "kind": "status",
                        "name": ctx.get("context") or "",
                        "conclusion": ctx.get("state"),
                        "status": "COMPLETED",
                    }
                )

        threads = []
        for node in _nodes(pr.get("reviewThreads")):
            comments = [
                ThreadComment(
                    node_id=c.get("id") or "",
                    database_id=c.get("databaseId"),
                    author=((c.get("author") or {}).get("login") or "ghost"),
                    body=c.get("body") or "",
                    created_at=c.get("createdAt") or "",
                    url=c.get("url") or "",
                )
                for c in _nodes(node.get("comments"))
            ]
            threads.append(
                ReviewThread(
                    node_id=node.get("id") or "",
                    is_resolved=bool(node.get("isResolved")),
                    is_outdated=bool(node.get("isOutdated")),
                    path=node.get("path"),
                    line=node.get("line"),
                    original_line=node.get("originalLine"),
                    diff_side=node.get("diffSide") or "RIGHT",
                    comments=comments,
                )
            )

        return PRSnapshot(
            number=pr.get("number") or number,
            title=pr.get("title") or "",
            is_draft=bool(pr.get("isDraft")),
            review_decision=pr.get("reviewDecision"),
            head_sha=pr.get("headRefOid") or "",
            base_ref=pr.get("baseRefName") or "",
            additions=int(pr.get("additions") or 0),
            deletions=int(pr.get("deletions") or 0),
            changed_files=int(pr.get("changedFiles") or 0),
            author=((pr.get("author") or {}).get("login") or "ghost"),
            labels=[n.get("name", "") for n in _nodes(pr.get("labels"))],
            requested_reviewers=requested,
            opinionated_reviews=[
                {
                    "state": r.get("state"),
                    "author": (r.get("author") or {}).get("login") or "",
                    "submitted_at": r.get("submittedAt"),
                }
                for r in _nodes(pr.get("latestOpinionatedReviews"))
            ],
            checks=CheckState(
                rollup=rollup.get("state"),
                contexts=contexts,
                accessible=rollup_accessible,
            ),
            latest_reviews=[
                {
                    "state": r.get("state"),
                    "author": (r.get("author") or {}).get("login") or "",
                    "submitted_at": r.get("submittedAt"),
                }
                for r in _nodes(pr.get("latestReviews"))
            ],
            created_at=pr.get("createdAt") or "",
            threads=threads,
            default_branch=(repo.get("defaultBranchRef") or {}).get("name") or "main",
            mergeable=(pr.get("mergeable") or "UNKNOWN"),
            url=pr.get("url") or "",
        )

    def merged_count(self, owner: str, name: str, since_date: str | None) -> int | None:
        """How many merged pull requests a backfill would cover.

        ``since_date`` is ``YYYY-MM-DD`` or None for everything. Returns None if
        the count could not be had — a backfill can still run without it, it
        just cannot say up front how big it will be.
        """
        query = f"repo:{owner}/{name} is:pr is:merged"
        if since_date:
            query += f" merged:>={since_date}"
        try:
            data, _ = self._execute(COUNT_QUERY, {"q": query})
        except GitHubError as exc:
            log.get().debug("could not count merged PRs for %s/%s: %s", owner, name, exc)
            return None
        count = (data.get("search") or {}).get("issueCount")
        return int(count) if isinstance(count, int) else None

    def merged_page(
        self, owner: str, name: str, cursor: str | None, size: int = 100
    ) -> tuple[list[dict[str, Any]], str | None]:
        """One page of merged pull requests, newest activity first.

        Returns ``(rows, next_cursor)``; a null cursor means the last page.
        """
        data, _ = self._execute(
            MERGED_QUERY,
            {"owner": owner, "name": name, "size": size, "cursor": cursor},
        )
        connection = ((data.get("repository") or {}).get("pullRequests")) or {}
        page = connection.get("pageInfo") or {}

        rows = []
        for node in _nodes(connection):
            rows.append(
                {
                    "number": node.get("number"),
                    "title": node.get("title") or "",
                    "url": node.get("url") or "",
                    "created_at": node.get("createdAt") or "",
                    "merged_at": node.get("mergedAt") or "",
                    "updated_at": node.get("updatedAt") or "",
                    "additions": int(node.get("additions") or 0),
                    "deletions": int(node.get("deletions") or 0),
                    "changed_files": int(node.get("changedFiles") or 0),
                    "base_ref": node.get("baseRefName") or "",
                    "author": (node.get("author") or {}).get("login") or "ghost",
                    "merged_by": (node.get("mergedBy") or {}).get("login") or "",
                    "labels": [n.get("name", "") for n in _nodes(node.get("labels"))],
                }
            )

        next_cursor = page.get("endCursor") if page.get("hasNextPage") else None
        return rows, next_cursor

    def resolve_thread(self, thread_id: str) -> bool:
        if self.dry_run:
            log.get().info("[dry-run] resolve thread %s", thread_id)
            return True
        try:
            data, errors = self._execute(RESOLVE_MUTATION, {"threadId": thread_id})
        except GitHubError as exc:
            log.get().warning("could not resolve thread %s: %s", thread_id, exc)
            return False
        if errors:
            log.get().warning(
                "could not resolve thread %s: %s",
                thread_id,
                "; ".join(e.get("message", "?") for e in errors)[:200],
            )
            return False
        thread = (data.get("resolveReviewThread") or {}).get("thread") or {}
        return bool(thread.get("isResolved"))


def _nodes(connection: Any) -> list[dict[str, Any]]:
    if not isinstance(connection, dict):
        return []
    return [n for n in (connection.get("nodes") or []) if isinstance(n, dict)]
