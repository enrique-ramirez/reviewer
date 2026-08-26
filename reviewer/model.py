"""Calling the model.

The model is invoked as a pure function: text in, JSON out. It gets read-only
access to a clean checkout of the PR head so it can look at surrounding code,
and nothing else — no shell it can write with, no network, and no GitHub token
anywhere in its environment.

Its working directory is a neutral empty scratch dir rather than the checkout.
That matters: every one of these CLIs auto-loads instructions from the directory
it starts in — ``CLAUDE.md``, ``AGENTS.md``, ``GEMINI.md`` — so running one
inside the PR's tree would let a pull request feed instructions to its own
reviewer. Repo context is read from the default branch and injected as delimited
data instead (see ``prompt.py``).

Which CLI actually runs is a config question, answered in ``providers.py``.
Everything in this module is true whichever one it is: one process, one prompt
on stdin, one JSON object back, and a handle held so the call can be stopped
when the tool quits.
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

from . import clock, log, providers

FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

# Anything in this list is stripped from the child environment. The token is the
# one that matters; the rest is hygiene.
SENSITIVE_ENV = ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_API_URL", "GITHUB_GRAPHQL_URL")


class ModelError(RuntimeError):
    pass


# Model calls in flight, so quitting can stop them.
#
# Without this they outlive the tool: the reviewer runs on a daemon thread, and
# a daemon thread being abandoned at interpreter exit does nothing to a child
# process it started — that child is reparented and runs to completion, paying
# for a review whose result nobody is left to post.
#
# Labelled ``repo#number`` so the dashboard can stop one of them by name. A
# review is minutes of work and several dollars of quota, so "stop that one"
# wants to be a different gesture from "stop everything".
_live: dict[subprocess.Popen, str] = {}
_live_lock = threading.Lock()
_cancelled = threading.Event()
# Labels cancelled by hand, so the failure that follows can say so rather than
# reading as a crash. Drained by whichever call notices it, since the label is
# free to be reused by the next tick.
_cancelled_labels: set[str] = set()


def _register(proc: subprocess.Popen, label: str) -> None:
    with _live_lock:
        _live[proc] = label


def _forget(proc: subprocess.Popen) -> None:
    with _live_lock:
        _live.pop(proc, None)


def stop_process(proc: subprocess.Popen, grace: float = 5.0) -> None:
    """End one model call, and anything it started.

    Signals the process group rather than the process: these CLIs run children
    of their own, and terminating only the parent would leave those behind —
    exactly the problem this exists to solve.
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


def cancel(label: str, grace: float = 5.0) -> int:
    """Stop the calls running under one label. Returns how many there were.

    For "stop reviewing this one" from the dashboard, as against the quit path's
    "stop everything". The label is recorded before the process is signalled so
    that the ``ModelError`` the call is about to raise can be phrased as a
    cancellation rather than as the crash it would otherwise look like.
    """
    with _live_lock:
        matching = [proc for proc, name in _live.items() if name == label]
        if matching:
            _cancelled_labels.add(label)
    for proc in matching:
        stop_process(proc, grace)
    return len(matching)


def was_cancelled(label: str) -> bool:
    """Whether ``label`` was cancelled by hand, clearing the mark as it reads.

    Cleared on read because the label is a pull request, and the next tick will
    happily use it again for a call nobody asked to stop.
    """
    with _live_lock:
        if label in _cancelled_labels:
            _cancelled_labels.discard(label)
            return True
        return False


def live_count() -> int:
    """Model calls running right now, across every thread.

    Covers reviews, thread replies and merge summaries alike — anything that
    reached a CLI — so a quit confirmation can say what it is about to end
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
    provider: str = ""
    model: str = ""


@dataclass
class Progress:
    """What a call can say about itself while it is still running.

    Handed to ``on_progress`` every few seconds so the caller can record a
    heartbeat. ``elapsed`` is wall-clock because it is for a person to read;
    ``silent_for`` is awake-only because it is the wedge signal. See ``clock``.
    """

    #: Wall-clock seconds since the call started, sleep included.
    elapsed: float
    #: How many of those the machine spent asleep.
    slept: float
    #: Awake seconds since the child last printed anything.
    silent_for: float
    #: Whatever the provider could say about where it has got to, or "".
    note: str = ""


#: How often a running call reports in. Short enough that the dashboard's
#: spinner is backed by something real, long enough to be free.
BEAT_SECONDS = 5.0


def _pump(
    proc: subprocess.Popen,
    *,
    stdin_text: str,
    deadline: clock.Deadline,
    adapter: providers.Adapter,
    on_progress: Any = None,
) -> tuple[str, str]:
    """Feed the child its prompt and collect its output, reporting as it goes.

    Three threads rather than ``communicate()``. One writes, because a review
    bundle is tens of kilobytes — larger than a pipe buffer — and writing it
    inline would deadlock against a child that has not started reading yet. One
    each for stdout and stderr, so that *a line arriving* is an event this can
    see rather than something discovered at EOF.

    That last part is the whole reason ``communicate()`` is not good enough here.
    It reads to EOF, so for the length of a call it can report exactly nothing,
    and "nothing" is precisely the state that needs telling apart from a hang.
    A provider that streams (see ``Adapter.progress``) turns that into a running
    account of where the review has got to.
    """
    out: list[str] = []
    err: list[str] = []
    silence = clock.Silence()
    note = ""

    def write() -> None:
        try:
            if proc.stdin is not None:
                proc.stdin.write(stdin_text)
                proc.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            # The child died before reading its prompt. Its exit code is the
            # better error, and it is already on its way to the caller.
            pass

    def drain(stream: Any, sink: list[str], watch: bool) -> None:
        nonlocal note
        try:
            for line in stream:
                sink.append(line)
                silence.beat()
                if not watch:
                    continue
                try:
                    found = adapter.progress(line)
                except Exception:  # noqa: BLE001 - a progress note is never worth a review
                    found = None
                if found:
                    note = found
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except (OSError, ValueError):
                pass

    workers = [
        threading.Thread(target=write, name="model-stdin", daemon=True),
        threading.Thread(
            target=drain, args=(proc.stdout, out, True), name="model-stdout", daemon=True
        ),
        threading.Thread(
            target=drain, args=(proc.stderr, err, False), name="model-stderr", daemon=True
        ),
    ]
    for worker in workers:
        worker.start()

    while True:
        try:
            proc.wait(timeout=BEAT_SECONDS)
            break
        except subprocess.TimeoutExpired:
            pass

        if on_progress is not None:
            try:
                on_progress(
                    Progress(
                        elapsed=deadline.elapsed(),
                        slept=deadline.slept(),
                        silent_for=silence.seconds(),
                        note=note,
                    )
                )
            except Exception:  # noqa: BLE001 - a heartbeat must not kill a review
                log.get().debug("heartbeat failed", exc_info=True)

        if deadline.expired():
            stop_process(proc)
            for worker in workers:
                worker.join(timeout=5)
            raise ModelError(
                f"model call gave up after {deadline.awake_elapsed():.0f}s awake "
                f"({deadline.elapsed():.0f}s elapsed, "
                f"{deadline.slept():.0f}s of it asleep)"
            )

    # The process is gone; its pipes are at EOF, so these finish promptly. The
    # timeout is a backstop against a grandchild holding the write end open.
    for worker in workers:
        worker.join(timeout=10)
    return "".join(out), "".join(err)


def _int(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def _float(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


@dataclass
class Spend:
    """What one review cost, totalled over however many calls it took.

    A review is one call most of the time and two when the axes are split, so
    the interesting number is the total rather than any single call's. Kept
    separate from ``usage`` because that is whatever a provider chose to report,
    in whatever shape it chose to report it, and this has to survive being
    written to a column.

    Fresh and cached input are counted apart on purpose. Adding them would read
    as one number you could act on, when the two are billed nothing like the
    same and only the fresh half responds to trimming a prompt.
    """

    calls: int = 0
    seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    provider: str = ""
    model: str = ""

    def add(self, result: ModelResult) -> None:
        usage = result.usage
        self.calls += 1
        self.seconds += result.duration_seconds
        self.input_tokens += _int(usage.get("input_tokens")) + _int(
            usage.get("cache_creation_input_tokens")
        )
        self.output_tokens += _int(usage.get("output_tokens"))
        self.cached_tokens += _int(usage.get("cache_read_input_tokens"))
        self.cost_usd += _float(usage.get("total_cost_usd"))
        # First one wins: a split review runs both halves on the same model, and
        # a blank from a provider that reports nothing should not erase a name
        # an earlier call did report.
        self.provider = self.provider or result.provider
        self.model = self.model or result.model

    @property
    def measured(self) -> bool:
        """Whether there is anything here worth showing.

        Not every provider reports usage. A row that only knows how long it took
        is still worth having; one that knows nothing at all is not.
        """
        return bool(self.calls and (self.seconds or self.output_tokens))


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
        raise ModelError("model returned nothing")

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

    raise ModelError(f"could not parse JSON from model output: {text[:300]}")


def run(
    cfg: dict[str, Any],
    *,
    system_prompt: str,
    user_prompt: str,
    add_dir: Path | None = None,
    label: str = "",
    on_progress: Any = None,
) -> ModelResult:
    """One model call. Returns the parsed JSON payload plus usage figures.

    ``cfg`` is a resolved provider block — see ``config.resolve_provider``.

    ``label`` names the call — ``owner/repo#123`` — so the dashboard can stop
    this one without stopping the rest. ``on_progress`` is handed a ``Progress``
    every few seconds while the call runs; it must not raise, and is wrapped so
    that it cannot take a review down with it if it does.
    """
    try:
        adapter = providers.get(str(cfg.get("type") or ""))
    except providers.ProviderError as exc:
        raise ModelError(str(exc)) from exc

    # A budget of awake seconds, not of elapsed ones: a call that spans a
    # suspend was frozen for it rather than failing to make progress, and
    # charging it for the lid being shut would kill work that was going to
    # succeed. See ``clock``.
    deadline = clock.Deadline(float(cfg.get("timeout_seconds") or 900))

    with tempfile.TemporaryDirectory(prefix="blinky-cwd-") as scratch:
        scratch_dir = Path(scratch)
        call = adapter.prepare(
            cfg,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            add_dir=add_dir,
            scratch=scratch_dir,
        )

        try:
            # Popen rather than run(), so the handle can be registered and the
            # call stopped when the tool is asked to quit. Its own session, so
            # terminating it takes the whole tree with it — these CLIs spawn
            # children of their own — and so that a Ctrl-C in the terminal is
            # something this code decides about rather than something the shell
            # delivers behind its back.
            proc = subprocess.Popen(
                call.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=scratch,
                env=_child_env(),
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise ModelError(
                f"{call.command[0]!r} is not on PATH. {adapter.install_hint} "
                f"Or point providers.{adapter.name}.command in config/global.json "
                "at it."
            ) from exc

        _register(proc, label)
        try:
            stdout, stderr = _pump(
                proc,
                stdin_text=call.stdin,
                deadline=deadline,
                adapter=adapter,
                on_progress=on_progress,
            )
        finally:
            _forget(proc)
        duration = deadline.awake_elapsed()

        # Drained here rather than inside the failure branch below, and whatever
        # the outcome. A call cancelled a moment before it would have exited on
        # its own reaches this line with a zero status, and a mark left behind
        # by that would be read by the *next* call on the same pull request —
        # reporting some unrelated failure as something the user asked for.
        cancelled_by_hand = bool(label) and was_cancelled(label)

        if proc.returncode != 0:
            if _cancelled.is_set():
                # Not a failure. We stopped it on the way out, and saying
                # "codex exited -15" would read as a crash.
                raise ModelError("model call stopped — shutting down")
            if cancelled_by_hand:
                raise ModelError("model call stopped — cancelled")
            detail = (stderr or stdout or "").strip()[:600]
            raise ModelError(f"{adapter.name} exited {proc.returncode}: {detail}")

        # Inside the scratch dir still: some providers write their answer to a
        # file in it, which is gone as soon as this block ends.
        try:
            reply = adapter.read(call, stdout or "")
        except providers.ProviderError as exc:
            raise ModelError(str(exc)) from exc

    payload = extract_json(reply.text)

    # Worth a line at INFO rather than DEBUG: this is the explanation for a
    # review that appeared to take far longer than its timeout allows, which is
    # otherwise one of the more alarming things this tool can print.
    slept = deadline.slept()
    if slept >= 60:
        log.get().info(
            "%s call ran across %.0fm of system sleep — %.0fs awake, %.0fs elapsed",
            adapter.name,
            slept / 60,
            duration,
            deadline.elapsed(),
        )

    log.get().debug(
        "%s call finished in %.1fs (%s)",
        adapter.name,
        duration,
        ", ".join(f"{k}={v}" for k, v in reply.usage.items()) or "no usage reported",
    )
    return ModelResult(
        payload=payload,
        raw=reply.text,
        usage=reply.usage,
        duration_seconds=duration,
        provider=adapter.name,
        model=str(cfg.get("model") or ""),
    )
