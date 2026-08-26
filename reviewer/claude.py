"""Calling the model.

The model is invoked as a pure function: text in, JSON out. It gets read-only
tools scoped to a clean checkout of the PR head so it can look at surrounding
code, and nothing else — no ``Bash``, no ``Write``, no network, and no GitHub
token anywhere in its environment.

Its working directory is a neutral empty scratch dir rather than the checkout.
That matters: Claude Code auto-loads ``CLAUDE.md`` from its working directory, so
running it inside the PR's tree would let a pull request feed instructions to its
own reviewer. Repo context is read from the default branch and injected as
delimited data instead (see ``prompt.py``).
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import log

FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

BLOCKED_TOOLS = [
    "Bash",
    "Write",
    "Edit",
    "MultiEdit",
    "NotebookEdit",
    "WebFetch",
    "WebSearch",
    "Task",
]

# Anything in this list is stripped from the child environment. The token is the
# one that matters; the rest is hygiene.
SENSITIVE_ENV = ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_API_URL", "GITHUB_GRAPHQL_URL")


class ClaudeError(RuntimeError):
    pass


# Model calls in flight, so quitting can stop them.
#
# Without this they outlive the tool: the reviewer runs on a daemon thread, and
# a daemon thread being abandoned at interpreter exit does nothing to a child
# process it started — that child is reparented and runs to completion, paying
# for a review whose result nobody is left to post.
_live: set[subprocess.Popen] = set()
_live_lock = threading.Lock()
_cancelled = threading.Event()


def _register(proc: subprocess.Popen) -> None:
    with _live_lock:
        _live.add(proc)


def _forget(proc: subprocess.Popen) -> None:
    with _live_lock:
        _live.discard(proc)


def stop_process(proc: subprocess.Popen, grace: float = 5.0) -> None:
    """End one model call, and anything it started.

    Signals the process group rather than the process: the CLI runs children of
    its own, and terminating only the parent would leave those behind — exactly
    the problem this exists to solve.
    """
    if proc.poll() is not None:
        return
    try:
        group = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        group = None

    try:
        if group is not None:
            os.killpg(group, signal.SIGTERM)
        else:
            proc.terminate()
    except (ProcessLookupError, PermissionError, OSError):
        return

    try:
        proc.wait(timeout=grace)
        return
    except subprocess.TimeoutExpired:
        pass

    try:
        if group is not None:
            os.killpg(group, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass


def terminate_all(grace: float = 5.0) -> int:
    """Stop every model call in flight. Returns how many there were.

    Called when the tool is quitting. Anything killed here was going to be
    thrown away regardless — the code that would have posted its review is on
    its way out — so the only thing letting it finish would buy is the bill.
    """
    _cancelled.set()
    with _live_lock:
        live = list(_live)
    for proc in live:
        stop_process(proc, grace)
    return len(live)


def live_count() -> int:
    """Model calls running right now, across every thread.

    Covers reviews, thread replies and merge summaries alike — anything that
    reached the CLI — so a quit confirmation can say what it is about to end
    without each caller having to register itself separately.
    """
    with _live_lock:
        return len(_live)


def cancelled() -> bool:
    """Whether calls were stopped because the tool is shutting down."""
    return _cancelled.is_set()


@dataclass
class ModelResult:
    payload: dict[str, Any]
    raw: str
    usage: dict[str, Any]
    duration_seconds: float


def _build_command(cfg: dict[str, Any], system_prompt: str, add_dir: Path | None) -> list[str]:
    # An explicit empty list means "no tools", which is what the merge summary
    # wants; only an absent or null setting falls back to the read-only default.
    allowed = cfg.get("allowed_tools")
    if allowed is None:
        allowed = ["Read", "Glob", "Grep"]
    cmd = [
        cfg.get("command", "claude"),
        "-p",
        "--output-format",
        "json",
        "--append-system-prompt",
        system_prompt,
    ]
    if allowed:
        cmd += ["--allowedTools", ",".join(allowed)]
    cmd += ["--disallowedTools", ",".join(BLOCKED_TOOLS)]
    if cfg.get("model"):
        cmd += ["--model", str(cfg["model"])]
    if add_dir is not None:
        cmd += ["--add-dir", str(add_dir)]
    cmd += [str(arg) for arg in (cfg.get("extra_args") or [])]
    return cmd


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in SENSITIVE_ENV:
        env.pop(key, None)
    return env


def extract_json(text: str) -> dict[str, Any]:
    """Pull a JSON object out of the model's reply.

    Handles a bare object, a fenced block, and an object with prose either side —
    all three show up in practice, and a review is too expensive to throw away
    over a stray "Here you go:".
    """
    text = (text or "").strip()
    if not text:
        raise ClaudeError("model returned nothing")

    candidates: list[str] = []
    if text.startswith("{"):
        candidates.append(text)
    candidates.extend(match.group(1) for match in FENCE_RE.finditer(text))

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise ClaudeError(f"could not parse JSON from model output: {text[:300]}")


def run(
    cfg: dict[str, Any],
    *,
    system_prompt: str,
    user_prompt: str,
    add_dir: Path | None = None,
) -> ModelResult:
    """One model call. Returns the parsed JSON payload plus usage figures."""
    import time

    command = _build_command(cfg, system_prompt, add_dir)
    timeout = int(cfg.get("timeout_seconds") or 900)

    with tempfile.TemporaryDirectory(prefix="pr-reviewer-cwd-") as scratch:
        started = time.monotonic()
        try:
            # Popen rather than run(), so the handle can be registered and the
            # call stopped when the tool is asked to quit. Its own session, so
            # terminating it takes the whole tree with it — the CLI spawns
            # children of its own — and so that a Ctrl-C in the terminal is
            # something this code decides about rather than something the shell
            # delivers behind its back.
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=scratch,
                env=_child_env(),
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise ClaudeError(
                f"{command[0]!r} is not on PATH. Install the Claude Code CLI and "
                "sign in, or set claude.command in config/global.json."
            ) from exc

        _register(proc)
        try:
            stdout, stderr = proc.communicate(input=user_prompt, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            stop_process(proc)
            raise ClaudeError(f"model call timed out after {timeout}s") from exc
        finally:
            _forget(proc)
        duration = time.monotonic() - started

    if proc.returncode != 0:
        if _cancelled.is_set():
            # Not a failure. We stopped it on the way out, and saying "claude
            # exited -15" would read as a crash.
            raise ClaudeError("model call stopped — shutting down")
        detail = (stderr or stdout or "").strip()[:600]
        raise ClaudeError(f"claude exited {proc.returncode}: {detail}")

    stdout = stdout or ""

    # --output-format json wraps the reply; older CLI versions print the text
    # directly. Handle both rather than pinning to one CLI release.
    usage: dict[str, Any] = {}
    body = stdout
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError:
        envelope = None

    if isinstance(envelope, dict):
        if envelope.get("is_error"):
            raise ClaudeError(f"claude reported an error: {str(envelope)[:400]}")
        if isinstance(envelope.get("result"), str):
            body = envelope["result"]
        usage = envelope.get("usage") or {}
        for key in ("total_cost_usd", "num_turns", "duration_ms"):
            if key in envelope:
                usage[key] = envelope[key]
    elif isinstance(envelope, dict) is False and stdout.strip().startswith("{"):
        body = stdout

    payload = extract_json(body)

    log.get().debug(
        "model call finished in %.1fs (%s)",
        duration,
        ", ".join(f"{k}={v}" for k, v in usage.items()) or "no usage reported",
    )
    return ModelResult(payload=payload, raw=body, usage=usage, duration_seconds=duration)
