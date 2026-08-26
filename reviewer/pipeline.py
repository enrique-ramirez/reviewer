"""One repository's tick, and one pull request's review.

The order of operations here is the cost model. A PR whose head has not moved and
whose threads have no new replies never reaches the model. A PR whose only change
is a new comment gets the cheap per-thread path, not a full re-review. Only a new
head SHA or an explicit re-review request buys a full pass.
"""

from __future__ import annotations

import re
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import (
    diff,
    gates,
    log,
    model,
    notify,
    prompt,
    publish,
    render,
    summarize,
    threads,
    worktree,
)
from .config import DEFAULT_CONTEXT_PATHS, GlobalConfig, RepoConfig
from .gh import GitHubError, GraphQLClient, RestClient
from .gh.graphql import PRSnapshot
from .log import DebugSink
from .state import Store

ISSUE_REF_RE = re.compile(r"(?:^|\s)#(\d{1,6})\b")
CLOSING_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d{1,6})\b", re.IGNORECASE
)
MAX_LINKED_ISSUES = 3
LOG_TITLE_CHARS = 160


def _compact(n: Any) -> str:
    """1_575_476 -> '1.6M'. Exact counts belong in --debug, not in a live log."""
    try:
        value = int(n)
    except (TypeError, ValueError):
        return "?"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.0f}k"
    return str(value)


def _epoch(stamp: Any) -> float | None:
    """GitHub's ``2024-01-02T03:04:05Z`` as a Unix timestamp."""
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _format_usage(usage: dict[str, Any], seconds: float) -> str:
    """One readable line instead of a wall of nested token dictionaries.

    The full payload is written to the debug directory; what belongs on screen
    is how long it took, how much it wrote, and how hard it worked.
    """
    parts = [f"model call {seconds:.0f}s"]
    if usage.get("output_tokens"):
        parts.append(f"{_compact(usage['output_tokens'])} out")
    if usage.get("cache_read_input_tokens"):
        parts.append(f"{_compact(usage['cache_read_input_tokens'])} cached")
    if usage.get("num_turns"):
        parts.append(f"{usage['num_turns']} turns")
    if usage.get("total_cost_usd"):
        parts.append(f"${float(usage['total_cost_usd']):.2f}")
    return " · ".join(parts)


@dataclass
class TickResult:
    reviewed: int = 0
    skipped: int = 0
    errors: int = 0


class Reviewer:
    def __init__(
        self,
        *,
        cfg: RepoConfig,
        global_cfg: GlobalConfig,
        rest: RestClient,
        graphql: GraphQLClient,
        store: Store,
        personality_dir: Path,
        debug: DebugSink,
        dry_run: bool = False,
        force: bool = False,
        status_cb: Any = None,
    ) -> None:
        self.cfg = cfg
        self.global_cfg = global_cfg
        self.rest = rest
        self.graphql = graphql
        self.store = store
        self.personality_dir = personality_dir
        self.debug = debug
        self.dry_run = dry_run
        self.force = force
        self.status_cb = status_cb or (lambda _text: None)
        self._repo_context: dict[str, str] | None = None
        self._repo_context_loaded = False

    # ------------------------------------------------------------------ tick

    def tick(self, *, budget: int, only_pr: int | None = None) -> TickResult:
        result = TickResult()

        # Nothing can legitimately be under review as a tick begins, so whatever
        # is still marked belongs to a process that did not finish.
        self.store.clear_active(self.cfg.repo)

        if self.cfg.local_path:
            try:
                worktree.prune_stale(self.cfg.local_path)
            except worktree.GitError as exc:
                log.get().warning("%s: could not prune worktrees: %s", self.cfg.repo, exc)

        try:
            pulls = self.rest.list_open_pulls(self.cfg.owner, self.cfg.name)
        except GitHubError as exc:
            log.get().error("%s: could not list pull requests: %s", self.cfg.repo, exc)
            result.errors += 1
            return result

        if only_pr is not None:
            pulls = [p for p in pulls if p.get("number") == only_pr]
            if not pulls:
                pulls = [{"number": only_pr}]

        log.get().info("%s: %d open pull requests", self.cfg.repo, len(pulls))

        numbers = [p["number"] for p in pulls if isinstance(p.get("number"), int)]

        # With --pr, ``pulls`` was filtered down to one and says nothing about
        # what else is open. Treating it as the full list would file every other
        # pull request as vanished and wipe the board.
        if only_pr is None:
            # Anything that vanished but could not be read is kept on the board,
            # or the row would be dropped here and the merge never recorded.
            unresolved = self._record_merges(numbers)
            self.store.forget_closed(self.cfg.repo, numbers + unresolved)

        # Phase 1 — a fast scan that populates the board before any model call.
        # Costs one GraphQL request per pull request and no model time, so the
        # interface has a complete picture within seconds of starting rather
        # than after a full review cycle. The snapshots are reused below, so
        # this adds no API calls overall.
        snapshots: dict[int, PRSnapshot] = {}
        for index, number in enumerate(numbers, start=1):
            self.status_cb(f"scanning {index}/{len(numbers)} — #{number}")
            try:
                snapshot = self.graphql.pr_snapshot(self.cfg.owner, self.cfg.name, number)
            except Exception as exc:  # noqa: BLE001 - one bad PR must not stop the scan
                log.get().warning("%s#%s: could not scan: %s", self.cfg.repo, number, exc)
                continue
            self._backfill_checks(snapshot)
            snapshots[number] = snapshot
            self.record_board(snapshot)
            log.get().info(
                "%s#%s scanned (%d/%d) — %s",
                self.cfg.repo,
                number,
                index,
                len(numbers),
                # Generous rather than tidy: 60 cut real titles mid-word, and
                # the pane this lands in wraps. Only a pathological title is
                # shortened now, and the full one is always on the board.
                snapshot.title[:LOG_TITLE_CHARS],
            )

        self.status_cb(f"scanned {len(snapshots)} — starting reviews")

        # Phase 2 — the slow half.
        for pull in pulls:
            if result.reviewed >= budget:
                log.get().info(
                    "%s: hit the per-tick review budget, leaving the rest for next time",
                    self.cfg.repo,
                )
                break
            number = pull.get("number")
            if not isinstance(number, int):
                continue
            self.status_cb(f"reviewing #{number}")
            try:
                if self.review_pr(number, snapshot=snapshots.get(number)):
                    result.reviewed += 1
                else:
                    result.skipped += 1
            except Exception as exc:  # noqa: BLE001 - one PR must not kill the tick
                log.get().exception("%s#%s: %s", self.cfg.repo, number, exc)
                result.errors += 1
                if notify.should_notify(self.cfg.notify_on, "error"):
                    notify.send(
                        self.global_cfg.notifications,
                        "PR reviewer error",
                        f"{self.cfg.repo}#{number}: {exc}"[:200],
                    )

        # Last, so a failure here cannot cost a review: by this point every
        # merge is already on record and only its description is outstanding.
        if only_pr is None:
            self._retry_descriptions()

        return result

    # ------------------------------------------------------------- merges

    def _record_merges(self, open_numbers: list[int]) -> list[int]:
        """File pull requests that left the open list having been merged.

        Only ones we reviewed, and approval is not the bar — a pull request we
        commented on or requested changes on is one we have an opinion about,
        and where it ended up is worth knowing.

        That filter is also what keeps this cheap. A pull request nobody asked
        us about costs nothing when it closes; only the ones we touched buy a
        request, and only once each.

        Returns the numbers whose fate could not be established, for the caller
        to keep on the board so the next tick can try again.
        """
        cfg = self.cfg
        vanished = sorted(set(self.store.board_numbers(cfg.repo)) - set(open_numbers))
        unresolved: list[int] = []
        for number in vanished:
            if self.store.is_merge_recorded(cfg.repo, number):
                continue
            if not self.store.has_reviewed(cfg.repo, number):
                continue
            try:
                pull = self.rest.get_pull(cfg.owner, cfg.name, number)
            except GitHubError as exc:
                # Filing a merge we could not confirm would be worse than
                # waiting, so hold the row and look again next time.
                log.get().warning(
                    "%s#%s: left the open list but could not be read: %s",
                    cfg.repo,
                    number,
                    exc,
                )
                unresolved.append(number)
                continue
            if not pull.get("merged_at"):
                log.get().info("%s#%s closed without merging", cfg.repo, number)
                continue
            self._file_merge(pull)
        return unresolved

    def _file_merge(self, pull: dict[str, Any]) -> None:
        cfg = self.cfg
        number = int(pull.get("number") or 0)
        author = (pull.get("user") or {}).get("login") or "ghost"
        tally = self.store.review_tally(cfg.repo, number)
        opened = _epoch(pull.get("created_at"))
        merged = _epoch(pull.get("merged_at"))

        filed = self.store.record_merged(
            {
                "repo": cfg.repo,
                "pr_number": number,
                "title": pull.get("title") or "",
                "author": author,
                "url": pull.get("html_url") or "",
                "base_ref": (pull.get("base") or {}).get("ref") or "",
                "labels": [
                    label.get("name", "")
                    for label in (pull.get("labels") or [])
                    if isinstance(label, dict)
                ],
                "is_ours": int(author.lower() == (cfg.identity or "").lower()),
                "additions": int(pull.get("additions") or 0),
                "deletions": int(pull.get("deletions") or 0),
                "changed_files": int(pull.get("changed_files") or 0),
                "opened_at": opened,
                "merged_at": merged,
                "merged_by": (pull.get("merged_by") or {}).get("login") or "",
                "our_reviews": tally["rounds"],
                "our_comments": tally["comments"],
                "our_blockers": tally["blockers"],
                "last_event": tally["last_event"],
            }
        )
        if not filed:
            return

        log.get().info(
            "%s#%s merged — %d round(s), %d comment(s) from us",
            cfg.repo,
            number,
            tally["rounds"],
            tally["comments"],
        )
        # Described here, while the payload is in hand, rather than on a later
        # pass that would have to fetch it all over again.
        self._describe(number, pull, tally["summary"])

    def _retry_descriptions(self) -> None:
        """Second attempt for merges whose description call failed."""
        settings = self.global_cfg.merge_summary
        if not settings.get("enabled", True):
            return
        pending = self.store.merged_pending_description(
            [self.cfg.repo],
            limit=int(settings.get("max_per_tick") or 5),
            max_tries=int(settings.get("max_tries") or 3),
        )
        for row in pending:
            number = int(row["pr_number"])
            try:
                pull = self.rest.get_pull(self.cfg.owner, self.cfg.name, number)
            except GitHubError:
                continue
            tally = self.store.review_tally(self.cfg.repo, number)
            self._describe(number, pull, tally["summary"])

    def _describe(self, number: int, pull: dict[str, Any], review_summary: str) -> None:
        """Write the one-line account of what a merged pull request changed."""
        cfg = self.cfg
        settings = self.global_cfg.merge_summary
        if not settings.get("enabled", True):
            # Summaries turned off. Our own review of the change is the next
            # best account of it and costs nothing, so use that rather than
            # leaving the row blank forever.
            self._fallback_description(number, pull, review_summary)
            return

        tries = self.store.bump_description_try(cfg.repo, number)
        self.status_cb(f"summarising merged #{number}")

        # The file list is only worth a request when we have nothing better.
        # Where the reviewer already described the change, that is the stronger
        # input and this stays a zero-API-call operation.
        files = None if review_summary.strip() else self._changed_paths(number)

        text = summarize.describe(
            self.global_cfg.summary_provider_for(cfg),
            repo=cfg.repo,
            pull=pull,
            review_summary=review_summary,
            files=files,
        )
        if text:
            self.store.set_merge_description(cfg.repo, number, text, "model")
            return

        if tries >= int(settings.get("max_tries") or 3):
            # Out of attempts. Something readable beats a permanently blank row.
            self._fallback_description(number, pull, review_summary)

    def _fallback_description(
        self, number: int, pull: dict[str, Any], review_summary: str
    ) -> None:
        """Fill the description without the model.

        The source is recorded alongside so the interface can say plainly that
        this is not the written summary, rather than passing it off as one.
        """
        text = " ".join((review_summary or pull.get("title") or "").split())
        if not text:
            return
        self.store.set_merge_description(
            self.cfg.repo,
            number,
            text[: summarize.MAX_DESCRIPTION_CHARS],
            "review" if review_summary.strip() else "title",
        )

    def _changed_paths(self, number: int) -> list[str]:
        """File names only, one page. Enough to say what area a change touched."""
        try:
            payload = self.rest.get(
                f"/repos/{self.cfg.owner}/{self.cfg.name}/pulls/{number}/files"
                "?per_page=100"
            )
        except GitHubError:
            return []
        if not isinstance(payload, list):
            return []
        return [f.get("filename", "") for f in payload if isinstance(f, dict)]

    # -------------------------------------------------------------- one PR

    def _backfill_checks(self, snapshot: PRSnapshot) -> None:
        """Fill in CI state from REST when the GraphQL rollup is unreadable.

        Fine-grained tokens without the Checks permission are refused
        ``statusCheckRollup``. Two REST endpoints can stand in, tried in order of
        how much they cover:

        * ``/check-runs`` — the same data as the rollup. Also needs Checks, so it
          usually fails alongside it, but costs one request to find out.
        * ``/status`` — legacy commit statuses. Covers integrations that post
          statuses; does *not* cover GitHub Actions check runs.

        A source that returns zero entries is treated as telling us nothing, not
        as telling us everything passed. Staying blind is the safe answer.
        """
        source = self.cfg.gates.get("ci_source", "auto")
        if source in ("none", "rollup") or snapshot.checks.accessible:
            return
        if not snapshot.head_sha:
            return

        if source in ("auto", "check_runs"):
            payload = self.rest.check_runs(self.cfg.owner, self.cfg.name, snapshot.head_sha)
            runs = (payload or {}).get("check_runs") or []
            if runs:
                snapshot.checks.contexts = [
                    {
                        "kind": "check_run",
                        "name": run.get("name") or "",
                        "conclusion": (run.get("conclusion") or "").upper() or None,
                        "status": (run.get("status") or "").upper(),
                    }
                    for run in runs
                ]
                snapshot.checks.accessible = True
                log.get().debug(
                    "%s#%s: CI read from REST check-runs (%d)",
                    self.cfg.repo,
                    snapshot.number,
                    len(runs),
                )
                return

        if source in ("auto", "actions_runs"):
            runs = self.rest.actions_runs(self.cfg.owner, self.cfg.name, snapshot.head_sha)
            if runs:
                snapshot.checks.contexts = [
                    {
                        "kind": "actions_run",
                        "name": run.get("name") or run.get("display_title") or "workflow",
                        "conclusion": (run.get("conclusion") or "").upper() or None,
                        "status": (run.get("status") or "").upper(),
                    }
                    for run in runs
                ]
                snapshot.checks.accessible = True
                log.get().debug(
                    "%s#%s: CI read from Actions workflow runs (%d)",
                    self.cfg.repo,
                    snapshot.number,
                    len(runs),
                )
                return

        if source in ("auto", "commit_statuses"):
            payload = self.rest.combined_status(
                self.cfg.owner, self.cfg.name, snapshot.head_sha
            )
            statuses = (payload or {}).get("statuses") or []
            if statuses:
                snapshot.checks.rollup = (payload or {}).get("state", "").upper() or None
                snapshot.checks.contexts = [
                    {
                        "kind": "status",
                        "name": s.get("context") or "",
                        "conclusion": (s.get("state") or "").upper(),
                        "status": "COMPLETED",
                    }
                    for s in statuses
                ]
                snapshot.checks.accessible = True
                log.get().warning(
                    "%s#%s: CI read from commit statuses only (%d). GitHub Actions "
                    "check runs are not visible to this token — anything running "
                    "in Actions is unchecked.",
                    self.cfg.repo,
                    snapshot.number,
                    len(statuses),
                )

    def record_board(self, snapshot: PRSnapshot, action: str | None = None) -> None:
        """Write one pull request's row for the interface.

        Called before gating so that skipped pull requests — including your own,
        which the reviewer never reviews — still appear on the board. Without
        this the interface could not answer "is my PR ready to merge", because
        the reviewer deliberately ignores those.
        """
        cfg = self.cfg
        identity = (cfg.identity or "").lower()
        threads = [t for t in snapshot.threads if not t.is_resolved]
        awaiting = [t for t in threads if t.awaiting_us(cfg.identity)]
        capped = [
            t
            for t in threads
            if self.store.is_thread_capped(cfg.repo, snapshot.number, t.node_id)
        ]

        ci_state, _ = gates.ci_status(snapshot, cfg)
        needs_human, reason = gates.needs_manual_approval(snapshot, cfg, [])

        row: dict[str, Any] = {
            "repo": cfg.repo,
            "pr_number": snapshot.number,
            "title": snapshot.title,
            "author": snapshot.author,
            "url": snapshot.url,
            "is_ours": int(snapshot.author.lower() == identity),
            "is_draft": int(snapshot.is_draft),
            "opened_at": _epoch(snapshot.created_at),
            "head_sha": snapshot.head_sha,
            "base_ref": snapshot.base_ref,
            "additions": snapshot.additions,
            "deletions": snapshot.deletions,
            "changed_files": snapshot.changed_files,
            "labels": snapshot.labels,
            "review_decision": snapshot.review_decision,
            # Who has reviewed and what they said. The board needs this for our
            # own pull requests, which the reviewer skips: with nothing of our
            # own to report on them, where they stand is entirely a question of
            # what other people have done.
            "reviews": [
                {"author": r.get("author") or "", "state": r.get("state") or ""}
                for r in snapshot.latest_reviews
                if r.get("author")
            ],
            "requested_reviewers": list(snapshot.requested_reviewers),
            "mergeable": snapshot.mergeable,
            "approved_by_others": int(snapshot.approved_by_others(cfg.identity)),
            "our_review_state": snapshot.our_last_review_state(cfg.identity),
            "requested_from_us": int(snapshot.review_requested_from(cfg.identity)),
            "ci_state": ci_state,
            "open_threads": len(threads),
            "threads_awaiting_us": len(awaiting),
            "capped_threads": len(capped),
            "needs_human": int(needs_human),
            "needs_human_reason": reason,
        }
        if action is not None:
            row["last_action"] = action
        self.store.upsert_pr_view(row)

    def review_pr(self, number: int, snapshot: PRSnapshot | None = None) -> bool:
        cfg = self.cfg
        if snapshot is None:
            snapshot = self.graphql.pr_snapshot(cfg.owner, cfg.name, number)
            self._backfill_checks(snapshot)
            self.record_board(snapshot)
        pr_state = self.store.get_pr(cfg.repo, number)

        discussion, newest_comment_id = self._discussion(number)
        has_new_comments = self._has_new_activity(
            snapshot, pr_state, newest_comment_id
        )

        decision = gates.evaluate(
            snapshot, cfg, pr_state, has_new_comments=has_new_comments
        )
        if not decision:
            if self.force:
                log.get().warning(
                    "%s#%s: --force, reviewing anyway (would have skipped: %s)",
                    cfg.repo,
                    number,
                    decision.reason,
                )
                decision = gates.Decision(True, "forced", "forced")
            else:
                log.get().info("%s#%s skipped — %s", cfg.repo, number, decision.reason)
                self.store.set_pr_action(cfg.repo, number, f"skipped: {decision.reason}")
                self.store.record_comment_scan(cfg.repo, number, newest_comment_id)
                return False

        log.get().info(
            "%s#%s reviewing — %s (%s)",
            cfg.repo,
            number,
            decision.reason,
            decision.trigger,
        )

        comments_only = decision.trigger == "new_comments"

        # From here on this pull request is live work, which can be minutes of
        # it. Marked so the board says so instead of showing the previous pass's
        # outcome the whole time. Cleared in `finally` — an exception here is
        # caught a level up, and without this the row would claim to still be
        # under review until the process restarted.
        self.store.begin_active(
            cfg.repo, number, "replying" if comments_only else "reviewing"
        )
        try:
            with ExitStack() as stack:
                checkout_path: Path | None = None
                if cfg.local_path:
                    try:
                        checkout = stack.enter_context(
                            worktree.pr_checkout(
                                cfg.local_path, number, snapshot.head_sha
                            )
                        )
                        checkout_path = checkout.path
                    except worktree.GitError as exc:
                        log.get().warning(
                            "%s#%s: no worktree (%s); reviewing from the diff alone",
                            cfg.repo,
                            number,
                            exc,
                        )

                repo_context = self._load_repo_context(snapshot.default_branch)

                self._handle_threads(snapshot, checkout_path, repo_context)

                if comments_only:
                    self.store.record_comment_scan(cfg.repo, number, newest_comment_id)
                    return True

                # Cheap guard before the expensive part: if a review for this
                # exact commit already went out, do not pay for another one.
                # Catches the crash-after-posting case, where the gate above
                # cannot know.
                if not self.force and self.store.already_posted(
                    cfg.repo, number, snapshot.head_sha
                ):
                    log.get().info(
                        "%s#%s: a review for %s already went out; not repeating it",
                        cfg.repo,
                        number,
                        snapshot.head_sha[:8],
                    )
                    self.store.record_review(
                        cfg.repo, number, snapshot.head_sha, "already-posted"
                    )
                    self.store.record_comment_scan(cfg.repo, number, newest_comment_id)
                    return False

                posted = self._full_review(
                    snapshot=snapshot,
                    checkout_path=checkout_path,
                    repo_context=repo_context,
                    pr_state_round=pr_state.review_round + 1,
                    discussion=discussion,
                )

            self.store.record_comment_scan(cfg.repo, number, newest_comment_id)
            return posted
        finally:
            self.store.end_active(cfg.repo, number)

    # ------------------------------------------------------------- helpers

    def _discussion(self, number: int) -> tuple[list[dict[str, Any]], int | None]:
        cfg = self.cfg
        try:
            issue_comments = self.rest.list_issue_comments(cfg.owner, cfg.name, number)
        except GitHubError:
            issue_comments = []
        ids = [c.get("id") for c in issue_comments if isinstance(c.get("id"), int)]
        newest = max(ids) if ids else None
        return issue_comments, newest

    def _has_new_activity(
        self,
        snapshot: PRSnapshot,
        pr_state,
        newest_comment_id: int | None,
    ) -> bool:
        """New PR comments, or a reply on one of our threads we have not answered."""
        if newest_comment_id is not None:
            if pr_state.last_seen_comment_id is None:
                if pr_state.last_reviewed_head_sha is not None:
                    return True
            elif newest_comment_id > pr_state.last_seen_comment_id:
                return True

        for thread in snapshot.threads:
            if thread.is_resolved or not thread.awaiting_us(self.cfg.identity):
                continue
            if self.store.is_thread_capped(
                self.cfg.repo, snapshot.number, thread.node_id
            ):
                continue
            last = thread.last_comment
            if last is None:
                continue
            seen = self.store.thread_last_seen(
                self.cfg.repo, snapshot.number, thread.node_id
            )
            if seen != last.node_id:
                return True
        return False

    def _load_repo_context(self, default_branch: str) -> dict[str, str] | None:
        if self._repo_context_loaded:
            return self._repo_context
        self._repo_context_loaded = True

        settings = self.cfg.review.get("repo_context") or {}
        if not settings.get("enabled", True) or not self.cfg.local_path:
            self._repo_context = None
            return None

        try:
            ref = worktree.fetch_base_ref(self.cfg.local_path, default_branch)
            files = worktree.read_trusted_files(
                self.cfg.local_path,
                ref,
                settings.get("paths") or DEFAULT_CONTEXT_PATHS,
                int(settings.get("max_chars") or 20000),
            )
        except worktree.GitError as exc:
            log.get().warning("%s: could not read repo context: %s", self.cfg.repo, exc)
            self._repo_context = None
            return None

        if files:
            log.get().debug(
                "%s: repo context from %s — %s",
                self.cfg.repo,
                default_branch,
                ", ".join(files),
            )
        self._repo_context = files or None
        return self._repo_context

    def _linked_issues(self, body: str, title: str) -> list[dict[str, Any]]:
        numbers: list[int] = []
        for match in CLOSING_RE.finditer(body):
            numbers.append(int(match.group(1)))
        for match in ISSUE_REF_RE.finditer(f"{title}\n{body}"):
            numbers.append(int(match.group(1)))

        seen: set[int] = set()
        issues: list[dict[str, Any]] = []
        for num in numbers:
            if num in seen or len(issues) >= MAX_LINKED_ISSUES:
                continue
            seen.add(num)
            try:
                issue = self.rest.get_issue(self.cfg.owner, self.cfg.name, num)
            except GitHubError:
                continue
            # An issue payload with pull_request set is another PR, not a ticket.
            if issue and "pull_request" not in issue:
                issues.append(issue)
        return issues

    def _handle_threads(
        self,
        snapshot: PRSnapshot,
        checkout_path: Path | None,
        repo_context: dict[str, str] | None,
    ) -> None:
        pending = threads.pending_threads(snapshot, self.cfg, self.store)
        if not pending:
            return

        log.get().info(
            "%s#%s: %d thread(s) with a reply to check",
            self.cfg.repo,
            snapshot.number,
            len(pending),
        )

        for thread in pending:
            outcome = threads.handle(
                thread=thread,
                snapshot=snapshot,
                cfg=self.cfg,
                global_cfg=self.global_cfg,
                rest=self.rest,
                graphql=self.graphql,
                store=self.store,
                checkout_path=checkout_path,
                personality_dir=self.personality_dir,
                repo_context=repo_context,
                debug=self.debug,
            )
            if outcome and outcome.capped and notify.should_notify(
                self.cfg.notify_on, "disagreement_cap_reached"
            ):
                notify.send(
                    self.global_cfg.notifications,
                    "Review thread needs you",
                    f"{self.cfg.repo}#{snapshot.number} — a thread went back and "
                    "forth too many times and is parked for a human.",
                )

    # -------------------------------------------------------- the main pass

    def _full_review(
        self,
        *,
        snapshot: PRSnapshot,
        checkout_path: Path | None,
        repo_context: dict[str, str] | None,
        pr_state_round: int,
        discussion: list[dict[str, Any]],
    ) -> bool:
        cfg = self.cfg
        number = snapshot.number

        pull = self.rest.get_pull(cfg.owner, cfg.name, number)
        pr_body = pull.get("body") or ""

        files = self.rest.list_pull_files(cfg.owner, cfg.name, number)
        bundle = diff.build(files, cfg.diff)

        if not bundle.has_content():
            log.get().info(
                "%s#%s: nothing reviewable after filtering, leaving it alone",
                cfg.repo,
                number,
            )
            return False

        axes = list(cfg.review.get("axes") or ["standards"])
        issues = self._linked_issues(pr_body, snapshot.title) if "spec" in axes else []

        system = prompt.build_system(self.personality_dir, cfg, repo_context)
        user = prompt.build_review_user_prompt(
            cfg=cfg,
            snapshot=snapshot,
            pr_body=pr_body,
            bundle=bundle,
            issues=issues,
            discussion=discussion,
            checkout_path=checkout_path,
            round_number=pr_state_round,
            axes=axes,
        )

        self.debug.write(cfg.repo, number, "system-prompt.md", system)
        self.debug.write(cfg.repo, number, "user-prompt.md", user)

        payloads: list[dict[str, Any]] = []
        if cfg.review.get("split_axes_into_separate_calls") and len(axes) > 1:
            for axis in axes:
                single = prompt.build_review_user_prompt(
                    cfg=cfg,
                    snapshot=snapshot,
                    pr_body=pr_body,
                    bundle=bundle,
                    issues=issues,
                    discussion=discussion,
                    checkout_path=checkout_path,
                    round_number=pr_state_round,
                    axes=[axis],
                )
                payloads.append(
                    self._call_model(system, single, checkout_path, number, axis)
                )
        else:
            payloads.append(self._call_model(system, user, checkout_path, number, "all"))

        findings: list[render.Finding] = []
        summaries: list[str] = []
        for payload in payloads:
            if not payload:
                continue
            findings.extend(render.parse_findings(payload))
            text = str(payload.get("summary") or "").strip()
            if text:
                summaries.append(text)

        if not payloads or all(p is None for p in payloads):
            return False

        summary_text = "\n\n".join(summaries)
        return self._publish(
            snapshot=snapshot,
            bundle=bundle,
            findings=findings,
            summary_text=summary_text,
        )

    def _call_model(
        self,
        system: str,
        user: str,
        checkout_path: Path | None,
        number: int,
        tag: str,
    ) -> dict[str, Any] | None:
        try:
            result = model.run(
                self.global_cfg.provider_for(self.cfg),
                system_prompt=system,
                user_prompt=user,
                add_dir=checkout_path,
            )
        except model.ModelError as exc:
            if model.cancelled():
                # We stopped it on purpose. Not an error worth shouting about.
                log.get().info("%s#%s: review abandoned — %s", self.cfg.repo, number, exc)
            else:
                log.get().error(
                    "%s#%s: model call failed: %s", self.cfg.repo, number, exc
                )
            return None
        self.debug.write(self.cfg.repo, number, f"response-{tag}.json", result.payload)
        log.get().info(
            "%s#%s: %s",
            self.cfg.repo,
            number,
            _format_usage(result.usage, result.duration_seconds),
        )
        return result.payload

    def _log_review_event(
        self,
        number: int,
        head_sha: str,
        event: str,
        findings: list[render.Finding],
        blockers: int,
        placed: Any,
        summary_text: str,
    ) -> None:
        """Append what this round did, for the merge record to total up later.

        ``summary_text`` is the model's own account of the change. It is written
        here because it is already in hand and would otherwise be rendered into
        the posted body and dropped — which makes it the cheapest possible input
        to the merge summary, and often the best one.
        """
        self.store.record_review_event(
            self.cfg.repo,
            number,
            head_sha=head_sha,
            event=event,
            findings=len(findings),
            blockers=blockers,
            inline=len(placed.inline),
            summary=summary_text,
        )

    def _publish(
        self,
        *,
        snapshot: PRSnapshot,
        bundle,
        findings: list[render.Finding],
        summary_text: str,
    ) -> bool:
        cfg = self.cfg
        number = snapshot.number
        head_sha = snapshot.head_sha

        event, invite = publish.decide_event(findings, cfg)

        manual_reason = ""
        if event == publish.EVENT_APPROVE:
            needs_human, reason = gates.needs_manual_approval(
                snapshot, cfg, bundle.changed_paths
            )
            if needs_human:
                event = publish.EVENT_COMMENT
                manual_reason = reason
                if notify.should_notify(cfg.notify_on, "manual_approval_needed"):
                    notify.send(
                        self.global_cfg.notifications,
                        "Approval waiting on you",
                        f"{cfg.repo}#{number} is clean but held: {reason}",
                    )

        placed = publish.place_comments(findings, bundle)
        blockers = sum(1 for f in findings if f.severity == "blocker")

        coverage = diff.manifest_note(bundle)
        if not cfg.gates.get("require_ci_green", True):
            # An approval on a red build with no mention of it misleads the team.
            # Say plainly that CI was not looked at.
            note = "CI status not verified — this review did not check whether the build passes"
            coverage = f"{coverage}. {note}" if coverage else note

        if event == publish.EVENT_APPROVE and not findings:
            body = render.approval_body(head_sha, summary_text, coverage)
        else:
            body = render.summary_body(
                head_sha=head_sha,
                summary_text=summary_text,
                findings=findings,
                event=event,
                coverage_note=coverage,
                invite_wave_off=invite,
                manual_reason=manual_reason,
            )
            body += publish.unplaceable_section(placed.unplaceable)

        claimed = self.store.begin_post(cfg.repo, number, head_sha, "review")
        if not claimed and not self.force:
            log.get().info(
                "%s#%s: a review for %s was already posted, not repeating it",
                cfg.repo,
                number,
                head_sha[:8],
            )
            return False

        if self.dry_run:
            log.get().info(
                "[dry-run] %s#%s would submit %s with %d inline comment(s)",
                cfg.repo,
                number,
                event,
                len(placed.inline),
            )
            self.debug.write(cfg.repo, number, "dry-run-review.md", body)
            # Recorded in the dry-run database only, so repeating the rehearsal
            # is free. The live database never sees it.
            self.store.finish_post(cfg.repo, number, head_sha, "review")
            self.store.record_review(cfg.repo, number, head_sha, f"DRY-RUN:{event}")
            self._log_review_event(
                number, head_sha, f"DRY-RUN:{event}", findings, blockers, placed, summary_text
            )
            return True

        ok = publish.submit_review(
            self.rest,
            cfg,
            number,
            head_sha,
            event=event,
            body=body,
            comments=placed.inline,
        )
        if not ok:
            self.store.abandon_post(cfg.repo, number, head_sha, "review")
            return False

        self.store.finish_post(cfg.repo, number, head_sha, "review")
        self.store.record_review(cfg.repo, number, head_sha, event)
        self._log_review_event(
            number, head_sha, event, findings, blockers, placed, summary_text
        )

        stale = threads.stale_thread_ids(snapshot, cfg, resolved_by_model=set())
        if stale:
            resolved, failed = publish.resolve_threads(self.graphql, stale)
            log.get().info(
                "%s#%s: resolved %d stale thread(s)%s",
                cfg.repo,
                number,
                resolved,
                f", {failed} failed" if failed else "",
            )

        summary = f"{event} — {len(findings)} finding(s)"
        if blockers:
            summary += f", {blockers} blocker(s)"
        if manual_reason:
            summary += f" · held: {manual_reason}"
        self.store.set_pr_action(cfg.repo, number, summary)

        log.get().info(
            "%s#%s: submitted %s — %d finding(s), %d inline",
            cfg.repo,
            number,
            event,
            len(findings),
            len(placed.inline),
        )

        if event == publish.EVENT_REQUEST_CHANGES and notify.should_notify(
            cfg.notify_on, "blocker_found"
        ):
            notify.send(
                self.global_cfg.notifications,
                "Blocker found",
                f"{cfg.repo}#{number}: changes requested",
            )
        elif event == publish.EVENT_APPROVE and notify.should_notify(
            cfg.notify_on, "approved"
        ):
            notify.send(
                self.global_cfg.notifications, "PR approved", f"{cfg.repo}#{number}"
            )
        elif notify.should_notify(cfg.notify_on, "review_posted"):
            notify.send(
                self.global_cfg.notifications, "Review posted", f"{cfg.repo}#{number}"
            )

        return True
