"""Read-only checkouts of PR heads, borrowed from your everyday clone.

The point of this module is to give the model somewhere to read — so it can
check whether a change follows the conventions of the code around it — without
disturbing the clone you work in.

What it does to your repository:

* fetches into ``refs/reviewer/*`` only, with ``--no-write-fetch-head``
* creates a detached worktree in ``$TMPDIR``
* removes the worktree and deletes the ref when the review ends
* prunes stale worktrees at startup, so a crashed run cleans up on the next one

What it never touches: ``refs/heads/*``, ``refs/remotes/*``, the index, the
working tree, ``HEAD``, or the stash.

A side effect worth having: because the worktree is a clean tree at the PR's head
SHA, your uncommitted work and local ``.env`` files are not visible to the model.
"""

from __future__ import annotations

import fnmatch
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from . import log

REF_NAMESPACE = "refs/reviewer"
GIT_TIMEOUT = 300


class GitError(RuntimeError):
    pass


def _run(
    repo: Path,
    args: list[str],
    *,
    check: bool = True,
    retries: int = 3,
) -> subprocess.CompletedProcess[str]:
    """Run a git command against ``repo``.

    Never ``cd``s and never uses a bare ``git`` — every call is scoped with
    ``-C`` so it cannot accidentally act on the wrong repository. Retries on lock
    contention, since you may well be running a git command at the same time.
    """
    cmd = ["git", "-C", str(repo), *args]
    last: subprocess.CompletedProcess[str] | None = None

    for attempt in range(retries):
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )
        last = proc
        if proc.returncode == 0:
            return proc
        stderr = (proc.stderr or "").lower()
        if "lock" in stderr and attempt < retries - 1:
            wait = 2 ** attempt
            log.get().debug(
                "git lock contention on %s, retrying in %ds", " ".join(args[:2]), wait
            )
            time.sleep(wait)
            continue
        break

    assert last is not None
    if check:
        raise GitError(
            f"git {' '.join(args)} failed ({last.returncode}): "
            f"{(last.stderr or last.stdout or '').strip()[:400]}"
        )
    return last


WORKTREE_PREFIX = "pr-reviewer-"


def _registered_worktrees(repo: Path) -> list[Path]:
    proc = _run(repo, ["worktree", "list", "--porcelain"], check=False)
    if proc.returncode != 0:
        return []
    return [
        Path(line.split(" ", 1)[1].strip())
        for line in (proc.stdout or "").splitlines()
        if line.startswith("worktree ")
    ]


def _is_ours(path: Path) -> bool:
    return path.name.startswith(WORKTREE_PREFIX)


def safe_prune(repo: Path) -> None:
    """Prune, but never someone else's worktree.

    ``git worktree prune`` is repository-wide: it deregisters *every* worktree
    whose directory is currently missing. That is fine in a repo where this tool
    is the only thing making worktrees, and not fine in one where an editor or
    another agent has its own — a worktree on a detached volume, or one an editor
    is between operations on, would be quietly unregistered.

    So: remove our own by path, and only fall through to the global prune when
    nothing stale belongs to anyone else.
    """
    registered = _registered_worktrees(repo)

    for path in registered:
        if _is_ours(path) and path.exists():
            _run(repo, ["worktree", "remove", "--force", str(path)], check=False)

    foreign_stale = [p for p in registered if not _is_ours(p) and not p.exists()]
    if foreign_stale:
        log.get().debug(
            "skipping `git worktree prune` — %d worktree(s) not ours are stale: %s",
            len(foreign_stale),
            ", ".join(str(p) for p in foreign_stale[:3]),
        )
        return

    _run(repo, ["worktree", "prune"], check=False)


def prune_stale(repo: Path) -> None:
    """Clear worktrees and refs left behind by an interrupted run."""
    safe_prune(repo)
    proc = _run(
        repo, ["for-each-ref", "--format=%(refname)", REF_NAMESPACE], check=False
    )
    for ref in (proc.stdout or "").splitlines():
        ref = ref.strip()
        if ref:
            _run(repo, ["update-ref", "-d", ref], check=False)


@dataclass
class Checkout:
    path: Path
    sha: str
    ref: str


@contextmanager
def pr_checkout(repo: Path, pr_number: int, head_sha: str) -> Iterator[Checkout]:
    """Detached worktree at a PR's head, removed on the way out."""
    ref = f"{REF_NAMESPACE}/pr-{pr_number}"
    workdir = Path(tempfile.mkdtemp(prefix=f"pr-reviewer-{pr_number}-"))
    created = False

    try:
        _run(
            repo,
            [
                "fetch",
                "--no-tags",
                "--no-write-fetch-head",
                "--force",
                "origin",
                f"+refs/pull/{pr_number}/head:{ref}",
            ],
        )
        _run(repo, ["worktree", "add", "--detach", "--force", str(workdir), head_sha])
        created = True
        log.get().debug("worktree for #%s at %s", pr_number, workdir)
        yield Checkout(path=workdir, sha=head_sha, ref=ref)
    finally:
        if created:
            _run(repo, ["worktree", "remove", "--force", str(workdir)], check=False)
        shutil.rmtree(workdir, ignore_errors=True)
        safe_prune(repo)
        _run(repo, ["update-ref", "-d", ref], check=False)


def fetch_base_ref(repo: Path, branch: str) -> str:
    """Fetch a branch into ``refs/reviewer/base-*`` and return the ref name.

    Used to read the repository's own agent docs from code that has already
    been reviewed, rather than from the branch under review.
    """
    ref = f"{REF_NAMESPACE}/base-{branch.replace('/', '-')}"
    _run(
        repo,
        [
            "fetch",
            "--no-tags",
            "--no-write-fetch-head",
            "--force",
            "origin",
            f"+refs/heads/{branch}:{ref}",
        ],
    )
    return ref


def read_trusted_files(
    repo: Path, ref: str, patterns: list[str], max_chars: int
) -> dict[str, str]:
    """Read files matching ``patterns`` from ``ref`` without checking anything out.

    Reads straight out of the object database, so it needs no worktree and cannot
    see anything that is not committed at that ref.
    """
    listing = _run(repo, ["ls-tree", "-r", "--name-only", ref], check=False)
    if listing.returncode != 0:
        return {}

    paths = [p for p in (listing.stdout or "").splitlines() if p.strip()]
    matched = [
        path
        for path in paths
        if any(fnmatch.fnmatch(path, pattern) for pattern in patterns)
    ]

    out: dict[str, str] = {}
    budget = max_chars
    for path in sorted(matched):
        if budget <= 0:
            break
        blob = _run(repo, ["show", f"{ref}:{path}"], check=False)
        if blob.returncode != 0:
            continue
        content = blob.stdout or ""
        if len(content) > budget:
            content = content[:budget] + "\n\n[truncated]"
        out[path] = content
        budget -= len(content)
    return out


def resolve_default_branch(repo: Path, fallback: str = "main") -> str:
    proc = _run(repo, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], check=False)
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip().split("/", 1)[-1]
    return fallback
