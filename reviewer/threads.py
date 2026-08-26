"""Threads the author has pushed back on.

The behaviour this implements: when someone replies to one of our comments, the
reply gets checked against the code before anything happens. If they are right,
say so plainly, point at what showed it, and resolve. If they are not, reply with
the file and line that contradicts them. If it cannot be settled from the code,
ask one specific question.

Resolving on the strength of an assertion is what this exists to prevent — the
model has to be able to point at something.

The round cap is per thread. One thread going in circles gets parked for a human;
every other thread and every later review round carries on untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import claude, log, prompt, render
from .config import GlobalConfig, RepoConfig
from .gh import GraphQLClient, RestClient
from .gh.graphql import PRSnapshot, ReviewThread
from .log import DebugSink
from .state import Store

VERDICT_AGREE = "author_is_right"
VERDICT_DISAGREE = "finding_stands"
VERDICT_UNCLEAR = "needs_more_information"


@dataclass
class ThreadOutcome:
    thread_id: str
    verdict: str
    reply: str
    resolve: bool
    capped: bool = False


def pending_threads(
    snapshot: PRSnapshot, cfg: RepoConfig, store: Store
) -> list[ReviewThread]:
    """Threads we started where someone else spoke last and we have not replied."""
    out: list[ReviewThread] = []
    for thread in snapshot.threads:
        if thread.is_resolved or not thread.awaiting_us(cfg.identity):
            continue
        if store.is_thread_capped(cfg.repo, snapshot.number, thread.node_id):
            continue
        last = thread.last_comment
        if last is None:
            continue
        seen = store.thread_last_seen(cfg.repo, snapshot.number, thread.node_id)
        if seen == last.node_id:
            continue
        out.append(thread)
    return out


def _parse(payload: dict[str, Any]) -> tuple[str, str, bool]:
    verdict = str(payload.get("verdict") or VERDICT_UNCLEAR).strip().lower()
    if verdict not in (VERDICT_AGREE, VERDICT_DISAGREE, VERDICT_UNCLEAR):
        verdict = VERDICT_UNCLEAR
    reply = str(payload.get("reply") or "").strip()
    evidence = str(payload.get("evidence") or "").strip()

    # Resolving requires the model to have pointed at something. An agreement
    # with no evidence is an opinion, and opinions do not close threads.
    resolve = verdict == VERDICT_AGREE and bool(evidence)
    if verdict == VERDICT_AGREE and not evidence:
        log.get().info("agreement without evidence — leaving the thread open")
    return verdict, reply, resolve


def handle(
    *,
    thread: ReviewThread,
    snapshot: PRSnapshot,
    cfg: RepoConfig,
    global_cfg: GlobalConfig,
    rest: RestClient,
    graphql: GraphQLClient,
    store: Store,
    checkout_path: Path | None,
    personality_dir: Path,
    repo_context: dict[str, str] | None,
    debug: DebugSink,
) -> ThreadOutcome | None:
    cap = int(cfg.review.get("max_disagreement_rounds_per_thread") or 3)
    rounds_used = store.get_thread_rounds(cfg.repo, snapshot.number, thread.node_id)

    system = prompt.build_system(personality_dir, cfg, repo_context)
    user = prompt.build_thread_user_prompt(
        cfg=cfg,
        snapshot=snapshot,
        thread=thread,
        checkout_path=checkout_path,
        rounds_used=rounds_used,
        rounds_allowed=cap,
    )

    debug.write(cfg.repo, snapshot.number, f"thread-{thread.node_id[:8]}-prompt.md", user)

    try:
        result = claude.run(
            global_cfg.claude,
            system_prompt=system,
            user_prompt=user,
            add_dir=checkout_path,
        )
    except claude.ClaudeError as exc:
        log.get().error("thread check failed on #%s: %s", snapshot.number, exc)
        return None

    debug.write(
        cfg.repo, snapshot.number, f"thread-{thread.node_id[:8]}-response.json", result.payload
    )

    verdict, reply, resolve = _parse(result.payload)
    last = thread.last_comment
    if last is None:
        return None

    rounds, capped = (rounds_used, False)
    if verdict == VERDICT_DISAGREE:
        rounds, capped = store.bump_thread_round(
            cfg.repo, snapshot.number, thread.node_id, cap
        )

    if capped:
        body = render.capped_notice(reply or "We see this differently.")
        resolve = False
    else:
        body = render.thread_reply_body(reply, resolving=resolve)

    if reply and last.database_id:
        try:
            rest.reply_to_review_comment(
                cfg.owner, cfg.name, snapshot.number, last.database_id, body
            )
        except Exception as exc:  # noqa: BLE001 - one bad thread must not stop the rest
            log.get().error("could not reply to thread %s: %s", thread.node_id, exc)

    if resolve:
        graphql.resolve_thread(thread.node_id)

    store.mark_thread_seen(cfg.repo, snapshot.number, thread.node_id, last.node_id)

    log.get().info(
        "#%s thread %s: %s%s",
        snapshot.number,
        thread.node_id[:8],
        verdict,
        " (capped, parked for a human)" if capped else "",
    )

    return ThreadOutcome(
        thread_id=thread.node_id,
        verdict=verdict,
        reply=reply,
        resolve=resolve,
        capped=capped,
    )


def stale_thread_ids(
    snapshot: PRSnapshot, cfg: RepoConfig, resolved_by_model: set[str]
) -> list[str]:
    """Our outdated threads that nobody has replied to.

    An outdated thread points at lines that have since changed. With no reply to
    weigh, the code moving on is enough to close it — it tells the author the
    point is dealt with, and keeps the conversation list honest.
    """
    ids: list[str] = []
    for thread in snapshot.threads:
        if thread.is_resolved or thread.node_id in resolved_by_model:
            continue
        if not thread.is_ours(cfg.identity):
            continue
        if not thread.is_outdated:
            continue
        if len(thread.comments) > 1:
            continue
        ids.append(thread.node_id)
    return ids
