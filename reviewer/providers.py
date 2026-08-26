"""One adapter per coding-agent CLI.

Every provider here is a *terminal agent* — a command you have already installed
and signed in, which reads a prompt, is allowed to read files, and prints an
answer. That is the shape the reviewer wants, and it is why there is no HTTP
client anywhere in this file: a review runs against your existing subscription,
and the tool never holds an API key.

An adapter is two small jobs:

``prepare``
    Turn the reviewer's ``(system prompt, user prompt, directory to read)`` into
    an actual command line, in whatever dialect that CLI speaks.
``read``
    Turn whatever the CLI printed back into ``(text, usage)``.

Everything else — starting the process, killing it on quit, pulling the JSON
object out of the reply — is the same for all of them and lives in ``model.py``.

Two rules every adapter follows, because the security properties in the README
depend on them rather than on any one vendor's flags:

*The prompt goes in on stdin.* A review bundle is tens of kilobytes and would
run into ``ARG_MAX`` as an argument long before it ran into anything else.

*The working directory is a scratch dir, never the checkout.* All of these CLIs
auto-load instructions from the directory they start in — ``CLAUDE.md``,
``AGENTS.md``, ``GEMINI.md``. Starting one inside the tree it is reviewing would
let a pull request write instructions to its own reviewer. The checkout is named
in the prompt and reached by absolute path instead.

Where a CLI has no way to accept a system prompt separately, this folds it into
the top of the user message. It is the same text either way; only the envelope
differs.

CLI flags drift between releases faster than this file can. Anything version
specific belongs in a provider's ``extra_args``, which is appended verbatim to
every invocation — that is the escape hatch, and reaching for it is expected.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Tools no reviewer has any business holding. Enforced where the CLI has a flag
# for it; where it does not, the adapter says so in ``caveat``.
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

DEFAULT_TOOLS = ["Read", "Glob", "Grep"]


@dataclass(frozen=True)
class Call:
    """One invocation, ready to run."""

    command: list[str]
    stdin: str
    #: Set when the CLI writes its final answer to a file rather than to stdout.
    last_message_file: Path | None = None


@dataclass(frozen=True)
class Reply:
    """What came back, once the CLI's own envelope is off."""

    text: str
    usage: dict[str, Any] = field(default_factory=dict)


class ProviderError(RuntimeError):
    """The CLI ran, but said something the adapter cannot use."""


def fold_system(system_prompt: str, user_prompt: str) -> str:
    """Put the system prompt at the top of the user message.

    For CLIs with no equivalent of ``--append-system-prompt``. Tagged rather
    than merely concatenated so the boundary is legible to the model: the
    instructions are ours, everything after them is the material under review.
    """
    return (
        "<reviewer_instructions>\n"
        f"{system_prompt.strip()}\n"
        "</reviewer_instructions>\n\n"
        f"{user_prompt}"
    )


def _tokens(blob: Any, keys: tuple[str, ...]) -> int | None:
    """Find a token count in a nested reply, without knowing its shape.

    Every CLI reports usage differently and each of them has changed the layout
    at least once. A missing count costs one less number in a log line, so this
    searches rather than insisting on a path.
    """
    if isinstance(blob, dict):
        for key in keys:
            value = blob.get(key)
            if isinstance(value, (int, float)):
                return int(value)
        for value in blob.values():
            found = _tokens(value, keys)
            if found is not None:
                return found
    elif isinstance(blob, list):
        for value in blob:
            found = _tokens(value, keys)
            if found is not None:
                return found
    return None


class Adapter:
    """What every provider has to be able to do."""

    #: Value of ``type`` in the config, and the default name of a provider entry.
    name = ""
    #: What to run if ``command`` is not set.
    default_command = ""
    #: Shown by ``--check`` when the command is not on PATH.
    install_hint = ""
    #: A true thing about this provider that the others do not share. Printed by
    #: ``--check`` so nobody has to read this file to find it out.
    caveat = ""

    def version_args(self) -> list[str]:
        return ["--version"]

    def prepare(
        self,
        cfg: dict[str, Any],
        *,
        system_prompt: str,
        user_prompt: str,
        add_dir: Path | None,
        scratch: Path,
    ) -> Call:
        raise NotImplementedError

    def read(self, call: Call, stdout: str) -> Reply:
        raise NotImplementedError

    def progress(self, line: str) -> str | None:
        """A short note about where the call has got to, from one output line.

        Called for every line the CLI prints, as it prints it. Returning a
        string replaces the note the dashboard shows beside the spinner;
        returning ``None`` leaves the previous one standing, which is what a
        line that carries no news should do.

        The default is ``None`` for every line: a CLI that prints its answer in
        one go at the end has nothing to report until it is finished, and
        pretending otherwise would be worse than an honest spinner. Providers
        that stream override this.

        Must be cheap — it runs on the read thread, once per line — and must not
        raise. ``model`` guards it anyway, on the principle that no progress
        note is worth losing a review over.
        """
        return None

    # Shared bits.

    def command_name(self, cfg: dict[str, Any]) -> str:
        return str(cfg.get("command") or self.default_command)

    def tools(self, cfg: dict[str, Any]) -> list[str]:
        """The tools this call may use.

        An explicit empty list means "none" — what a merge summary wants, since
        it has nothing to look at. Only an absent or null setting falls back to
        the read-only default.
        """
        allowed = cfg.get("allowed_tools")
        if allowed is None:
            return list(DEFAULT_TOOLS)
        return [str(tool) for tool in allowed]

    def extra(self, cfg: dict[str, Any]) -> list[str]:
        return [str(arg) for arg in (cfg.get("extra_args") or [])]


#: What the dashboard calls each tool while the model is using it. Anything not
#: listed falls back to the tool's own name, so a new one degrades to a slightly
#: uglier note rather than to nothing.
CLAUDE_TOOL_VERBS = {
    "Read": "reading",
    "Grep": "searching",
    "Glob": "looking for",
    "ReadManyFiles": "reading",
}


def _json_line(line: str) -> dict[str, Any] | None:
    """One line as a JSON object, or None if it is not one."""
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def _is_result(event: dict[str, Any]) -> bool:
    kind = event.get("type")
    # The second half is the single-object ``--output-format json`` envelope,
    # which carries the same fields under no type at all on older releases.
    return kind == "result" or (kind is None and "result" in event)


def _final_result(stdout: str) -> dict[str, Any] | None:
    """The stream's closing ``result`` event, looked for from the end.

    Backwards, and stopped at the first hit, because of what the rest of the
    stream contains: under stream-json every file the model read comes back as
    a tool result with its contents inline, so a review of a large pull request
    runs to megabytes. Parsing all of that to reach an event which is by
    definition the last one would hold the whole review in memory twice over,
    for nothing. The common case ends after one line.
    """
    for line in reversed(stdout.splitlines()):
        event = _json_line(line)
        if event is not None and _is_result(event):
            return event
    return None


def _content_blocks(event: dict[str, Any]) -> list[dict[str, Any]]:
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def _assistant_text(event: dict[str, Any]) -> str:
    parts = [
        str(block.get("text") or "")
        for block in _content_blocks(event)
        if block.get("type") == "text"
    ]
    return "".join(parts).strip()


def _last_assistant_text(stdout: str) -> str:
    """The final thing the model said in its own voice, searched from the end.

    Only ever wanted when the closing event is missing, which means the call was
    cut short. Backwards for the same reason as ``_final_result``, and because
    the most recent turn is the one worth keeping.
    """
    for line in reversed(stdout.splitlines()):
        event = _json_line(line)
        if event is None or event.get("type") != "assistant":
            continue
        text = _assistant_text(event)
        if text:
            return text
    return ""


def _error_detail(event: dict[str, Any]) -> str:
    """The readable half of a failure event.

    The whole event is a few hundred characters of session ids and token counts,
    and printing it verbatim buries the one sentence that says what went wrong
    under everything that did not.
    """
    for key in ("error", "result", "subtype"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:400]
    return str(event)[:400]


class ClaudeAdapter(Adapter):
    """Claude Code.

    The tightest of the three, and the reason the defaults look the way they do:
    it is the only one here that can be handed an explicit tool allowlist *and*
    an explicit denylist, so "may read files, may not run anything" is a fact
    about the process rather than a hope about the model.

    Run with ``--output-format stream-json``, which prints one JSON event per
    line as the review happens rather than one object at the end. Two things
    come of that. The dashboard can say what the model is doing right now
    instead of spinning at it, and — the reason it is worth the extra parsing —
    a call that has printed nothing for ten minutes becomes distinguishable from
    one that is simply taking ten minutes. Total elapsed time cannot tell those
    apart; silence can.
    """

    name = "claude"
    default_command = "claude"
    install_hint = (
        "Install the Claude Code CLI (https://claude.com/claude-code) and sign in."
    )

    def prepare(
        self,
        cfg: dict[str, Any],
        *,
        system_prompt: str,
        user_prompt: str,
        add_dir: Path | None,
        scratch: Path,
    ) -> Call:
        command = [
            self.command_name(cfg),
            "-p",
            "--output-format",
            "stream-json",
            # Required alongside stream-json under --print, and the reason this
            # is not simply the old flag with a new value.
            "--verbose",
            "--append-system-prompt",
            system_prompt,
        ]
        tools = self.tools(cfg)
        if tools:
            command += ["--allowedTools", ",".join(tools)]
        command += ["--disallowedTools", ",".join(BLOCKED_TOOLS)]
        if cfg.get("model"):
            command += ["--model", str(cfg["model"])]
        if add_dir is not None:
            command += ["--add-dir", str(add_dir)]
        command += self.extra(cfg)
        return Call(command=command, stdin=user_prompt)

    def progress(self, line: str) -> str | None:
        event = _json_line(line)
        if event is None:
            return None
        kind = event.get("type")

        if kind == "system" and event.get("subtype") == "init":
            return "starting up"
        if kind != "assistant":
            # Tool results and the final result event say nothing a person
            # watching a spinner wants to read.
            return None

        # Last block wins: by the time a turn is printed the interesting part is
        # what it ended up doing, not what it said on the way there.
        for block in reversed(_content_blocks(event)):
            if block.get("type") == "tool_use":
                return self._tool_note(block)
            if block.get("type") == "text" and str(block.get("text") or "").strip():
                return "writing the review"
        return None

    @staticmethod
    def _tool_note(block: dict[str, Any]) -> str:
        name = str(block.get("name") or "working")
        verb = CLAUDE_TOOL_VERBS.get(name, name.lower())
        args = block.get("input")
        args = args if isinstance(args, dict) else {}
        target = ""
        for key in ("file_path", "pattern", "path", "query"):
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                target = value.strip()
                break
        if not target:
            return verb
        # The basename, because this lands in a fixed-width line beside a
        # spinner and a full repository path would push everything else off it.
        return f"{verb} {target.rsplit('/', 1)[-1]}"[:60]

    def read(self, call: Call, stdout: str) -> Reply:
        # stream-json ends with a "result" event carrying the answer and the
        # token counts. ``--output-format json`` — an older CLI, or anyone who
        # has put it back in extra_args — prints that same object on its own,
        # and older versions still print bare text. All three land here, because
        # these flags drift between releases faster than this file can.
        result = _final_result(stdout)

        if result is None:
            # No closing event: the call was cut short, or this is not a stream
            # at all but a pretty-printed object. Only here — the path that has
            # already failed — is it worth reading the whole thing to salvage
            # the last thing the model actually said.
            fallback = _last_assistant_text(stdout)
            try:
                parsed = json.loads(stdout)
            except json.JSONDecodeError:
                return Reply(text=fallback or stdout)
            if not isinstance(parsed, dict):
                return Reply(text=fallback or stdout)
            result = parsed

        if result.get("is_error"):
            raise ProviderError(f"claude reported an error: {_error_detail(result)}")

        usage = dict(result.get("usage") or {})
        for key in ("total_cost_usd", "num_turns", "duration_ms"):
            if key in result:
                usage[key] = result[key]

        body = result["result"] if isinstance(result.get("result"), str) else ""
        # Never fall back to raw stdout here the way the other adapters can:
        # under stream-json that is a wall of JSONL, and handing it to the JSON
        # extractor would turn a recoverable hiccup into a parse failure. The
        # last thing the model actually said is a far better guess.
        if not body.strip():
            body = _last_assistant_text(stdout) or stdout
        return Reply(text=body, usage=usage)


class CodexAdapter(Adapter):
    """OpenAI's Codex CLI, via ``codex exec``.

    Two differences from Claude Code worth knowing before you switch:

    It has no ``--append-system-prompt``, so the personality is folded into the
    top of the user message instead.

    Its read-only sandbox is read-only about *writing*, not about reading: it
    permits shell commands and grants read access to the whole filesystem, not
    just the checkout. So a review here can see more of your machine than the
    same review under Claude Code, where the tool allowlist is the boundary. The
    sandbox still cannot write, install, or reach the network, and the GitHub
    token is stripped from its environment like everywhere else — but if the
    scoping in the README is why you run this tool, that is the line that moves.
    """

    name = "codex"
    default_command = "codex"
    install_hint = "Install the Codex CLI (`npm i -g @openai/codex`) and run `codex login`."
    caveat = (
        "codex reads with a filesystem sandbox rather than a tool allowlist: it "
        "can read outside the checkout and can run read-only shell commands."
    )

    def prepare(
        self,
        cfg: dict[str, Any],
        *,
        system_prompt: str,
        user_prompt: str,
        add_dir: Path | None,
        scratch: Path,
    ) -> Call:
        last_message = scratch / "last-message.txt"
        command = [
            self.command_name(cfg),
            "exec",
            # The working directory is a scratch dir, so there is no repository
            # here and codex would otherwise refuse to start.
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--json",
            "--output-last-message",
            str(last_message),
        ]
        if cfg.get("model"):
            command += ["--model", str(cfg["model"])]
        command += self.extra(cfg)
        # ``-`` means "the prompt is on stdin".
        command += ["-"]
        return Call(
            command=command,
            stdin=fold_system(system_prompt, user_prompt),
            last_message_file=last_message,
        )

    def read(self, call: Call, stdout: str) -> Reply:
        usage: dict[str, Any] = {}
        last_agent_message = ""

        # ``--json`` prints one JSON event per line. The final answer is in the
        # file, but the token counts are only here.
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            # The event type has lived at both the top level and under "msg"
            # across codex releases, so look in both and assume neither.
            nested = event.get("msg")
            kind = str(
                event.get("type")
                or (nested.get("type") if isinstance(nested, dict) else "")
                or ""
            )
            if "token" in kind or "usage" in kind:
                out = _tokens(event, ("output_tokens", "completion_tokens"))
                cached = _tokens(event, ("cached_input_tokens", "cache_read_input_tokens"))
                if out is not None:
                    usage["output_tokens"] = out
                if cached is not None:
                    usage["cache_read_input_tokens"] = cached
            if "agent_message" in kind:
                source = nested if isinstance(nested, dict) else event
                text = source.get("message") or source.get("text") or ""
                if isinstance(text, str) and text.strip():
                    last_agent_message = text

        body = ""
        path = call.last_message_file
        if path is not None:
            try:
                body = path.read_text(encoding="utf-8")
            except OSError:
                body = ""

        # Fall back through the event stream, then through raw stdout, rather
        # than throwing away a review because one output channel was empty.
        if not body.strip():
            body = last_agent_message
        if not body.strip():
            body = stdout

        return Reply(text=body, usage=usage)


class GeminiAdapter(Adapter):
    """Google's Gemini CLI.

    Tool names differ from Claude Code's, so the configured allowlist is
    translated rather than passed through — a config that says ``Read`` means
    the same thing whichever provider ends up serving it. Names this does not
    recognise are passed along untouched, so a Gemini-specific allowlist still
    works if you would rather write one.

    ``--include-directories`` grants write access as well as read. Nothing here
    turns on ``--approval-mode yolo``, so a write still needs a confirmation
    that a non-interactive run cannot give and therefore fails — but that is one
    flag in ``extra_args`` away from not being true, which is a good reason not
    to put it there.
    """

    name = "gemini"
    default_command = "gemini"
    install_hint = "Install the Gemini CLI (`npm i -g @google/gemini-cli`) and sign in."
    caveat = (
        "gemini blocks writes by withholding approval rather than by dropping "
        "the tools; do not add --approval-mode yolo to extra_args."
    )

    TOOL_NAMES = {
        "Read": "read_file",
        "Glob": "glob",
        "Grep": "search_file_content",
        "ReadManyFiles": "read_many_files",
    }

    def prepare(
        self,
        cfg: dict[str, Any],
        *,
        system_prompt: str,
        user_prompt: str,
        add_dir: Path | None,
        scratch: Path,
    ) -> Call:
        command = [
            self.command_name(cfg),
            "--output-format",
            "json",
            "--approval-mode",
            "default",
        ]
        if cfg.get("model"):
            command += ["--model", str(cfg["model"])]

        tools = [self.TOOL_NAMES.get(tool, tool) for tool in self.tools(cfg)]
        if tools:
            command += ["--allowed-tools", ",".join(tools)]
        if add_dir is not None:
            command += ["--include-directories", str(add_dir)]
        command += self.extra(cfg)
        return Call(command=command, stdin=fold_system(system_prompt, user_prompt))

    def read(self, call: Call, stdout: str) -> Reply:
        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError:
            return Reply(text=stdout)

        if not isinstance(envelope, dict):
            return Reply(text=stdout)

        error = envelope.get("error")
        if error:
            message = error.get("message") if isinstance(error, dict) else error
            raise ProviderError(f"gemini reported an error: {str(message)[:400]}")

        usage: dict[str, Any] = {}
        stats = envelope.get("stats")
        out = _tokens(stats, ("candidates", "output_tokens", "candidatesTokenCount"))
        cached = _tokens(stats, ("cached", "cachedContentTokenCount"))
        if out is not None:
            usage["output_tokens"] = out
        if cached is not None:
            usage["cache_read_input_tokens"] = cached

        body = envelope["response"] if isinstance(envelope.get("response"), str) else stdout
        return Reply(text=body, usage=usage)


class CommandAdapter(Adapter):
    """Any other CLI that reads a prompt on stdin and prints an answer.

    The escape hatch, and the reason a fourth provider does not need a fourth
    class. It assumes nothing: the system prompt is folded into the message, the
    whole command line past the executable comes from ``extra_args``, and the
    answer is read straight from stdout with no envelope to unwrap.

    Nothing here can restrict what that command is allowed to do — this is a
    ``type`` that trusts the command you named. Point it at something read-only.
    """

    name = "command"
    default_command = ""
    install_hint = "Set the provider's `command` to an executable on your PATH."
    caveat = (
        "type 'command' cannot sandbox anything: whatever the command allows "
        "itself, the review gets."
    )

    def prepare(
        self,
        cfg: dict[str, Any],
        *,
        system_prompt: str,
        user_prompt: str,
        add_dir: Path | None,
        scratch: Path,
    ) -> Call:
        command = [self.command_name(cfg)]
        if cfg.get("model"):
            command += ["--model", str(cfg["model"])]
        command += self.extra(cfg)
        return Call(command=command, stdin=fold_system(system_prompt, user_prompt))

    def read(self, call: Call, stdout: str) -> Reply:
        return Reply(text=stdout)


ADAPTERS: dict[str, Adapter] = {
    adapter.name: adapter
    for adapter in (ClaudeAdapter(), CodexAdapter(), GeminiAdapter(), CommandAdapter())
}

TYPES = sorted(ADAPTERS)


def get(type_name: str) -> Adapter:
    """The adapter for a provider ``type``."""
    try:
        return ADAPTERS[type_name]
    except KeyError:
        raise ProviderError(
            f"unknown provider type {type_name!r}; known types are "
            f"{', '.join(TYPES)}"
        ) from None
