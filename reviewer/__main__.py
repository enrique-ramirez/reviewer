"""Entry point: the loop, the lockfile, and the walk across repositories.

Run it in one terminal window and leave it there.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from types import FrameType
from typing import Any

from . import (
    backfill,
    conversation,
    identity,
    log,
    model,
    notify,
    state,
    summarize,
    worktree,
)
from .config import ConfigError, GlobalConfig, RepoConfig, load_env, load_repos
from .gh import GraphQLClient, RestClient
from .log import DebugSink
from .pipeline import Reviewer, TickResult

REPO_ROOT = Path(__file__).resolve().parent.parent
_stop = False


def _handle_signal(signum: int, _frame: FrameType | None) -> None:
    global _stop
    _stop = True
    # Whatever the model is working on is abandoned rather than finished: the
    # code that would post its review is on its way out, so letting it run to
    # completion would buy the bill and nothing else.
    stopped = model.terminate_all()
    log.get().info(
        "signal %s received — stopping%s",
        signum,
        f", ended {stopped} model call(s) in flight" if stopped else "",
    )


class Lock:
    """A PID lockfile.

    A tick that lands while the previous one is still working is skipped rather
    than queued. A review can take ten minutes without three more piling up
    behind it.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.acquired = False

    def __enter__(self) -> "Lock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                pid = int(self.path.read_text().strip())
            except (ValueError, OSError):
                pid = -1
            if pid > 0 and _pid_alive(pid):
                raise RuntimeError(f"another run is active (pid {pid})")
            log.get().warning("clearing a stale lockfile from pid %s", pid)
            self.path.unlink(missing_ok=True)
        self.path.write_text(str(os.getpid()))
        self.acquired = True
        return self

    def __exit__(self, *_exc: object) -> None:
        if self.acquired:
            self.path.unlink(missing_ok=True)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="blinky",
        description="Review pull requests on a schedule, from your own machine.",
    )
    parser.add_argument("--once", action="store_true", help="one pass, then exit")
    parser.add_argument(
        "--init",
        action="store_true",
        help="write the config files by asking a few questions, then check the "
        "token. Safe to re-run: it never overwrites a file that exists",
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help="run the reviewer and the board together in one window. ./run.sh "
        "does this by default",
    )
    parser.add_argument(
        "--lean",
        action="store_true",
        help="plain scrolling log, no dashboard and no dependencies. Read by "
        "./run.sh, which otherwise starts the dashboard",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="review even when the gates say to skip (draft, blocking label, "
        "already reviewed). For testing; pair with --pr and --dry-run.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="probe what the token can reach and name any missing permission, "
        "then exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="do everything except write to GitHub; the drafted review is saved "
        "to the state directory",
    )
    parser.add_argument(
        "--backfill",
        nargs="?",
        const="ask",
        metavar="RANGE",
        help="fill in merge history that predates this tool, then exit. RANGE is "
        "one of: " + ", ".join(k for k, _, _ in backfill.RANGES) + ". Records "
        "everything merged, not only what this tool reviewed, and makes no model "
        "calls. Asks before starting; pair with --yes to skip the prompt.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="answer yes to the backfill confirmation (for scripts)",
    )
    parser.add_argument("--repo", help="restrict to one configured repo (owner/name)")
    parser.add_argument(
        "--pr", type=int, help="restrict to one PR number (implies --once, needs --repo)"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="write full prompts and model responses to the state directory",
    )
    parser.add_argument("--state-dir", type=Path, help="override the state directory")
    parser.add_argument(
        "--config-dir", type=Path, default=REPO_ROOT / "config", help="config directory"
    )
    parser.add_argument(
        "--personality-dir",
        type=Path,
        default=REPO_ROOT / "personality",
        help="personality directory",
    )
    return parser.parse_args(argv)


def run_tick(
    repos: list[RepoConfig],
    global_cfg: GlobalConfig,
    store: state.Store,
    personality_dir: Path,
    debug: DebugSink,
    *,
    dry_run: bool,
    force: bool,
    only_repo: str | None,
    only_pr: int | None,
    status_cb: Any = None,
) -> TickResult:
    total = TickResult()
    budget = global_cfg.max_reviews_per_tick

    for cfg in repos:
        if only_repo and cfg.repo.lower() != only_repo.lower():
            continue
        if budget <= 0:
            log.get().info("per-tick review budget spent; remaining repos wait")
            break
        if _stop:
            break

        rest = RestClient(
            global_cfg.token, global_cfg.api_url, store, dry_run=dry_run
        )
        graphql = GraphQLClient(
            global_cfg.token, global_cfg.graphql_url, dry_run=dry_run
        )
        reviewer = Reviewer(
            cfg=cfg,
            global_cfg=global_cfg,
            rest=rest,
            graphql=graphql,
            store=store,
            personality_dir=personality_dir,
            debug=debug,
            dry_run=dry_run,
            force=force,
            status_cb=status_cb,
        )

        result = reviewer.tick(budget=budget, only_pr=only_pr)
        budget -= result.reviewed
        total.reviewed += result.reviewed
        total.skipped += result.skipped
        total.errors += result.errors

    return total


def _choose_range(preset: str) -> str | None:
    """Which slice of history to fetch. Returns None if the user backs out."""
    if preset != "ask":
        if preset not in backfill.RANGE_DAYS:
            print(
                f"unknown range {preset!r}. Pick one of: "
                f"{', '.join(backfill.RANGE_DAYS)}",
                file=sys.stderr,
            )
            return None
        return preset

    print("\n  How far back?\n")
    for index, (key, label, _days) in enumerate(backfill.RANGES, start=1):
        print(f"    {index}) {label:<16} ({key})")
    print()
    try:
        answer = input("  Which? [1] ").strip() or "1"
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if answer.isdigit() and 1 <= int(answer) <= len(backfill.RANGES):
        return backfill.RANGES[int(answer) - 1][0]
    if answer in backfill.RANGE_DAYS:
        return answer
    print(f"  not a choice: {answer}", file=sys.stderr)
    return None


def _run_backfill(
    *,
    repos: list[RepoConfig],
    global_cfg: GlobalConfig,
    store: state.Store,
    preset: str,
    assume_yes: bool,
) -> int:
    """Fill in merge history, once, because the user asked.

    Kept out of the tick deliberately: this reaches back through a repository's
    whole past, and how far back is a judgement only the person running it can
    make. It says what it will cost before it does any of it.
    """
    logger = log.get()
    range_key = _choose_range(preset)
    if range_key is None:
        return 2

    graphql = GraphQLClient(global_cfg.token, global_cfg.graphql_url)

    print()
    plans = []
    total = 0
    unknown = False
    for cfg in repos:
        plan = backfill.plan(graphql, store, cfg.repo, range_key)
        plans.append((cfg, plan))
        print(f"  {plan.describe()}")
        if plan.count is None:
            unknown = True
        else:
            total += plan.count
        if plan.already_filed:
            covered = plan.already_covered_since
            span = "all time" if covered is None else f"back to {_ymd(covered)}"
            print(
                f"    already have {plan.already_filed:,} on record ({span}); "
                "anything already filed is left alone"
            )

    if not unknown and total == 0:
        print("\n  Nothing to fetch. History is already up to date.\n")
        return 0

    print()
    if total >= backfill.LARGE:
        print(
            f"  That is {total:,} pull requests — a large sweep. It makes no "
            "model calls,\n  so the cost is time and API quota rather than "
            "tokens, but it will take\n  a few minutes and can be stopped with "
            "Ctrl-C without losing what it got.\n"
        )

    if not assume_yes:
        try:
            if input("  Go ahead? [y/N] ").strip().lower() not in ("y", "yes"):
                print("  Left alone.\n")
                return 0
        except (EOFError, KeyboardInterrupt):
            print("\n  Left alone.\n")
            return 0
        print()

    # Ctrl-C sets the same flag a tick watches, so a long sweep stops between
    # pages and keeps every page it already committed.
    def should_stop() -> bool:
        return _stop

    filed_total = 0
    for cfg, plan in plans:
        if should_stop():
            break

        def progress(filed: int, scanned: int, _repo: str = cfg.repo) -> None:
            print(f"\r  {_repo}: {filed:,} filed, {scanned:,} scanned…", end="", flush=True)

        result = backfill.run(
            graphql,
            store,
            plan,
            identity=cfg.identity,
            progress=progress,
            should_stop=should_stop,
        )
        filed_total += result.filed
        note = " (stopped)" if result.stopped_early else ""
        print(
            f"\r  {cfg.repo}: {result.filed:,} filed, {result.skipped:,} already "
            f"had, {result.pages} page(s){note}"
        )
        for message in result.errors:
            logger.warning("%s: %s", cfg.repo, message)

    print(f"\n  Done — {filed_total:,} added to the history.\n")
    return 0


def _ymd(epoch: float) -> str:
    return time.strftime("%d %b %Y", time.localtime(epoch))


def _run_with_tui(
    *,
    repos: list[RepoConfig],
    global_cfg: GlobalConfig,
    store: state.Store,
    state_dir: Path,
    personality_dir: Path,
    debug: DebugSink,
    args: argparse.Namespace,
    lock_path: Path,
) -> int:
    """Run the reviewer and the board together in one window.

    The reviewer runs on a background thread and curses owns the main one, which
    is the way round curses wants it. Each side gets its own SQLite connection —
    the database is in WAL mode, so a reader never blocks the writer and the
    board stays responsive through a five-minute model call.

    The log is detached from stdout while this runs; writing to the terminal
    underneath curses would corrupt the display. It still goes to the logfile,
    and the bottom pane shows it live.
    """
    try:
        from . import tui
    except ImportError as exc:
        print(
            f"--tui needs Textual, which is not installed ({exc}).\n"
            "  pip install textual\n"
            "Everything else — ./run.sh, --once, --check — works without it.",
            file=sys.stderr,
        )
        return 1


    logger = log.get()
    pane = tui.LogRelay()
    pane.setLevel(logging.INFO)

    stdout_handlers = [
        h
        for h in logger.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler)
    ]
    for handler in stdout_handlers:
        logger.removeHandler(handler)
    logger.addHandler(pane)

    stop = threading.Event()
    status: dict[str, Any] = {"phase": "starting…", "remaining": None, "total": None}
    # Taken before the worker starts, so nothing it notices can predate it and
    # fall outside the Summary tab's "this run".
    run_started = time.time()
    db_name = "state-dryrun.sqlite3" if args.dry_run else "state.sqlite3"

    def worker() -> None:
        # A sqlite3 connection belongs to the thread that opened it, so the
        # reviewer opens its own here rather than borrowing the one the main
        # thread made. WAL means this writer and the interface's reader never
        # block each other.
        work_store = state.Store(state_dir, db_name)
        try:
            while not stop.is_set():
                started = time.monotonic()
                try:
                    with Lock(lock_path):
                        result = run_tick(
                            repos,
                            global_cfg,
                            work_store,
                            personality_dir,
                            debug,
                            dry_run=args.dry_run,
                            force=args.force,
                            only_repo=args.repo,
                            only_pr=args.pr,
                            status_cb=lambda text: status.update(
                                phase=text, remaining=None, total=None
                            ),
                        )
                    logger.info(
                        "pass finished in %.0fs — %d reviewed, %d skipped, %d error(s)",
                        time.monotonic() - started,
                        result.reviewed,
                        result.skipped,
                        result.errors,
                    )
                except RuntimeError as exc:
                    logger.info("skipping this pass: %s", exc)
                except Exception as exc:  # noqa: BLE001 - a dead thread must not hang the UI
                    logger.exception("tick failed: %s", exc)

                next_at = time.monotonic() + global_cfg.tick_seconds
                while not stop.is_set() and time.monotonic() < next_at:
                    status.update(
                        phase="waiting",
                        remaining=max(0.0, next_at - time.monotonic()),
                        total=float(global_cfg.tick_seconds),
                    )
                    stop.wait(0.5)
        finally:
            work_store.close()

    thread = threading.Thread(target=worker, name="reviewer", daemon=True)
    thread.start()

    # The interface reads through its own connection so it never contends with
    # the reviewer's writes.
    ui_store = state.Store(state_dir, db_name)

    # A backfill from the History tab runs on a third thread with a third
    # connection: it talks to GitHub, which must not happen on the event loop,
    # and it must not wait for a review that is halfway through a model call.
    backfiller = backfill.Runner(
        open_store=lambda: state.Store(state_dir, db_name),
        graphql=GraphQLClient(global_cfg.token, global_cfg.graphql_url),
        repos=repos,
    )
    # And summarising a backfilled merge runs on a fourth, for the same
    # reasons: it makes a model call, which is the slowest thing in the tool.
    summariser = summarize.Runner(
        open_store=lambda: state.Store(state_dir, db_name),
        global_cfg=global_cfg,
        repos=repos,
        dry_run=args.dry_run,
    )
    # Reading a review conversation is a fifth thread, for the same reason as
    # the others: it talks to GitHub, which must not happen on the event loop.
    conversations = conversation.Runner(
        open_store=lambda: state.Store(state_dir, db_name),
        global_cfg=global_cfg,
        repos=repos,
        dry_run=args.dry_run,
    )
    try:
        tui.run(
            tui.Runtime(
                store=ui_store,
                repos=tuple(r.repo for r in repos),
                relay=pane,
                stop=stop,
                status=lambda: dict(status),
                started_at=run_started,
                backfiller=backfiller,
                summariser=summariser,
                conversations=conversations,
            )
        )
    finally:
        stop.set()
        backfiller.cancel()
        summariser.cancel()
        # Before the join, not after: the reviewer thread is most likely sitting
        # inside a model call that will not return for minutes, and the join
        # would time out and leave the child running as an orphan. Killing it
        # first means the thread comes back promptly and with nothing to post.
        stopped = model.terminate_all()
        if stopped:
            logger.info("stopped %d model call(s) in flight", stopped)
        ui_store.close()
        thread.join(timeout=5)
        logger.removeHandler(pane)
        for handler in stdout_handlers:
            logger.addHandler(handler)
        store.close()

    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if args.pr is not None and not args.repo:
        print("--pr needs --repo", file=sys.stderr)
        return 2
    once = args.once or args.pr is not None

    state_dir = args.state_dir or state.default_state_dir()
    state_dir = Path(state_dir).expanduser()
    moved_from = state.adopt_legacy_state_dir(state_dir)
    logger = log.setup("INFO", state_dir / "reviewer.log")
    if moved_from is not None:
        logger.info("state moved from %s to %s (the project was renamed)",
                    moved_from, state_dir)

    # Before anything reads config: this is the command that writes it.
    if args.init:
        from . import bootstrap

        return bootstrap.run(args.config_dir, REPO_ROOT)

    try:
        load_env(args.config_dir, REPO_ROOT)
        global_cfg = GlobalConfig.load(args.config_dir)
        repos = load_repos(args.config_dir, global_cfg)
    except ConfigError as exc:
        logger.error("%s", exc)
        return 1

    log.setup(global_cfg.logging.get("level", "INFO"), state_dir / "reviewer.log")

    if args.check:
        from . import preflight

        targets = [
            r for r in repos if not args.repo or r.repo.lower() == args.repo.lower()
        ]
        if not targets:
            logger.error("no configured repo matches --repo %s", args.repo)
            return 2
        # Once, ahead of the per-repository probes: the account and the model
        # CLI are the same for all of them.
        worst = preflight.environment(global_cfg, targets)
        for cfg in targets:
            print()
            worst = max(worst, preflight.run(global_cfg, cfg, args.pr))
        return worst

    try:
        repos = identity.resolve(repos, global_cfg)
    except ConfigError as exc:
        logger.error("%s", exc)
        return 1

    debug = DebugSink(state_dir / "debug" if args.debug else None)
    if args.debug:
        logger.warning(
            "--debug is on: full prompts and responses will be written to %s",
            state_dir / "debug",
        )

    store = state.Store(
        state_dir, "state-dryrun.sqlite3" if args.dry_run else "state.sqlite3"
    )
    store.prune_cache()
    if args.dry_run:
        logger.info(
            "dry run: state goes to %s, so repeating a dry run is free and the "
            "live database is untouched",
            store.path.name,
        )

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # Before the watch loop: a backfill is a one-off errand, not a mode to run
    # in. It reports what it will fetch, asks, does it, and exits.
    if args.backfill:
        try:
            return _run_backfill(
                repos=repos,
                global_cfg=global_cfg,
                store=store,
                preset=args.backfill,
                assume_yes=args.yes,
            )
        finally:
            store.close()

    logger.info(
        "watching %s every %ds%s",
        ", ".join(r.repo for r in repos),
        global_cfg.tick_seconds,
        " (dry run)" if args.dry_run else "",
    )

    lock_path = state_dir / "reviewer.lock"
    exit_code = 0

    # --lean wins: it is the explicit request for no dashboard, and run.sh
    # may have added --tui before seeing it.
    if args.tui and not args.lean:
        return _run_with_tui(
            repos=repos,
            global_cfg=global_cfg,
            store=store,
            state_dir=state_dir,
            personality_dir=args.personality_dir,
            debug=debug,
            args=args,
            lock_path=lock_path,
        )

    try:
        while True:
            started = time.monotonic()
            try:
                with Lock(lock_path):
                    result = run_tick(
                        repos,
                        global_cfg,
                        store,
                        args.personality_dir,
                        debug,
                        dry_run=args.dry_run,
                        force=args.force,
                        only_repo=args.repo,
                        only_pr=args.pr,
                    )
                logger.info(
                    "pass finished in %.0fs — %d reviewed, %d skipped, %d error(s)",
                    time.monotonic() - started,
                    result.reviewed,
                    result.skipped,
                    result.errors,
                )
            except RuntimeError as exc:
                logger.info("skipping this pass: %s", exc)

            if once or _stop:
                break

            sleep_for = max(5, global_cfg.tick_seconds - (time.monotonic() - started))
            deadline = time.monotonic() + sleep_for
            while time.monotonic() < deadline and not _stop:
                time.sleep(min(2, deadline - time.monotonic()))
            if _stop:
                break
    except Exception as exc:  # noqa: BLE001 - last resort, so the log gets it
        logger.exception("fatal: %s", exc)
        notify.send(
            global_cfg.notifications, "PR reviewer stopped", str(exc)[:200]
        )
        exit_code = 1
    finally:
        store.close()
        for cfg in repos:
            if cfg.local_path:
                try:
                    worktree.prune_stale(cfg.local_path)
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    pass
        logger.info("stopped")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
